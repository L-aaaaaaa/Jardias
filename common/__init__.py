"""
common — 核心基础设施。

模块层 lazy 暴露 bootstrap / conversation_loop，避免顶层 import 时
拖动整条 yinao.ipu_client 链造成循环。
"""
from __future__ import annotations


def __getattr__(name):
    if name == "bootstrap":
        from .bootstrap import bootstrap
        return bootstrap
    if name == "conversation_loop":
        from .lifecycle import conversation_loop
        return conversation_loop
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")