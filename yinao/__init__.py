"""
yinao — 智能演员的义脑（策略层 + 智能基元调度）

子模块：
  ipu_resolver      — IPU 简称 → API ID 与能力映射
  provider_manager  — providers.json5 读写与热重载
  ipu_client        — 智能基元 HTTP 客户端封装
"""
from .ipu_resolver import (
    IPUVendor,
    DEFAULT_ROLE_PROMPT,
    IPU_REGISTRY,
    IPU_CAPS,
    get_ipu_capabilities,
    choose_ipu,
    choose_ipu_provider,
    resolve_ipu,
)