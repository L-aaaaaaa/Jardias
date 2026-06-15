"""
lifecycle.py — 对话循环：轮次执行、状态收集、历史持久化。
"""
import asyncio
import os
import re as _re_module
import sys
from datetime import datetime as _dt

from actor_config import load_config, MODEL_NAMES, resolve_model
from character.summarizer import check_and_compress
from common.actor_log import turn_open, model_switch as log_model_switch
from common.context import form_full_context
from common.logger import logger
from common.utils import set_display_name, set_stream_color
from model_client.model_context import (
    set_round_meta, update_cumulative, build_round_context,
)
from model_client.switch import resolve_chat, sync_config_to_model, reload_after_switch, make_switch_note, \
    _next_provider, _pick_fallback_model, _next_vision_provider
from tool.builtin import tools, clear_pending_switch


def extract_reply(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            c = msg["content"]
            c = _re_module.sub(r'\[思考\].*?\n', '', c, flags=_re_module.DOTALL)
            c = _re_module.sub(r'<think>.*?</think>', '', c, flags=_re_module.DOTALL)
            return c.strip()
    return ""


async def _run_turn(ctx, user_input: str, image_url: str | None,
        switch_note: str | None, round_context: str) -> tuple[bool, list[dict]]:
    retry = 0
    messages = None
    tried: set = {ctx.provider}
    while retry < 5:
        if messages is None:
            messages = form_full_context(
                ctx.config, ctx.history.messages, user_input,
                image_url=image_url, switch_note=switch_note,
                round_context=round_context, character_name=ctx.character_name,
            )
        switch_note = None
        ctx.model_config.tools = tools.get_definitions()
        ctx.model_config.tool_choice = "auto"
        try:
            result = await ctx.chat_fn(messages, ctx.model_config, character_name=ctx.character_name)
            if result.should_switch:
                retry += 1
                old_prov, old_model = ctx.config.runtime.provider, ctx.config.runtime.model
                reload_after_switch(ctx)
                log_model_switch(old_prov, old_model, ctx.provider, ctx.model, reason="LLM requested switch")
                if retry < 3:
                    switch_note = make_switch_note(
                        old_prov, old_model, ctx.config.runtime.provider, ctx.config.runtime.model,
                        old_full=old_model, new_full=ctx.model_config.model,
                        reason="model switch requested by previous assistant")
                    messages = result.messages
                    if switch_note:
                        messages.append({"role": "user", "content": switch_note})
                continue
            from model_client.model_context import record_model_success
            record_model_success(ctx.provider)
            return True, messages
        except Exception as e:
            logger.error(f"  [ERROR] 调用异常 | {ctx.provider}/{ctx.model} | {type(e).__name__}: {e}")
            from model_client.circuit_breaker import is_exhausted_error
            from model_client.model_context import record_model_failure
            if is_exhausted_error(e):
                record_model_failure(ctx.provider, e)
            need_vision = bool(image_url)
            fallback = _next_vision_provider(ctx.provider, tried) if need_vision else _next_provider(ctx.provider, tried)
            if fallback:
                old_prov, old_model = ctx.provider, ctx.model
                tried.add(fallback)
                fallback_model = _pick_fallback_model(fallback, vision_first=need_vision)
                ctx.provider, ctx.model = fallback, fallback_model
                ctx.config.runtime.provider, ctx.config.runtime.model = fallback, fallback_model
                ctx.chat_fn = resolve_chat(fallback)
                _, ctx.model_config = resolve_model(fallback, fallback_model)
                sync_config_to_model(ctx.config, ctx.model_config)
                from model_client.model_context import set_actual_model
                set_actual_model(ctx.provider, ctx.model)
                log_model_switch(old_prov, old_model, ctx.provider, ctx.model,
                    reason=f"auto-fallback after {type(e).__name__}")
                switch_note = make_switch_note(
                    old_prov, old_model, ctx.provider, ctx.model,
                    old_full=old_model, new_full=ctx.model_config.model,
                    reason=f"上一个引擎发生错误 ({type(e).__name__})，自动转移到可用供应商")
                messages = None
                retry += 1
                continue
            set_round_meta(0, error=f"{type(e).__name__}: {e}")
            break
    return False, []


def _collect_round_meta(round_ok: bool, ctx) -> str:
    from model_client.model_context import last_round
    if round_ok:
        update_cumulative(last_round.usage, ctx.provider, last_round.api_time)
    return build_round_context()


def _post_round(ctx, user_input: str, messages: list[dict], round_ok: bool = True, ts: str | None = None):
    if round_ok:
        reply = extract_reply(messages)
    else:
        reply = _build_failure_reply(ctx, messages)
    if user_input.startswith("时策任务到期"):
        if reply:
            ctx.history.append_assistant(reply)
    else:
        ctx.history.append_pair(user_input, reply, ts=ts)
    ctx.history.save()
    ctx.config = load_config(ctx.character_name, config_dir=ctx.config_dir)
    sync_config_to_model(ctx.config, ctx.model_config)
    ctx.turn_num += 1


def _build_failure_reply(ctx, messages):
    from model_client.model_context import last_round
    err = last_round.error if last_round.error else "未知错误"
    return (
        f"[本轮对话失败] 引擎 {ctx.provider}/{ctx.model} 返回错误，"
        f"且无可用备选供应商。错误: {err}。"
    )


async def _post_round_async(ctx, user_input, messages, round_ok=True, ts=None):
    _post_round(ctx, user_input, messages, round_ok, ts=ts)
    await check_and_compress(ctx.character_name, ctx.history.messages)


async def _do_switch_character(ctx, name: str):
    from character import get_history_path
    from character.history import History
    from tool.builtin import set_actor
    ctx.history.save()
    ctx.character_name = name
    set_actor(name)
    set_display_name(name)
    ctx.config = load_config(name, config_dir=ctx.config_dir)
    ctx.provider = ctx.config.runtime.provider
    ctx.model = ctx.config.runtime.model
    ctx.chat_fn = resolve_chat(ctx.provider)
    _, ctx.model_config = resolve_model(ctx.provider, ctx.model)
    sync_config_to_model(ctx.config, ctx.model_config)
    from model_client.model_context import set_actual_model
    set_actual_model(ctx.provider, ctx.model)
    ctx.history = History(str(get_history_path(name))).load()
    ctx.turn_num = int(len(ctx.history.messages) / 2) + 1
    logger.info(f"  [SWITCH] {ctx.character_name} | {ctx.provider}/{ctx.model}")


async def _handle_directive(user_input: str, ctx):
    stripped = user_input.strip()
    if stripped.startswith("··"):
        parts = stripped[2:].split(None, 1)
        target = parts[0] if parts else ""
        new_input = parts[1] if len(parts) > 1 else ""
        from character.registry import registry
        if registry.exists(target):
            await _do_switch_character(ctx, target)
            return new_input or ""
        if target in tools.list_names():
            result = await tools.execute(target, {})
            print(result)
            return None
        print(f"[Error] 未知目标: {target}")
        return None
    return None


def _build_trigger_message(ctx) -> str:
    """构建时策触发的友好提示消息，避免 LLM 困惑。"""
    triggers = [m for m in ctx.history.messages if m.get("role") == "system_trigger"]
    if not triggers:
        return "时策任务到期"
    last = triggers[-1]
    content = last.get("content", "")
    # system_trigger 格式: "[时策任务 | 已延迟 X 秒]\n{实际消息}"
    # 直接取换行后的实际消息
    desc = content.split("\n", 1)[-1].strip() if "\n" in content else content.strip()
    return f"时策任务到期，请执行：{desc}"


def _format_trigger_display(user_input: str) -> str:
    """从 trigger user_input 中提取纯任务描述用于终端显示。"""
    if "：" in user_input:
        inner = user_input[user_input.find("：") + 1:]
        return inner.rstrip("]").strip()
    return user_input


async def conversation_loop(ctx, allow_switch: bool = False):
    round_context = ""
    stdin_queue: asyncio.Queue = asyncio.Queue()

    async def _stdin_reader():
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            await stdin_queue.put(line.rstrip("\n"))

    reader_task = asyncio.create_task(_stdin_reader())

    try:
        while True:
            print("\n" + "\u2014" * 30)
            sys.stdout.write("# 【用户输入】: ")
            sys.stdout.flush()

            user_input = ""
            while True:
                try:
                    user_input = await asyncio.wait_for(stdin_queue.get(), timeout=1.0)
                    break
                except asyncio.TimeoutError:
                    ctx.history.load()
                    recent = ctx.history.messages[-3:] if len(ctx.history.messages) >= 3 else ctx.history.messages
                    pending = any(m.get("role") == "system_trigger" for m in recent)
                    if pending:
                        last = recent[-1] if recent else {}
                        if last.get("role") != "assistant" or not last.get("content"):
                            user_input = _build_trigger_message(ctx)
                            break
                    continue

            # 时策触发：清除空 prompt 行，显示友好提示
            if user_input.startswith("时策任务到期"):
                sys.stdout.write("\r" + " " * 60 + "\r")
                sys.stdout.flush()
                print(f"\n[时策触发 {_dt.now().strftime('%H:%M:%S')}] {_format_trigger_display(user_input)}")
            else:
                t_now = _dt.now().strftime("%H:%M:%S")
                print(f"\uff08\u53d1\u9001\u65f6\u95f4\uff1a{t_now}\uff09")

            if not user_input.strip():
                continue

            if user_input.startswith("时策任务到期"):
                pass  # 已经是 trigger，继续处理
            elif user_input.strip().lower() in ("exit", "quit", "q"):
                ctx.history.save()
                break

            if allow_switch and user_input.strip().lower() == "switch":
                ctx.history.save()
                return "switch"

            routed = await _handle_directive(user_input, ctx)
            if routed is not None:
                if routed == "":
                    continue
                user_input = routed

            turn_open(ctx.turn_num, ctx.config.runtime.provider,
                ctx.config.runtime.model, ctx.model_config.model,
                runtime=ctx.config.runtime, tool_defs=tools.get_definitions())

            # 图片预检
            from media.image import detect_image_url, detect_local_image, local_image_to_data_url, auto_switch_for_vision
            image_url = detect_image_url(user_input)
            if not image_url:
                local_path = detect_local_image(user_input)
                if local_path:
                    image_url = local_image_to_data_url(local_path)
                    if image_url:
                        from common.actor_log import local_image_loaded
                        local_image_loaded(os.path.basename(local_path), os.path.getsize(local_path) // 1024)

            switch_note = None
            if image_url:
                old_prov, old_model = ctx.config.runtime.provider, ctx.config.runtime.model
                if auto_switch_for_vision(ctx, image_url):
                    switch_note = make_switch_note(
                        old_prov, old_model, ctx.config.runtime.provider, ctx.config.runtime.model,
                        old_full=ctx.model_config.model, new_full=ctx.model_config.model,
                        reason="auto-switch for image understanding")

            turn_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
            round_ok, messages = await _run_turn(ctx, user_input, image_url, switch_note, round_context)
            round_context = _collect_round_meta(round_ok, ctx)

            # 时策触发不保存 user_input 到历史
            if user_input.startswith("时策任务到期"):
                reply = extract_reply(messages) if round_ok else ""
                if reply:
                    ctx.history.append_assistant(reply)
                    ctx.history.save()
                ctx.config = load_config(ctx.character_name, config_dir=ctx.config_dir)
                sync_config_to_model(ctx.config, ctx.model_config)
                ctx.turn_num += 1
            else:
                await _post_round_async(ctx, user_input, messages, round_ok, ts=turn_ts)

            next_character = clear_pending_switch()
            if next_character:
                await _do_switch_character(ctx, next_character)
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass
        # 停止调度器
        from tool.builtin import _scheduler
        if _scheduler:
            _scheduler.stop()


async def interactive_loop():
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
        separator_to_terminal("=", 30)
        print(f"当前角色: {char_name} | 引擎: {ctx.provider}/{ctx.model}")
        print("输入 'quit' 退出，输入 'switch' 切换角色\n")

        signal = await conversation_loop(ctx, allow_switch=True)
        if signal != "switch":
            return
