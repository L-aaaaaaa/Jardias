"""
ipu_client — 多供应商智能基元客户端

按需延迟加载（避免循环导入）：
- 子模块（dashscope/deepseek/minimax）首次访问时才真正 import
- 顶层常用符号从 switch / common_client_util 暴露
"""
from __future__ import annotations

from . import dashscope, deepseek, minimax, switch, common_client_util  # noqa: F401

# 转发常用入口（避免上层 import 时找不到符号）
from .switch import (
    resolve_chat,
    sync_config_to_ipu,
    reload_after_switch,
    make_switch_note,
    _next_provider,
    _pick_fallback_ipu,
    _next_vision_provider,
)
from .common_client_util import (
    form_client,
    single_completion,
    form_stream,
    collect_round,
    replay_deltas,
    reason_action_loop,
)
from . import circuit_breaker  # noqa: F401  (含 CircuitBreaker / is_exhausted_error)
from .circuit_breaker import is_exhausted_error