"""
agent_config.py — 已弃用（DEPRECATED）的旧兼容 shim。

本模块的内容已迁移至：
  - data_shape.yinao_config : ActorConfig / RoleConfig / IPURuntime
  - data_shape.ipu          : IPUEntry / IPUProviderConfig / IPUConfigFile

本文件仅保留为重导出 shim 以避免破坏既有 import 路径，
新代码请直接 import data_shape.* 或 data_shape.yinao_config.* / data_shape.ipu.*。
本文件将在后续清理阶段删除。
"""
from .yinao_config import ActorConfig, RoleConfig, IPURuntime
from .ipu import IPUEntry, IPUProviderConfig, IPUConfigFile

__all__ = [
    "ActorConfig",
    "RoleConfig",
    "IPURuntime",
    "IPUEntry",
    "IPUProviderConfig",
    "IPUConfigFile",
]