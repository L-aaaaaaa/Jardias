"""lifecycle — 对话循环：轮次执行、状态收集、历史持久化。"""
import os

from actor_config import load_config, MODEL_NAMES, resolve_model
from character.summarizer import check_and_compress
from common.actor_log import turn_open, model_switch as log_model_switch
from common.context import form_full_context
from common.logger import logger
from common.utils import set_display_name
from model_client.model_context import (
    set_round_meta, update_cumulative, build_round_context,
)
from model_client.switch import resolve_chat, sync_config_to_model, reload_after_switch, make_switch_note, \
    _next_provider, _pick_fallback_model, _next_vision_provider
from tool.builtin import tools, clear_pending_switch


def extract_reply(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"]
    return ""


async def _run_turn(ctx, user_input: str, image_url: str | None,
        switch_note: str | None, round_context: str) -> tuple[bool, list[dict]]:
    """发送一轮对话，处理模型自切换重试 + 供应商故障自动转移。返回 (是否成功, messages 列表)。"""
    retry = 0
    messages = None  # None → 首次构建；非 None → 重试时复用上次的 result.messages
    tried: set = {ctx.provider}  # 已尝试的供应商（含当前），避免无限回环

    while retry < 5:
        if messages is None:
            messages = form_full_context(
                ctx.config, ctx.history.messages, user_input,
                image_url=image_url, switch_note=switch_note,
                round_context=round_context,
                character_name=ctx.character_name,
            )
        switch_note = None  # 已消费（form_full_context 或下方追加入 messages）
        ctx.model_config.tools = tools.get_definitions()
        ctx.model_config.tool_choice = "auto"
        try:
            result = await ctx.chat_fn(messages, ctx.model_config,
                character_name=ctx.character_name)
            if result.should_switch:
                retry += 1
                old_prov, old_model = ctx.config.runtime.provider, ctx.config.runtime.model
                old_full = ctx.model_config.model
                reload_after_switch(ctx)
                log_model_switch(old_prov, old_model, ctx.provider, ctx.model,
                    reason="LLM requested switch")
                if retry < 3:
                    switch_note = make_switch_note(
                        old_prov, old_model, ctx.config.runtime.provider, ctx.config.runtime.model,
                        old_full=old_full, new_full=ctx.model_config.model,
                        reason="model switch requested by previous assistant")
                    # 复用 result.messages 携带当前轮全部上下文，避免失忆
                    messages = result.messages
                    if switch_note:
                        messages.append({"role": "user", "content": switch_note})
                continue
            from model_client.model_context import record_model_success
            record_model_success(ctx.provider)
            return True, messages
        except Exception as e:
            logger.error(f"  ❌ 调用异常 | {ctx.provider}/{ctx.model} | {type(e).__name__}: {e}")
            # 如果是耗尽类错误，记录到熔断器
            from model_client.circuit_breaker import is_exhausted_error
            from model_client.model_context import record_model_failure
            if is_exhausted_error(e):
                record_model_failure(ctx.provider, e)
            # ── 自动故障转移 ──
            # 图片消息需要 vision 能力，优先选有 vision 的供应商
            need_vision = bool(image_url)
            fallback = _next_vision_provider(ctx.provider, tried) if need_vision else None
            if not fallback:
                fallback = _next_provider(ctx.provider, tried)
            if fallback:
                tried.add(fallback)
                fallback_model = _pick_fallback_model(fallback, vision_first=need_vision)
                old_prov, old_model = ctx.provider, ctx.model
                old_full = old_model  # 短名，用于 switch_note
                # 直接切换 ctx，不持久化（临时转移，下次启动恢复原配置）
                ctx.provider = fallback
                ctx.model = fallback_model
                ctx.config.runtime.provider = fallback
                ctx.config.runtime.model = fallback_model
                ctx.chat_fn = resolve_chat(fallback)
                _prov, ctx.model_config = resolve_model(fallback, fallback_model)
                sync_config_to_model(ctx.config, ctx.model_config)
                from model_client.model_context import set_actual_model
                set_actual_model(ctx.provider, ctx.model)
                new_full = ctx.model_config.model
                log_model_switch(old_prov, old_model, ctx.provider, ctx.model,
                    reason=f"auto-fallback after {type(e).__name__}")
                switch_note = make_switch_note(
                    old_prov, old_model, ctx.provider, ctx.model,
                    old_full=old_full, new_full=new_full,
                    reason=f"上一个引擎发生错误 ({type(e).__name__})，自动转移到可用供应商")
                messages = None  # 强制重建上下文（新供应商）
                retry += 1
                continue
            # 无可转移供应商 → 放弃
            set_round_meta(0, error=f"{type(e).__name__}: {e}")
            break

    return False, []


def _collect_round_meta(round_ok: bool, ctx) -> str:
    """收集本轮元信息，返回下轮的 round_context。"""
    from model_client.model_context import last_round
    if round_ok:
        update_cumulative(last_round.usage, ctx.provider, last_round.api_time)
    new_ctx = build_round_context()
    if new_ctx:
        logger.info(f"  {'[OK]' if round_ok else '[WARN]'} round meta\n{new_ctx}")
    return new_ctx


def _post_round(ctx, user_input: str, messages: list[dict]):
    """收尾：存历史、重载配置、轮次 +1。"""
    reply = extract_reply(messages)
    ctx.history.append_pair(user_input, reply)
    ctx.history.save()
    ctx.config = load_config(ctx.character_name, config_dir=ctx.config_dir)
    sync_config_to_model(ctx.config, ctx.model_config)
    ctx.turn_num += 1


async def _post_round_async(ctx, user_input: str, messages: list[dict]):
    """收尾 + L1 压缩（需要 async 上下文）。"""
    _post_round(ctx, user_input, messages)
    await check_and_compress(ctx.character_name, ctx.history.messages)


async def _do_switch_character(ctx, name: str):
    """重新加载目标角色的所有上下文状态。"""
    from character import get_history_path
    from character.history import History
    from tool.builtin import set_actor

    old = ctx.character_name

    # 1. 保存当前角色历史
    ctx.history.save()

    # 2. 切角色名 + 重载配置
    ctx.character_name = name
    set_actor(name)
    set_display_name(name)
    ctx.config = load_config(name, config_dir=ctx.config_dir)

    # 3. 如果 provider/model 变了 → 切换引擎
    if ctx.provider != ctx.config.runtime.provider or ctx.model != ctx.config.runtime.model:
        ctx.provider = ctx.config.runtime.provider
        ctx.model = ctx.config.runtime.model
        ctx.chat_fn = resolve_chat(ctx.provider)

    # 4. 重建 model_config
    _prov, new_mc = resolve_model(ctx.provider, ctx.model)
    ctx.model_config = new_mc
    sync_config_to_model(ctx.config, ctx.model_config)

    # 5. 加载目标角色历史
    history_path = str(get_history_path(name))
    ctx.history = History(history_path).load()
    ctx.turn_num = int(len(ctx.history.messages) / 2) + 1

    msg = (
        f"  🔄 角色切换 | {old} → {name} | "
        f"引擎 {ctx.provider}/{ctx.model} | "
        f"历史 {len(ctx.history.messages)} 条"
    )
    logger.info(msg)
    print(f"\n{msg}\n")


async def _handle_directive(user_input: str, ctx):
    """
    处理 ·· 前缀指令。
    
    ··角色名 [内容]  → 切换到角色，有内容则立即发送
    ··工具名 [参数]   → 直接执行工具
    
    返回 None 表示未匹配（正常流程），"" 表示已处理无需回复，
    非空字符串表示替换后的 user_input（切换+发送）。
    """
    stripped = user_input.strip()
    if not stripped.startswith("··"):
        return None

    after = stripped[2:].lstrip()
    if not after:
        print("  ❌ 语法: ··角色名 或 ··工具名")
        return ""

    parts = after.split(maxsplit=1)
    target = parts[0]
    content = parts[1].strip() if len(parts) > 1 else ""

    # ── 检查是否是工具名 ──
    tool = tools.get(target)
    if tool:
        try:
            result = await tool.execute({})  # 快速调用，无参数
            print(f"\n  ⚡ [{target}]")
            print(f"  {result}")
        except Exception as e:
            print(f"  ❌ [{target}] 执行失败: {e}")
        return ""

    # ── 检查是否是角色名 ──
    from character.registry import registry
    if registry.exists(target):
        if ctx.character_name != target:
            await _do_switch_character(ctx, target)
        if content:
            return content  # 切换 + 发送内容
        return ""  # 纯切换

    print(f"  ❌ 未找到角色或工具: {target}")
    return ""


async def conversation_loop(ctx, allow_switch: bool = False):
    """
    对话主循环。
    
    若 allow_switch=True，用户输入 "switch" 时返回 "switch" 信号，
    调用方负责重新选择角色并重新 bootstrap。
    否则 "switch" 被视为普通文本。
    """
    round_context = ""

    while True:
        print("\n" + "—" * 30)
        user_input = input("# 【用户输入】: ")
        if user_input.strip().lower() in ("exit", "quit", "q"):
            ctx.history.save()
            break

        if allow_switch and user_input.strip().lower() == "switch":
            ctx.history.save()
            return "switch"

        # ── `··` 路由 ──
        routed = await _handle_directive(user_input, ctx)
        if routed is not None:
            if routed == "":
                continue  # 纯切换，等待下一轮
            user_input = routed  # 切换 + 内容，继续发送

        turn_open(ctx.turn_num, ctx.config.runtime.provider,
            ctx.config.runtime.model, ctx.model_config.model,
            runtime=ctx.config.runtime, tool_defs=tools.get_definitions())

        switch_note: str | None = None

        # ── 图片预检 ──
        from media.image import detect_image_url, detect_local_image, local_image_to_data_url, auto_switch_for_vision

        image_url = detect_image_url(user_input)
        if not image_url:
            local_path = detect_local_image(user_input)
            if local_path:
                image_url = local_image_to_data_url(local_path)
                if image_url:
                    from common.actor_log import local_image_loaded
                    local_image_loaded(os.path.basename(local_path),
                        os.path.getsize(local_path) // 1024)

        if image_url:
            old_prov, old_model = ctx.config.runtime.provider, ctx.config.runtime.model
            switched = auto_switch_for_vision(ctx, image_url)
            if switched:
                old_full = MODEL_NAMES.get(old_prov, {}).get(old_model, old_model)
                switch_note = make_switch_note(
                    old_prov, old_model, ctx.config.runtime.provider, ctx.config.runtime.model,
                    old_full=old_full, new_full=ctx.model_config.model,
                    reason="auto-switch for image understanding"
                )

        # ── 执行 ──
        round_ok, messages = await _run_turn(ctx, user_input, image_url, switch_note, round_context)
        round_context = _collect_round_meta(round_ok, ctx)
        await _post_round_async(ctx, user_input, messages)

        # ── 角色切换 ──
        next_character = clear_pending_switch()
        if next_character:
            await _do_switch_character(ctx, next_character)


# ── 交互式入口（供 app.py 调用） ──

async def interactive_loop():
    """交互式角色选择 + 对话循环（支持 switch 切换角色）。"""
    from common.bootstrap import bootstrap
    from common.cli_style import separator_to_terminal
    from character.character_menu import select_or_create_character

    while True:
        result = select_or_create_character()
        if result is None:
            print("退出。")
            return

        char_name, provider, model = result
        set_display_name(char_name)
        ctx = bootstrap(provider, model, character_name=char_name)
        separator_to_terminal("━", 30)
        print(f"当前角色: {char_name} | 引擎: {ctx.provider}/{ctx.model}")
        print("输入 'quit' 退出，输入 'switch' 切换角色\n")

        signal = await conversation_loop(ctx, allow_switch=True)
        if signal != "switch":
            return  # quit
