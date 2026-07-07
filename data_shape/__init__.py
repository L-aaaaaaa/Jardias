"""
data_shape — 数据形状层

集中管理所有数据模型，全项目共享。
每个根目录模块对应一个文件，__init__.py 统一出口。

目录:
  yinao_config.py  — ActorConfig / RoleConfig / IPURuntime
  ipu.py           — IPUEntry / IPUProviderConfig / IPUConfigFile
  ipu_client.py    — IPUConfig / IPUProvider / ToolCall / RoundOutput / ChatResult / RoundMeta / IPUSwitch
  character.py     — L1Summary
  tool.py          — ToolDef / ToolParam
"""
from .yinao_config import ActorConfig, RoleConfig, IPURuntime
from .ipu import IPUEntry, IPUProviderConfig, IPUConfigFile
from .ipu_client import (
    IPUConfig, IPUProvider, ToolCall, RoundOutput, ChatResult, RoundMeta, IPUSwitch,
)
from .character import L1Summary, TopicSegment
from .tool import ToolDef, ToolParam