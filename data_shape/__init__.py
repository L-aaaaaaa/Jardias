"""
data_shape — 数据形状层

按模块划分，每个根目录模块对应一个文件：
  character.py — character/ 模块用（ActorConfig / RoleConfig / IPURuntime / L1Summary / TopicSegment）
  yinao.py     — yinao/ 模块用（IPUEntry / IPUProvider / IPUConfig / ToolCall / RoundOutput 等）
  tool.py      — tool/ 模块用（ToolDef / ToolParam / UpdateRuntimeArgs）
"""
from .character import (
    ActorConfig, RoleConfig, IPURuntime,
    L1Summary, TopicSegment,
)
from .yinao import (
    IPUEntry, IPUProviderConfig, IPUConfigFile,
    AddIPURequest, UpdateIPURequest, RemoveIPURequest,
    IPUConfig, IPUProvider,
    ToolCall, RoundOutput, ChatResult, RoundMeta, IPUSwitch,
    LineDedupState, ReasoningExtractState, OutputState, RoundCollectState,
)
from .tool import ToolDef, ToolParam, UpdateRuntimeArgs
