"""
ipu_client — 多供应商智能基元客户端

按需延迟加载（避免循环导入）：
- 子模块首次访问时才真正 import
- 顶层常用符号从 switch / thought_weaver / chunk_normalizer / presenter 暴露
"""
from __future__ import annotations

from . import circuit_breaker  # noqa: F401  (含 CircuitBreaker / is_exhausted_error)
from . import ipu_switch, thought_weaver  # noqa: F401
from .circuit_breaker import is_exhausted_error
from .thought_weaver import weave_thought, WEAVE_MAX_TURNS
from .chunk_normalizer import collect_stream, THINK_OPEN, THINK_CLOSE
# 转发常用入口（避免上层 import 时找不到符号）
from .ipu_switch import (
    resolve_chat,
    sync_config_to_ipu,
    reload_after_switch,
    inform_ipu_switch,
    format_engine_switch_log,
    next_provider,
    pick_fallback_ipu,
    next_vision_provider,
)
from .reply_getter import get_ipu_stream_reply
