# Copyright 2026 Cazlor
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
    cumulative_usage,
    provider_latency,
    update_cumulative,
)
# 兼容旧的 ipu_client 导入路径
from yinao import launcher, weaver
from yinao.launcher import ipu_switch
from yinao.weaver import tool_runner, chunk_normalizer, thought_weaver, icp_tracker, circuit_breaker

import sys
# ipu_client 兼容层：让旧的 yinao.ipu_client.xxx 导入仍能工作
class _IpuClientCompat:
    """模拟已删除的 yinao.ipu_client 包。"""
    def __getattr__(self, name):
        if name == "resolve_chat":
            from yinao.launcher import resolve_chat as f; return f
        if name == "sync_config_to_ipu":
            from yinao.launcher import sync_config_to_ipu as f; return f
        if name == "reload_after_switch":
            from yinao.launcher import reload_after_switch as f; return f
        if name == "ipu_switch":
            from yinao.launcher import ipu_switch as m; return m
        if name == "circuit_breaker":
            from yinao.weaver import circuit_breaker as m; return m
        if name == "tool_runner":
            from yinao.weaver import tool_runner as m; return m
        if name == "chunk_normalizer":
            from yinao.weaver import chunk_normalizer as m; return m
        if name == "thought_weaver":
            from yinao.weaver import thought_weaver as m; return m
        if name == "icp_tracker":
            from yinao.weaver import icp_tracker as m; return m
        raise AttributeError(f"module 'yinao.ipu_client' has no attribute '{name}'")

sys.modules['yinao.ipu_client'] = _IpuClientCompat()
sys.modules['yinao.ipu_client.ipu_switch'] = launcher.ipu_switch
sys.modules['yinao.ipu_client.circuit_breaker'] = weaver.circuit_breaker
sys.modules['yinao.ipu_client.tool_runner'] = weaver.tool_runner
sys.modules['yinao.ipu_client.chunk_normalizer'] = weaver.chunk_normalizer
sys.modules['yinao.ipu_client.thought_weaver'] = weaver.thought_weaver
sys.modules['yinao.ipu_client.icp_tracker'] = weaver.icp_tracker
sys.modules['yinao.ipu_config_manager'] = launcher.ipu_config_manager
sys.modules['yinao.config_resolver'] = launcher.config_resolver
