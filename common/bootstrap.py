"""bootstrap — 会话初始化引导。"""
import json
import os
import sys
from datetime import datetime

from common.logger import logger
from agent_config import (
    AgentConfig,
    init_config,
    load_config,
    save_config,
    resolve_model,
    get_model_capabilities,
    MODEL_NAMES,
)
from model_client.switch import resolve_chat, sync_config_to_model
from model_client.common_client_util import form_client, single_completion
from tool.builtin import set_agent, tools
from character import get_history_path, ensure_dirs
from character.history import History
from common.agent_log import bootstrap_summary


def _default_system_prompt() -> str:
    return (
        "You are an AI assistant with file operations, code execution and self-config capabilities. "
        "Use tools when needed and adjust your runtime config freely."
    )


def bootstrap(provider: str, model: str, character_name: str = "default"):
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

    set_agent(character_name)

    config_dir = os.path.join(base_dir, "config")
    try:
        config = load_config(character_name, config_dir=config_dir)
    except (FileNotFoundError, Exception):
        config = init_config(character_name, config_dir=config_dir)
        config.identity.system_prompt = _default_system_prompt()
        config.runtime.provider = provider
        config.runtime.model = model
        save_config(config, character_name, config_dir=config_dir)
    else:
        if not config.identity.system_prompt:
            config.identity.system_prompt = _default_system_prompt()
        # 使用角色自己的 provider/model，CLI 参数仅新建时作 fallback
        if config.runtime.provider:
            provider = config.runtime.provider
        if config.runtime.model:
            model = config.runtime.model

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
        config: AgentConfig
        provider: str
        model: str
        chat_fn: object
        model_config: object
        history: History
        config_dir: str
        turn_num: int
        character_name: str = ""
        last_config_sig: str = ""

    _prov, model_config = resolve_model(provider, model)

    ctx = AppContext(
        config=config,
        provider=provider,
        model=model,
        chat_fn=resolve_chat(provider),
        model_config=model_config,
        history=history,
        config_dir=config_dir,
        turn_num=int(len(history.messages) / 2) + 1,
        character_name=character_name,
    )
    sync_config_to_model(ctx.config, ctx.model_config)

    from model_client.model_context import set_actual_model
    set_actual_model(provider, model)

    tool_defs = tools.get_definitions()
    bootstrap_summary(len(ctx.history.messages), ctx.provider, ctx.model, len(tool_defs))

    _setup_llm_executor(ctx)
    return ctx


def _setup_llm_executor(ctx):
    """创建并注入 @llm_tool 旁路小模型执行器（支持跨 provider 模型路由）。"""
    from tool.llm_tool import set_llm_executor
    from model_client.common_client_util import form_client
    from model_client.model_context import MODEL_NAMES
    from agent_config.provider_manager import provider_manager
    from model_client.switch import _next_provider, _pick_fallback_model
    from data_shape import AIModelProvider

    # 为所有可用 provider 预建 client
    _provider_clients: dict[str, object] = {}
    _model_to_provider: dict[str, str] = {}
    _model_to_id: dict[str, str] = {}  # 模型展示名 → API model ID
    providers_cfg = provider_manager.load()
    for provider_cfg in providers_cfg.providers:
        pname = provider_cfg.name
        api_key = os.environ.get(provider_cfg.api_key_env, "")
        pobj = AIModelProvider(
            api_key=api_key,
            base_url=provider_cfg.base_url,
            model="",
        )
        _provider_clients[pname] = form_client(pobj)
        for model_name, model_entry in provider_cfg.models.items():
            _model_to_provider[model_name] = pname
            _model_to_id[model_name] = model_entry.get("id", model_name)

    # 默认 client（向后兼容：未知 model 走当前角色 provider）
    _default_client = form_client(ctx.model_config)

    async def execute(model: str, system_prompt: str, user_message: str, output_schema: dict):
        """调用 LLM 完成摘要/分析（带 provider 自动转移，复用对话级切换逻辑）。"""
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
            """从 LLM 输出中鲁棒提取 JSON：找首尾括号、去代码块、修常见错误。"""
            text = text.strip()
            # 去掉 markdown 代码块包裹
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:]) if len(lines) > 1 else text
                if text.rstrip().endswith("```"):
                    text = text[:text.rfind("```")].strip()
            # 找到 JSON 起始位置（跳过 LLM 絮絮叨叨的前言）
            for opener in ("[", "{"):
                idx = text.find(opener)
                if idx != -1:
                    text = text[idx:]
                    break
            # 修正常见 LLM JSON 错误
            import re
            text = re.sub(r',\s*([}\]])', r'\1', text)  # 行尾多余逗号
            text = re.sub(r'(?<!\\)"([^"]*?)(?<!\\)"', lambda m: '"' + m.group(1).replace('"', '\\"') + '"', text)  # 未转义的内嵌引号（保守） 实际上这个正则太复杂，跳过
            return text.strip()

        def _repair_json(text: str) -> str:
            """修复常见 LLM JSON 错误后尝试解析。"""
            import re
            # 1. 移除行尾多余逗号
            text = re.sub(r',\s*\n\s*([}\]])', r'\n\1', text)
            text = re.sub(r',\s*([}\]])$', r'\1', text, flags=re.MULTILINE)
            # 2. 移除 JSON 数组/对象末尾的逗号
            text = re.sub(r',(\s*[}\]])', r'\1', text)
            # 3. 检查括号平衡（仅对 {} 和 []）
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
            # 补全未闭合的括号
            while stack:
                balanced.append(brackets[stack.pop()])
            return "".join(balanced)

        # 构建回退链：首选模型 → 跨 provider 自动切换
        primary_provider = _model_to_provider.get(model)
        fallback_chain = []
        if primary_provider and primary_provider in _provider_clients:
            fallback_chain.append((model, primary_provider,
                                   _provider_clients[primary_provider],
                                   _model_to_id.get(model, model)))

        # 其他 provider 的模型作为备选
        tried_providers = {primary_provider} if primary_provider else set()
        for _ in range(3):  # 最多额外 3 个备选
            next_p = _next_provider("", tried_providers)
            if not next_p:
                break
            tried_providers.add(next_p)
            fb_model = _pick_fallback_model(next_p)
            fallback_chain.append((fb_model, next_p,
                                   _provider_clients[next_p],
                                   _model_to_id.get(fb_model, fb_model)))

        # 依次尝试
        last_error = None
        for fb_model, fb_provider, fb_client, fb_api_model in fallback_chain:
            try:
                raw_text = single_completion(fb_client, fb_api_model, messages,
                                         temperature=0.0, max_tokens=4096)
                text = _extract_json(raw_text)
                try:
                    result = json.loads(text)
                except json.JSONDecodeError as je:
                    # 修复常见 LLM JSON 错误再试
                    repaired = _repair_json(text)
                    try:
                        result = json.loads(repaired)
                    except json.JSONDecodeError:
                        # 两种尝试都失败，记录片段便于诊断
                        logger.warning(f"  [LLM-TOOL] {fb_model} raw(-100 chars): {repr(raw_text[-100:])}")
                        raise je
                # LLM 可能直接返回数组 [...] 而非包裹对象 → 自动包装
                if isinstance(result, list):
                    array_key = next((k for k, v in output_schema.items() if "array" in v), None)
                    if array_key:
                        result = {array_key: result}
                if fb_model != model:
                    logger.info(f"  [LLM-TOOL] fallback success | {model}->{fb_model} | provider={fb_provider}")
                return result
            except Exception as e:
                last_error = e
                # 检查是否是耗尽类错误，记录到熔断器
                from model_client.circuit_breaker import is_exhausted_error
                from model_client.model_context import record_model_failure
                try:
                    if is_exhausted_error(e):
                        record_model_failure(fb_provider, e)
                except Exception:
                    pass
                logger.warning(f"  [LLM-TOOL] {fb_model} failed ({type(e).__name__}), trying next...")
                continue

        raise RuntimeError(
            f"LLM tool '{model}' failed after {len(fallback_chain)} attempt(s). "
            f"Last error: {type(last_error).__name__}: {last_error}"
        )

    set_llm_executor(execute)
    logger.info(f"  [LLM-TOOL] executor ready | provider={ctx.provider} | base={ctx.model_config.base_url}")
