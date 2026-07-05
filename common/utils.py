"""
utils.py — 项目全局可复用工具
"""
import sys
from datetime import datetime as _dt

# ════════════════════════════════════════════════════════════════

_display_name: str | None = None
_silent: bool = False

# ── ANSI 颜色 ──
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_RESET = "\033[0m"
_COLORS = {"yellow": _YELLOW, "blue": _BLUE}

_stream_color: str | None = None  # 全局流式颜色标记


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
    if _silent:
        return
    if end:
        set_stream_color(None)
        print(f"\n{' -' * (length // 2)}\n")
        return

    # 根据标题自动切换流式颜色
    if title in ("回复", "工具调用"):
        set_stream_color("blue")
    elif title in ("推理过程", "思考"):
        set_stream_color("yellow")

    # ── 时策回复也加时间 ──
    if title in ("推理过程", "回复", "工具调用", "时策回复"):
        t = _dt.now().strftime("%H:%M:%S")
        title = f"{title} {t}"

    # 拼接标签
    label = f"【{_display_name}】{title}" if _display_name else title

    if not label:
        print(f"\n{separator * length}")
        return

    # 居中
    total_deco = max(0, length - len(label))
    left = separator * (total_deco // 2)
    right = separator * (total_deco - total_deco // 2)
    print(f"\n{left}{label}{right}")


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
