"""终端输出样式工具。"""
from __future__ import annotations


def separator_to_terminal(separator: str = "—", length: int = 20, title: str = "") -> None:
    half = length // 2 - len(title) // 2
    middle = f" {title} " if title else ""
    print(f"\n{separator * half}{middle}{separator * half}")
