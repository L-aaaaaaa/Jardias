"""Jardias structured logging -- terminal + file output."""
from __future__ import annotations

import logging as _stdlib_logging
from datetime import datetime

from common.logger import logger

# suppress httpx noise
_stdlib_logging.getLogger("httpx").setLevel(_stdlib_logging.WARNING)
_stdlib_logging.getLogger("httpcore").setLevel(_stdlib_logging.WARNING)

# ── 去重 ──

_last_config_sig: str = ""
_last_tool_names: list[str] = []


def _config_sig(runtime) -> str:
    parts = [f"temperature={runtime.temperature}",
             f"top_p={runtime.top_p}",
             f"max_icp={runtime.max_icp}"]
    if runtime.thinking_mode:
        parts.append(f"thinking_mode={runtime.thinking_mode}")
    if runtime.reasoning_effort:
        parts.append(f"reasoning_effort={runtime.reasoning_effort}")
    parts.append(f"thinking_enabled={runtime.thinking_enabled}")
    return " | ".join(parts)


# ══════════════════════════════════════════════════════════════
#  轮次生命周期
# ══════════════════════════════════════════════════════════════

def turn_open(turn_num: int, provider: str, ipu_short: str, ipu_full: str,
        runtime=None, tool_defs: list[dict] | None = None) -> None:
    """轮次开始 — 不画分割线（print 已画），直接输出轮次 + 参数"""
    now = datetime.now().strftime("%H:%M:%S")
    logger.info(f"第 {turn_num} 轮 | {now} | {provider}/{ipu_short} -> {ipu_full}")

    global _last_config_sig, _last_tool_names

    if runtime:
        sig = _config_sig(runtime)
        if sig != _last_config_sig:
            _last_config_sig = sig
            logger.info(f"  引擎参数  | {sig}")

    if tool_defs:
        names = sorted(t['function']['name'] for t in tool_defs)
        if names != _last_tool_names:
            _last_tool_names = names
            logger.info(f"  可用工具  | {len(names)} 个: {' '.join(names)}")


def turn_input(text: str):
    logger.info(f"  【用户输入】：{text}")


# ══════════════════════════════════════════════════════════════
#  步骤生命周期
# ══════════════════════════════════════════════════════════════

def round_start(round_num: int, msg_count: int) -> None:
    logger.info(f"    第 {round_num} 步 · {msg_count} 条消息")


def round_end(round_count: int, reason: str = "no tool calls"):
    logger.info(f"  [OK] {round_count} 步完成 ({reason})")


# ══════════════════════════════════════════════════════════════
#  事件
# ══════════════════════════════════════════════════════════════

def model_switch(old_prov: str, old_ipu: str, new_prov: str, new_ipu: str, reason: str = "") -> None:
    tag = f"{new_ipu} ({new_prov})"
    if reason:
        tag += f" -- {reason}"
    logger.info(f"  [SWITCH] {old_prov}/{old_ipu} -> {tag}")


def local_image_loaded(filename: str, size_kb: int = 0) -> None:
    tag = f"{filename}"
    if size_kb:
        tag += f", {size_kb}KB"
    logger.info(f"  [IMG] {tag}")


def max_rounds_reached(max_iter: int) -> None:
    logger.warning(f"  [WARN] 达到最大步数限制 ({max_iter})，强制退出")


# ══════════════════════════════════════════════════════════════
#  工具
# ══════════════════════════════════════════════════════════════

def format_api_ok(elapsed: float, usage: dict | None = None,
        finish_reason: str | None = None) -> str:
    """合并 API 耗时 + ICP 用量 + 截断警告为一行中文日志"""
    parts = [f"API OK · {elapsed:.1f}s"]

    if usage:
        tokens = []
        if usage.get("prompt_tokens"):
            tokens.append(f"输入 {usage['prompt_tokens']} 智点")
        details = usage.get("completion_tokens_details", {}) or {}
        reason_tok = details.get("reasoning_tokens", 0)
        comp_tok = usage.get("completion_tokens", 0)
        if reason_tok:
            tokens.append(f"思考 {reason_tok} 智点")
            output_only = comp_tok - reason_tok
            tokens.append(f"输出 {output_only} 智点")
        elif comp_tok:
            tokens.append(f"输出 {comp_tok} 智点")
        if usage.get("total_tokens"):
            tokens.append(f"合计 {usage['total_tokens']} 智点")
        if tokens:
            parts.append(" · ".join(tokens))

    if finish_reason == "length":
        parts.append("[WARN] 输出被截断(长度限制)")

    return " · ".join(parts)


def format_round_usage(usage: dict | None) -> str:
    """把本轮 usage 格式化成中文自然句（用户视角，套入智点计数）。

    例：'本轮输入 4698 智点，输出 17 智点的思考，9 智点的回答，合计 4724 智点'
    """
    from common.utils import get_silent
    if get_silent() or not usage:
        return ""
    prompt = usage.get("prompt_tokens", 0)
    total = usage.get("total_tokens", 0)
    details = usage.get("completion_tokens_details", {}) or {}
    reason_tok = details.get("reasoning_tokens", 0)
    comp_tok = usage.get("completion_tokens", 0)

    parts = [f"本轮输入 {prompt} 智点"]
    if reason_tok and comp_tok:
        reply_tok = comp_tok - reason_tok
        parts.append(f"输出 {reason_tok} 智点的思考，{reply_tok} 智点的回答")
    elif comp_tok:
        parts.append(f"输出 {comp_tok} 智点的回答")
    if total:
        parts.append(f"合计 {total} 智点")
    return "，".join(parts) + "。"


# ══════════════════════════════════════════════════════════════
#  启动
# ══════════════════════════════════════════════════════════════

def bootstrap_summary(history_msgs: int, provider: str, ipu: str, tool_count: int) -> None:
    logger.info(f"Jardias 启动完成 -- 已加载 {history_msgs} 条历史记录，"
                f"当前引擎 {provider}/{ipu}，可用工具 {tool_count} 个")


def turn_header(turn_num: int, provider: str, ipu_full: str, ipu_short: str):
    now = datetime.now().strftime("%H:%M:%S")
    logger.info(f"=== Turn {turn_num} | {now} | {provider}/{ipu_short} -> {ipu_full}")


def turn_config_brief(runtime) -> None:
    items = [f"temp={runtime.temperature}", f"top_p={runtime.top_p}",
             f"max_icp={runtime.max_icp}"]
    if runtime.thinking_mode:
        items.append(f"think={runtime.thinking_mode}")
    if runtime.reasoning_effort:
        items.append(f"effort={runtime.reasoning_effort}")
    items.append(f"think_enabled={runtime.thinking_enabled}")
    logger.info(f"  config | {', '.join(items)}")


def turn_tools_summary(tool_defs: list[dict]) -> None:
    names = [t['function']['name'] for t in tool_defs]
    logger.info(f"  tools  | {len(names)}: {' '.join(names)}")


def round_begin(num: int) -> None:
    logger.info(f"  -- Round {num} --")


def round_end_ok(rounds: int, tool_calls: int = 0, elapsed: float = 0.0) -> None:
    parts = []
    if elapsed:
        parts.append(f"API {elapsed:.1f}s")
    if tool_calls:
        parts.append(f"{tool_calls} tool calls")
    logger.info(f"  DONE | total {rounds} rounds" + (f" | {' · '.join(parts)}" if parts else ""))


def tool_calls_summary(calls: list) -> None:
    names = []
    for tc in calls:
        try:
            import json
            args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
            names.append(f"{tc.name}({', '.join(f'{k}={v}' for k, v in args.items())})")
        except Exception:
            names.append(f"{tc.name}(...)")
    logger.info(f"  TOOLS | {'; '.join(names)}")