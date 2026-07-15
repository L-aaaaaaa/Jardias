"""
ipu_client — 兼容层

此模块已重构为 yinao.launcher 和 yinao.weaver
此处保留以兼容旧导入路径
"""
from yinao.launcher import (
    resolve_chat,
    sync_config_to_ipu,
    reload_after_switch,
    inform_ipu_switch,
    format_engine_switch_log,
    next_provider,
    pick_fallback_ipu,
    next_vision_provider,
    get_ipu_stream_reply,
    get_ipu_reply,
    form_client,
    ipu_switch as ipu_switch_module,
    ipu_config_manager,
    config_resolver,
    reply_getter,
)
from yinao.weaver import (
    weave_thought,
    WEAVE_MAX_TURNS,
    collect_stream,
    THINK_OPEN,
    THINK_CLOSE,
    ToolRunner,
    log_tool_calls,
    display_tool_calls,
    log_tool_result,
    display_tool_result,
    last_round,
    set_round_meta,
    build_round_context,
    cumulative_usage,
    provider_latency,
    update_cumulative,
    is_exhausted_error,
    record_ipu_success,
    record_ipu_failure,
    is_provider_available,
    get_circuit_status,
    CircuitBreaker,
    circuit_breaker as circuit_breaker_module,
    ipu_context as ipu_context_module,
    tool_runner as tool_runner_module,
    chunk_normalizer as chunk_normalizer_module,
    thought_weaver as thought_weaver_module,
    icp_tracker as icp_tracker_module,
)

# 兼容子模块导入
import yinao.launcher.ipu_switch as ipu_switch
import yinao.launcher.ipu_config_manager as ipu_config_manager
import yinao.launcher.config_resolver as config_resolver
import yinao.launcher.reply_getter as reply_getter
import yinao.weaver.circuit_breaker as circuit_breaker
import yinao.weaver.ipu_context as ipu_context
import yinao.weaver.tool_runner as tool_runner
import yinao.weaver.chunk_normalizer as chunk_normalizer
import yinao.weaver.thought_weaver as thought_weaver
import yinao.weaver.icp_tracker as icp_tracker
