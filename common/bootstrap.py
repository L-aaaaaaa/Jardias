"""bootstrap — 会话初始化引导。"""
import json
import os
import sys
from datetime import datetime

from character import get_history_path, ensure_dirs
from character.config_io import init_config, load_config, save_config
from character.history import History
from common.actor_log import bootstrap_summary
from common.logger import logger
from data_shape import ActorConfig
from tool.builtin import set_actor, tools
from yinao import resolve_ipu
from yinao.launcher import resolve_chat, sync_config_to_ipu, set_active_ipu, next_provider, pick_fallback_ipu
from yinao.launcher.reply_getter import get_ipu_reply, form_client
from yinao.launcher.ipu_config_manager import ipu_config_manager

def _default_system_prompt() -> str:
    return (
        "You are an AI assistant with file operations, code execution and self-config capabilities. "
        "Use tools when needed and adjust your runtime config freely."
    )


def bootstrap(provider: str, ipu: str, character_name: str = "default"):
    from dataclasses import dataclass

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 检查角色是否存在
    from character.registry import registry
    if not registry.exists(character_name) and character_name != "default":
        print(f"[Error] 角色 '{character_name}' 不存在。可用角色: {', '.join(registry.scan())}")
        sys.exit(1)

    ensure_dirs(character_name)
    history_path = str(get_history_path(character_name))
    config_dir = os.path.join(base_dir, "character", character_name)

    legacy_history = os.path.join(base_dir, "history.json")
    if os.path.exists(legacy_history) and not os.path.exists(history_path):
        import shutil
        shutil.copy2(legacy_history, history_path)
        logger.info(f"  📁 历史迁移 | {legacy_history} → {history_path}")

    set_actor(character_name)

    config_dir = os.path.join(base_dir, "config")
    try:
        config = load_config(character_name, config_dir=config_dir)
    except (FileNotFoundError, Exception):
        config = init_config(character_name, config_dir=config_dir)
        config.identity.system_prompt = _default_system_prompt()
        config.runtime.provider = provider
        config.runtime.ipu = ipu
        save_config(config, character_name, config_dir=config_dir)
    else:
        if not config.identity.system_prompt:
            config.identity.system_prompt = _default_system_prompt()
        if config.runtime.provider:
            provider = config.runtime.provider
        if config.runtime.ipu:
            ipu = config.runtime.ipu

    history = History(history_path).load()

    # 首次对话 — 记录角色诞生时间
    if not config.identity.birth_time:
        if not history.messages:
            config.identity.birth_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_config(config, character_name, config_dir=config_dir)
            logger.info(f"  🐣 角色诞生 | {config.identity.birth_time}")
        elif history.messages[0].get("time"):
            config.identity.birth_time = history.messages[0]["time"]
            save_config(config, character_name, config_dir=config_dir)
            logger.info(f"  🐣 诞生时间恢复 | {config.identity.birth_time}")

    @dataclass
    class AppContext:
        config: ActorConfig
        provider: str
        ipu: str
        chat_fn: object
        ipu_config: object
        history: History
        config_dir: str
        turn_num: int
        character_name: str = ""
        last_config_sig: str = ""

    _prov, ipu_config = resolve_ipu(provider, ipu)

    ctx = AppContext(
        config=config,
        provider=provider,
        ipu=ipu,
        chat_fn=resolve_chat(provider),
        ipu_config=ipu_config,
        history=history,
        config_dir=config_dir,
        turn_num=int(len(history.messages) / 2) + 1,
        character_name=character_name,
    )
    sync_config_to_ipu(ctx.config, ctx.ipu_config)

    from yinao.launcher import set_active_ipu
    set_active_ipu(provider, ipu)

    tool_defs = tools.get_definitions()
    bootstrap_summary(len(ctx.history.messages), ctx.provider, ctx.ipu, len(tool_defs))

    _setup_actor_executor(ctx)
    _setup_scheduler(ctx)
    return ctx


def _setup_actor_executor(ctx):
    """创建并注入 @actor_tool 旁路小模型执行器（支持跨 provider 模型路由）。"""
    from tool.actor_tool import set_actor_executor
    from yinao.launcher.reply_getter import form_client
    from yinao.launcher.ipu_config_manager import ipu_config_manager
    from yinao.launcher import next_provider, pick_fallback_ipu
    from data_shape import IPUProvider

    _provider_clients: dict[str, object] = {}
    _ipu_to_provider: dict[str, str] = {}
    _ipu_to_id: dict[str, str] = {}
    providers_cfg = ipu_config_manager.load()
    for provider_cfg in providers_cfg.providers:
        pname = provider_cfg.name
        api_key = os.environ.get(provider_cfg.api_key_env, "")
        pobj = IPUProvider(
            api_key=api_key,
            base_url=provider_cfg.base_url,
        )
        _provider_clients[pname] = form_client(pobj)
        for ipu_name, ipu_entry in provider_cfg.ipus.items():
            _ipu_to_provider[ipu_name] = pname
            _ipu_to_id[ipu_name] = ipu_entry.get("id", ipu_name)

    _default_client = form_client(ctx.ipu_config)

    async def execute(ipu: str, system_prompt: str, user_message: str, output_schema: dict):
        """调用 IPU 完成摘要/分析（带 provider 自动转移，复用对话级切换逻辑）。"""
        schema_keys = ", ".join(f'"{k}": {v}' for k, v in output_schema.items())
        wrapped_user = (
            f"{user_message}\n\n"
            f"⚠️ 必须只输出纯 JSON，以 [ 或 {{ 开头，不要加 ```json 标签，不要加任何解释文字。\n"
            f"⚠️ JSON 字符串值内部不要使用英文双引号 \"，用中文引号「」代替。\n"
            f"JSON 格式: {{{schema_keys}}}"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": wrapped_user},
        ]

        def _extract_json(text: str) -> str:
            text = text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:]) if len(lines) > 1 else text
                if text.rstrip().endswith("```"):
                    text = text[:text.rfind("```")].strip()
            for opener in ("{", "["):  # 找第一个 { 或 [（最外层）
                idx = text.find(opener)
                if idx != -1:
                    text = text[idx:]
                    break
            import re
            text = re.sub(r',\s*([}\]])', r'\1', text)
            return text.strip()

        def _repair_json(text: str) -> str:
            import re

            # 1. 去除 markdown code fence
            text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
            text = re.sub(r'\s*```$', '', text)
            text = text.strip()

            # 2. 修复 JSON 字符串值中的内嵌换行符（\n 未转义）
            # 思路：逐字符扫描，维护是否在字符串内状态，忽略转义符 \"
            in_str = False
            chars = []
            for i, ch in enumerate(text):
                if ch == '"' and (i == 0 or text[i - 1] != '\\'):
                    in_str = not in_str
                    chars.append(ch)
                elif ch == '\n' and in_str:
                    chars.append(' ')  # 换行替换为空格（保守策略）
                else:
                    chars.append(ch)
            text = ''.join(chars)

            # 3. 去除尾随逗号
            text = re.sub(r',\s*\n\s*([}\]])', r'\n\1', text)
            text = re.sub(r',\s*([}\]])$', r'\1', text, flags=re.MULTILINE)
            text = re.sub(r',(\s*[}\]])', r'\1', text)

            # 4. 平衡括号
            brackets = {"[": "]", "{": "}"}
            stack = []
            balanced = []
            for ch in text:
                if ch in '[{':
                    stack.append(ch)
                elif ch in ']}':
                    if stack and brackets.get(stack[-1]) == ch:
                        stack.pop()
                balanced.append(ch)
            while stack:
                balanced.append(brackets[stack.pop()])
            return "".join(balanced)

        primary_provider = _ipu_to_provider.get(ipu)
        fallback_chain = []
        if primary_provider and primary_provider in _provider_clients:
            fallback_chain.append((ipu, primary_provider,
                                   _provider_clients[primary_provider],
                                   _ipu_to_id.get(ipu, ipu)))

        tried_providers = {primary_provider} if primary_provider else set()
        for _ in range(3):
            next_p = next_provider("", tried_providers)
            if not next_p:
                break
            tried_providers.add(next_p)
            fb_ipu = pick_fallback_ipu(next_p)
            fallback_chain.append((fb_ipu, next_p,
                                   _provider_clients[next_p],
                                   _ipu_to_id.get(fb_ipu, fb_ipu)))

        last_error = None
        for fb_ipu, fb_provider, fb_client, fb_api_ipu in fallback_chain:
            try:
                raw_text = get_ipu_reply(fb_client, fb_api_ipu, messages,
                    temperature=0.0, max_icp=4096)
                text = _extract_json(raw_text)
                try:
                    result = json.loads(text)
                except json.JSONDecodeError as je:
                    repaired = _repair_json(text)
                    try:
                        result = json.loads(repaired)
                    except json.JSONDecodeError:
                        logger.warning(f"  [LLM-TOOL] {fb_ipu} raw(-100 chars): {repr(raw_text[-100:])}")
                        raise je
                if isinstance(result, list):
                    array_key = next((k for k, v in output_schema.items() if "array" in v), None)
                    if array_key:
                        result = {array_key: result}
                if fb_ipu != ipu:
                    logger.info(f"  [LLM-TOOL] fallback success | {ipu}->{fb_ipu} | provider={fb_provider}")
                return result
            except Exception as e:
                last_error = e
                from yinao.weaver import is_exhausted_error, record_ipu_failure
                try:
                    if is_exhausted_error(e):
                        record_ipu_failure(fb_provider, e)
                except Exception:
                    pass
                logger.warning(f"  [LLM-TOOL] {fb_ipu} failed ({type(e).__name__}), trying next...")
                continue

        raise RuntimeError(
            f"LLM tool '{ipu}' failed after {len(fallback_chain)} attempt(s). "
            f"Last error: {type(last_error).__name__}: {last_error}"
        )

    set_actor_executor(execute)
    logger.info(f"  [LLM-TOOL] executor ready | provider={ctx.provider} | base={ctx.ipu_config.base_url}")


def _setup_scheduler(ctx):
    """创建 TemporalScheduler 并注入 on_job_fire 回调。"""
    import asyncio
    import os
    from schedule import TemporalScheduler
    from tool.builtin import set_scheduler as _set_tool_scheduler

    store_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "schedule")
    os.makedirs(store_dir, exist_ok=True)
    store_path = os.path.join(store_dir, "schedule_data.json")

    async def on_job_fire(fire_ctx):
        """时策触发回调：写入 trigger 到 ctx.history（复用对话循环的 History 实例，避免竞写）。"""
        trigger_msg = fire_ctx.format_trigger()
        # 复用对话循环的 History 实例，直接追加到内存中（不 load，避免竞写覆盖）
        ctx.history.append_trigger(trigger_msg)
        ctx.history.save()
        logger.info(f"[时策] 触发完成 | {fire_ctx.character_id} | {trigger_msg[:50]}...")

    scheduler = TemporalScheduler(store_path, on_job_fire=on_job_fire)
    _set_tool_scheduler(scheduler)
    asyncio.ensure_future(scheduler.start())
    logger.info(f"  [时策] 调度器已启动 | 存储: {store_path}")
