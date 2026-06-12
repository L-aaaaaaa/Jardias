"""
data_shape — 数据形状层

集中管理所有数据模型，全项目共享。
每个根目录模块对应一个文件，__init__.py 统一出口。

目录:
  agent_config.py   — ModelEntry / ProviderConfig / ConfigFile
  character.py      — L1Summary
  model_client.py   — AIModelConfig / AIModelProvider / ToolCall / RoundOutput / ChatResult / RoundMeta / ModelSwitch
  tool.py           — ToolDef / ToolParam
"""
from .agent_config import AgentConfig, IdentityConfig, RuntimeConfig, ModelEntry, ProviderConfig, ConfigFile
from .character import L1Summary
from .model_client import AIModelConfig, AIModelProvider, ToolCall, RoundOutput, ChatResult, RoundMeta, ModelSwitch
from .tool import ToolDef, ToolParam
