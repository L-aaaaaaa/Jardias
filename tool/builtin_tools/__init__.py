"""builtin_tools — builtin.py 内工具实现的分类拆分。

每个分类子模块导出自己的 ``HANDLERS`` 字典；``builtin.py`` 集中合并到
``_BUILTIN_HANDLERS``。所有模块都依赖 ``builtin.py`` 暴露的
``_current_actor / _format_error``（调度层）以及对应的私有 helper，
因此工具函数仍按"接收 arguments dict"签名实现，保持 dispatch 路径稳定。

子模块通过 ``builtin.py`` 顶层显式 ``from tool.builtin_tools.X import HANDLERS``
合并访问；本 ``__init__`` 只放包级说明，避免包加载时触发子模块级 import
（子模块的函数体内对 ``tool.builtin`` 的延迟 import 需要 builtin.py 已加载完）。
"""
from __future__ import annotations

__all__ = ["characters", "config", "context", "files", "shice", "web"]
