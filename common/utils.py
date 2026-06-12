"""
utils.py — 项目全局可复用工具
"""
import sys
from typing import Final

# ════════════════════════════════════════════════════════════════

_display_name: str | None = None
_silent: bool = False


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


def separate_print(separator: str = "─", title: str = "", length: int = 64,
                   end: bool = False) -> None:
    """
    流式输出分隔线 — 用户视角直出，不经 logger。

    如果 set_display_name() 已设置，标题格式为：【角色名】标题
    end=True 时输出虚线收尾分隔。

    示例:
        ——————【小华】思考——————
        - - - - - - - - - - -
    """
    if _silent:
        return
    if end:
        print(f"\n{' -' * (length // 2)}\n")
        return

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


def stream_print(content: str, end: str = "", flush: bool = True) -> None:
    """逐字流式输出到终端"""
    if _silent:
        return
    try:
        print(content, end=end, flush=flush)
    except UnicodeEncodeError:
        # Windows GBK 终端无法打印 emoji / 生僻字 → 用 ? 替代，避免整行丢失
        enc = sys.stdout.encoding or "utf-8"
        safe = content.encode(enc, errors="replace").decode(enc)
        print(safe, end=end, flush=flush)


def stream_newline() -> None:
    """流式输出收尾换行"""
    if _silent:
        return
    print()
