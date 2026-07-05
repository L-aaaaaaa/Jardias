"""
lifecycle.py — 对话循环：轮次执行、状态收集、历史持久化。
"""
import asyncio
import os
import re as _re_module
import sys
from datetime import datetime as _dt

from character.summarizer import check_and_compress
from common.actor_log import turn_open, model_switch as log_model_switch
from common.context import form_full_context
from common.logger import logger
from common.utils import set_display_name
from tool.builtin import tools, clear_pending_switch
from character.config_io import load_config
from yinao import resolve_ipu
from yinao.ipu_client import resolve_chat, sync_config_to_ipu, reload_after_switch, make_switch_note, \
    _next_provider, _pick_fallback_ipu, _next_vision_provider
from yinao.ipu_client.ipu_context import (
    set_round_meta, update_cumulative, build_round_context,
)


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
            messages = form_full_context(ctx.config, ctx.history.messages, user_input,
                image_url=image_url, switch_note=switch_note,
                round_context=round_context, character_name=ctx.character_name)
        switch_note = None
        ctx.ipu_config.tools = tools.get_definitions()
        ctx.ipu_config.tool_choice = "auto"
        try:
            result = await ctx.chat_fn(messages, ctx.ipu_config, character_name=ctx.character_name)
            if result.should_switch:
                retry += 1
                old_prov, old_ipu = ctx.config.runtime.provider, ctx.config.runtime.ipu
                reload_after_switch(ctx)
                log_model_switch(old_prov, old_ipu, ctx.provider, ctx.ipu, reason="LLM requested switch")
                if retry < 3:
                    switch_note = make_switch_note(old_prov, old_ipu, ctx.provider, ctx.ipu,
                        old_full=old_ipu, new_full=ctx.ipu_config.ipu,
                        reason="智能基元切换由上一轮 assistant 请求")
                    messages = result.messages
                    if switch_note:
                        messages.append({"role": "user", "content": switch_note})
                continue
            from yinao.ipu_client.ipu_context import record_ipu_success
            record_ipu_success(ctx.provider)
            return True, messages
        except Exception as e:
            logger.error(f"  [ERROR] 调用异常 | {ctx.provider}/{ctx.ipu} | {type(e).__name__}: {e}")
            from yinao.ipu_client import is_exhausted_error
            from yinao.ipu_client.ipu_context import record_ipu_failure
            if is_exhausted_error(e):
                record_ipu_failure(ctx.provider, e)
            need_vision = bool(image_url)
            fallback = _next_vision_provider(ctx.provider, tried) if need_vision else _next_provider(ctx.provider, tried)
            if fallback:
                old_prov, old_ipu = ctx.provider, ctx.ipu
                tried.add(fallback)
                fm = _pick_fallback_ipu(fallback, vision_first=need_vision)
                ctx.provider, ctx.ipu = fallback, fm
                ctx.config.runtime.provider, ctx.config.runtime.ipu = fallback, fm
                ctx.chat_fn = resolve_chat(fallback)
                _, ctx.ipu_config = resolve_ipu(fallback, fm)
                sync_config_to_ipu(ctx.config, ctx.ipu_config)
                from yinao.ipu_client.ipu_context import set_active_ipu
                set_active_ipu(ctx.provider, ctx.ipu)
                log_model_switch(old_prov, old_ipu, ctx.provider, ctx.ipu,
                    reason=f"auto-fallback after {type(e).__name__}")
                switch_note = make_switch_note(old_prov, old_ipu, ctx.provider, ctx.ipu,
                    old_full=old_ipu, new_full=ctx.ipu_config.ipu,
                    reason=f"上一个引擎发生错误 ({type(e).__name__})，自动转移到可用供应商")
                messages = None
                retry += 1
                continue
            set_round_meta(0, error=f"{type(e).__name__}: {e}")
            break
    return False, []


def _collect_round_meta(round_ok: bool, ctx) -> str:
    from yinao.ipu_client.ipu_context import last_round
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
    sync_config_to_ipu(ctx.config, ctx.ipu_config)
    ctx.turn_num += 1


def _build_failure_reply(ctx, messages):
    from yinao.ipu_client.ipu_context import last_round
    err = last_round.error if last_round.error else "未知错误"
    return f"[本轮对话失败] 引擎 {ctx.provider}/{ctx.ipu} 返回错误，且无可用备选供应商。错误: {err}。"


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
    ctx.ipu = ctx.config.runtime.ipu
    ctx.chat_fn = resolve_chat(ctx.provider)
    _, ctx.ipu_config = resolve_ipu(ctx.provider, ctx.ipu)
    sync_config_to_ipu(ctx.config, ctx.ipu_config)
    from yinao.ipu_client.ipu_context import set_active_ipu
    set_active_ipu(ctx.provider, ctx.ipu)
    ctx.history = History(str(get_history_path(name))).load()
    ctx.turn_num = int(len(ctx.history.messages) / 2) + 1
    logger.info(f"  [SWITCH] {ctx.character_name} | {ctx.provider}/{ctx.ipu}")


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
    triggers = [m for m in ctx.history.messages if m.get("role") == "system_trigger"]
    if not triggers:
        return "时策任务到期"
    last = triggers[-1]
    content = last.get("content", "")
    desc = content.split("\n", 1)[-1].strip() if "\n" in content else content.strip()
    pending = []
    for m in reversed(ctx.history.messages):
        if m.get("role") == "assistant" and m.get("content"):
            break
        if m.get("role") == "system_trigger":
            pending.append(m)
    skipped = len(pending) - 1
    if skipped > 0:
        return f"时策任务到期，请执行：{desc}（前面已有 {skipped} 个任务过期，本次是第 {skipped + 1} 个）"
    return f"时策任务到期，请执行：{desc}"


def _format_trigger_display(user_input: str) -> str:
    if "请执行：" in user_input:
        return user_input[user_input.find("请执行：") + 4:][:60]
    return user_input[:60]


def _get_pending_triggers(ctx) -> list[str]:
    ctx.history.load()
    msgs = ctx.history.messages
    processed = 0
    total = sum(1 for m in msgs if m.get("role") == "system_trigger")
    for i, m in enumerate(msgs):
        if m.get("role") == "system_trigger":
            if any(msgs[j].get("role") == "assistant" and msgs[j].get("content")
                   for j in range(i + 1, len(msgs))):
                processed += 1
    pending = []
    for m in reversed(msgs):
        if m.get("role") == "system_trigger":
            pending.append(m)
        elif m.get("role") == "assistant" and m.get("content"):
            break
        elif m.get("role") == "user":
            if not m.get("content", "").startswith("时策任务到期"):
                break
    if not pending:
        return []
    missed_before = len(pending) - 1
    result = []
    for m in reversed(pending):
        content = m.get("content", "")
        if "\n" in content:
            parts = content.split("\n", 1)
            header = parts[0].strip()
            desc = parts[1].strip()
            lm = _re_module.search(r'已延迟\s*(\d+)\s*秒', header)
            late_sec = int(lm.group(1)) if lm else 0
        else:
            desc = content.strip()
            late_sec = 0
        pos = processed + len(result) + 1
        detail = f"（#{pos}"
        if late_sec > 0:
            detail += f"，延迟 {late_sec}s"
        if missed_before > 0 and len(result) == 0:
            detail += f"，前 {missed_before} 个错过"
        detail += "）"
        result.append(f"{desc}{detail}")
    return result


def _clear_prompt_line():
    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()


async def _process_triggers(ctx):
    triggers = _get_pending_triggers(ctx)
    if not triggers:
        return
    if len(triggers) == 1:
        user_input = "时策任务到期，请执行：" + triggers[0]
    else:
        lines = ["以下时策任务均已到期。请先用 shice_schedule_list 检查当前状态，判断累计错过次数是否≥3。"
                 "若≥3：取消所有剩余任务，以新间隔重新注册。若<3：直接执行以下各任务：", ""]
        for i, t in enumerate(triggers):
            lines.append(f"{i + 1}. {t}")
        user_input = "\n".join(lines)
    _clear_prompt_line()
    if len(triggers) > 1:
        print(f"\n[时策触发 {_dt.now().strftime('%H:%M:%S')}] 共 {len(triggers)} 个任务到期")
    else:
        print(f"\n[时策触发 {_dt.now().strftime('%H:%M:%S')}] {triggers[0][:60]}")
    turn_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    round_ok, messages = await _run_turn(ctx, user_input, None, None, "")
    reply = extract_reply(messages) if round_ok else ""
    if reply:
        ctx.history.append_assistant(reply)
        ctx.history.save()
        ctx.config = load_config(ctx.character_name, config_dir=ctx.config_dir)
        sync_config_to_ipu(ctx.config, ctx.ipu_config)
        ctx.turn_num += 1


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
            triggers = _get_pending_triggers(ctx)
            if triggers:
                await _process_triggers(ctx)
                continue

            try:
                user_input = await asyncio.wait_for(stdin_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            t_now = _dt.now().strftime("%H:%M:%S")
            print(f"\n（发送时间：{t_now}）")
            from common.actor_log import turn_input
            turn_input(user_input)

            if not user_input.strip():
                continue

            if user_input.strip().lower() in ("exit", "quit", "q"):
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

            turn_open(ctx.turn_num, ctx.config.runtime.provider, ctx.config.runtime.ipu, ctx.ipu_config.ipu,
                runtime=ctx.config.runtime, tool_defs=tools.get_definitions())

            from media.image import detect_image_url, detect_local_image, local_image_to_data_url, \
                auto_switch_for_vision
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
                old_prov, old_ipu = ctx.config.runtime.provider, ctx.config.runtime.ipu
                if auto_switch_for_vision(ctx, image_url):
                    switch_note = make_switch_note(old_prov, old_ipu, ctx.config.runtime.provider,
                        ctx.config.runtime.ipu,
                        old_full=ctx.ipu_config.ipu, new_full=ctx.ipu_config.ipu,
                        reason="auto-switch for image understanding")

            turn_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
            round_ok, messages = await _run_turn(ctx, user_input, image_url, switch_note, round_context)
            round_context = _collect_round_meta(round_ok, ctx)
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
        char_name, provider, ipu = result
        set_display_name(char_name)
        ctx = bootstrap(provider, ipu, character_name=char_name)
        separator_to_terminal("=", 30)
        print(f"当前角色: {char_name} | 引擎: {ctx.provider}/{ctx.ipu}")
        print("输入 'quit' 退出，输入 'switch' 切换角色\n")
        signal = await conversation_loop(ctx, allow_switch=True)
        if signal != "switch":
            return