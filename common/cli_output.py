"""cli_output — 终端输出层。

集中以下职责：
- 全局会话状态：display_name / silent / stream_color。
- 流式分隔线：``separate_print``（长 50、auto-color、角色名前缀）和 ``separator_to_terminal``（短轻量 banner）。
- 流式打印：``stream_print`` / ``stream_newline``，带 ANSI 颜色与 Windows GBK 兼容。
- RoundOutput 渲染：``render_round``。

设计：
- ``separate_print`` 和 ``separator_to_terminal`` 是两种分隔线，语义不同：
    - ``separate_print``：对话内分段（"推理过程" / "回复" / "工具调用"）。
    - ``separator_to_terminal``：菜单 / 启动 banner / 生命周期节点。
- silent 标志是模块级全局状态，由 ``set_silent`` / ``get_silent`` 维护，所有打印函数自行短路。
- 不依赖 logger；用户视角的输出永远直出 stdout。
"""
from __future__ import annotations

import sys
from datetime import datetime as _dt

from data_shape import RoundOutput

# ════════════════════════════════════════════════════════════════

_display_name: str | None = None
_silent: bool = False

# ── ANSI 颜色 ──
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_RESET = "\033[0m"
_COLORS = {"yellow": _YELLOW, "blue": _BLUE}

_stream_color: str | None = None  # 全局流式颜色标记


# ── 全局状态 ──

def set_stream_color(color: str | None):
    """设置当前流式输出颜色（"yellow" / None）。推理输出自动设黄色。"""
    global _stream_color
    _stream_color = color


def set_display_name(name: str):
    """设置当前角色名，供 separate_print 使用。"""
    global _display_name
    _display_name = name


def set_silent(v: bool):
    """静默模式：抑制所有终端输出（send_to_character 内部使用）。"""
    global _silent
    _silent = v


def get_silent() -> bool:
    return _silent


# ── 分隔线 ──

def separate_print(separator: str = "─", title: str = "", length: int = 50,
        end: bool = False) -> None:
    """
    流式输出分隔线 — 用户视角直出，不经 logger。

    如果 set_display_name() 已设置，标题格式为：【角色名】标题
    end=True 时输出虚线收尾分隔。

    颜色规则：
      - 思考/推理 → 自动黄字
      - 回复/工具调用 → 自动恢复默认
    """
    from common.i18n import t, get_lang

    if _silent:
        return
    if end:
        set_stream_color(None)
        print(f"\n{' -' * (length // 2)}")
        return

    # 标题映射（中英文）
    title_map = {
        "回复": t("reply"),
        "工具调用": t("tool_call"),
        "推理过程": t("reasoning"),
        "思考": t("reasoning"),
        "时策回复": t("reply"),
    }
    display_title = title_map.get(title, title)

    # 根据标题自动切换流式颜色
    if title in ("回复", "工具调用"):
        set_stream_color("blue")
    elif title in ("推理过程", "思考"):
        set_stream_color("yellow")

    # ── 时策回复也加时间 ──
    if title in ("推理过程", "回复", "工具调用", "时策回复"):
        t_str = _dt.now().strftime("%H:%M:%S")
        display_title = f"{display_title} {t_str}"

    # 拼接标签
    label = f"【{_display_name}】{display_title}" if _display_name else display_title

    if not label:
        print(f"\n{separator * length}")
        return

    # 居中
    total_deco = max(0, length - len(label))
    left = separator * (total_deco // 2)
    right = separator * (total_deco - total_deco // 2)
    print(f"\n{left}{label}{right}")


def separator_to_terminal(separator: str = "—", length: int = 20, title: str = "") -> None:
    """轻量分隔线 — 用于菜单 / banner / 生命周期节点，不带角色名。"""
    half = length // 2 - len(title) // 2
    middle = f" {title} " if title else ""
    print(f"\n{separator * half}{middle}{separator * half}")


# ── 流式打印 ──

def stream_print(content: str, end: str = "", flush: bool = True, color: str | None = None) -> None:
    """逐字流式输出到终端。跳过空白泛滥内容（避免 MiniMax thinking→content 切换时空行泛滥），但保留单空格等有意义字符。"""
    if _silent:
        return
    if not content.strip():
        # 跳过纯空白行（>1 个字符），保留单个空格/换行等有意义内容
        if len(content) > 1:
            return
    use_color = color or _stream_color
    ansi_code = _COLORS.get(use_color, "") if use_color else ""
    try:
        if ansi_code:
            print(f"{ansi_code}{content}{_RESET}", end=end, flush=flush)
        else:
            print(content, end=end, flush=flush)
    except UnicodeEncodeError:
        # Windows GBK 终端无法打印 emoji / 生僻字 → 用 ? 替代，避免整行丢失
        enc = sys.stdout.encoding or "utf-8"
        safe = content.encode(enc, errors="replace").decode(enc)
        if ansi_code:
            print(f"{ansi_code}{safe}{_RESET}", end=end, flush=flush)
        else:
            print(safe, end=end, flush=flush)


def stream_newline() -> None:
    """流式输出收尾换行"""
    if _silent:
        return
    print()


# ── RoundOutput 渲染 ──

def render_round(output: RoundOutput, *, silent: bool = False,
        is_tool_round: bool = False) -> None:
    """把 ``RoundOutput`` 渲染到终端。终端交互由调用方（silent）控制。

    约定：
    - ``silent=True`` 时所有 print/header 全部跳过。
    - ``is_tool_round=True`` 时不输出"回复"标题（工具调用链中段）。
    - 推理段显示"推理过程"分隔线，正文段显示"回复"分隔线。
    - 只有当对应缓冲区非空时才输出分隔线，避免空段污染终端。
    """
    from common.i18n import tr_reasoning, tr_reply
    if silent:
        return
    if output.reasoning:
        set_stream_color('yellow')
        separate_print(title=tr_reasoning())
        stream_print(output.reasoning)
    if output.content:
        if not is_tool_round:
            separate_print(title=tr_reply())
        stream_print(output.content)


def emit_reasoning_header(silent: bool) -> None:
    from common.i18n import tr_reasoning
    if not silent:
        set_stream_color('yellow')
        separate_print(title=tr_reasoning())


def emit_reasoning(silent: bool, text: str) -> None:
    if text and not silent:
        stream_print(text)


def emit_content_header(silent: bool, is_tool_round: bool) -> None:
    from common.i18n import tr_reply
    if not silent and not is_tool_round:
        separate_print(title=tr_reply())


def emit_content(silent: bool, is_tool_round: bool, text: str) -> None:
    if not text:
        return
    emit_content_header(silent, is_tool_round)
    if not silent:
        stream_print(text)


# 兼容旧名字（presenter / common.utils / common.cli_style）
present_round = render_round

__all__ = [
    # 全局状态
    'set_stream_color', 'set_display_name', 'set_silent', 'get_silent',
    # 分隔线
    'separate_print', 'separator_to_terminal',
    # 流式打印
    'stream_print', 'stream_newline',
    # RoundOutput 渲染
    'render_round', 'present_round',
    'emit_reasoning_header', 'emit_reasoning',
    'emit_content_header', 'emit_content',
]