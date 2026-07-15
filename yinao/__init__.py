"""
yinao — 智能演员的义脑（策略层 + 智能基元调度）

子模块：
  launcher — 构建发射器
    ipu_config_manager — ipu_config.json 读写与热重载
    config_resolver     — IPU 简称 → API ID 与能力映射
    ipu_switch         — 供应商差异配置与统一对话生成入口
    reply_getter       — OpenAI 客户端与流式请求构造

  weaver — 编制思绪
    thought_weaver   — 思绪编织器：ReAct 编排入口
    chunk_normalizer — 纯解析的 chunk 规范化器
    tool_runner      — 工具执行调度
    circuit_breaker  — 熔断器
    ipu_context      — 每轮运行状态 + 注入上下文构建
    icp_tracker      — 智点（ICP）累计 + 各供应商延迟
"""
# 兼容层：从子模块转发所有公开 API
from yinao.launcher import (
    IPUVendor,
    DEFAULT_ROLE_PROMPT,
    IPU_REGISTRY,
    IPU_CAPS,
    get_ipu_capabilities,
    choose_ipu,
    choose_ipu_provider,
    resolve_ipu,
    resolve_ipu_provider,
    list_ipu_providers,
    list_ipus,
    resolve_chat,
    sync_config_to_ipu,
    reload_after_switch,
    inform_ipu_switch,
    format_engine_switch_log,
    next_provider,
    pick_fallback_ipu,
    next_vision_provider,
    IPUSwitched,
    switch_request,
    set_active_ipu,
    get_active_ipu,
    request_switch,
    pop_switch,
    get_ipu_stream_reply,
    get_ipu_reply,
    form_client,
)
from yinao.weaver import (
    CircuitBreaker,
    is_exhausted_error,
    record_ipu_success,
    record_ipu_failure,
    is_provider_available,
    get_circuit_status,
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
)
# 兼容旧的 ipu_client 导入路径
from yinao import launcher, weaver
