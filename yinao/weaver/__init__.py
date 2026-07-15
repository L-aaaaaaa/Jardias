"""
weaver — 编制思绪

子模块：
  thought_weaver   — 思绪编织器：ReAct 编排入口
  chunk_normalizer — 纯解析的 chunk 规范化器
  tool_runner      — 工具执行调度
  circuit_breaker  — 熔断器：单实例 + 跨 provider 共享字典
  ipu_context      — 每轮运行状态 + 注入上下文构建
  icp_tracker      — 智点（ICP）累计 + 各供应商延迟
"""
from .circuit_breaker import (
    CircuitBreaker,
    is_exhausted_error,
    record_ipu_success,
    record_ipu_failure,
    is_provider_available,
    get_circuit_status,
)
from .thought_weaver import weave_thought, WEAVE_MAX_TURNS
from .chunk_normalizer import collect_stream, THINK_OPEN, THINK_CLOSE
from .tool_runner import (
    ToolRunner,
    log_tool_calls,
    display_tool_calls,
    log_tool_result,
    display_tool_result,
)
from experience import last_round, set_round_meta, build_round_context
from .icp_tracker import (
    cumulative_usage,
    provider_latency,
    update_cumulative,
)
