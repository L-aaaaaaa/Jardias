"""
launcher — 构建发射器

子模块：
  ipu_config_manager — ipu_config.json 读写与热重载
  config_resolver     — IPU 简称 → API ID 与能力映射
  ipu_switch         — 供应商差异配置与统一对话生成入口
  reply_getter       — OpenAI 客户端与流式请求构造
"""
from .config_resolver import (
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
)
from .ipu_switch import (
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
)
from .reply_getter import get_ipu_stream_reply, get_ipu_reply, form_client
