"""
yinao.py — yinao 模块数据形状（仅字段声明，零行为）。

合并来源：
- ipu.py         : IPUEntry / IPUProviderConfig / IPUConfigFile / AddIPURequest / UpdateIPURequest / RemoveIPURequest
- ipu_client.py  : IPUConfig / IPUProvider / ToolCall / RoundOutput / ChatResult / RoundMeta / IPUSwitch
- stream_state.py: LineDedupState / ReasoningExtractState / OutputState / RoundCollectState
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# IPU 注册表（ipu_config_manager 使用）
# ══════════════════════════════════════════════════════════════════════════════


class IPUEntry(BaseModel):
    """单个智能基元的注册条目（简称 → API ID + 能力标签）。"""
    id: str
    caps: list[str] = Field(default_factory=list)


class IPUProviderConfig(BaseModel):
    """一个供应商的完整配置。"""
    name: str
    api_key_env: str
    base_url: str
    ipus: dict[str, dict] = Field(default_factory=dict)


class IPUConfigFile(BaseModel):
    """整份 ipu_config.json 的类型化结构。"""
    version: int = 1
    providers: list[IPUProviderConfig] = Field(default_factory=list)


@dataclass
class AddIPURequest:
    provider_name: str
    short_name: str
    ipu_id: str
    caps: list[str] | None = None


@dataclass
class UpdateIPURequest:
    provider_name: str
    short_name: str
    new_ipu_id: str
    caps: list[str] | None = None


@dataclass
class RemoveIPURequest:
    provider_name: str
    short_name: str


# ══════════════════════════════════════════════════════════════════════════════
# IPU 客户端（ipu_client 使用）
# ══════════════════════════════════════════════════════════════════════════════


class IPUConfig(BaseModel):
    """单次 IPU 调用的客户端配置。"""
    ipu: str = "MiniMax-M2.7"
    base_url: str = "https://api.minimax.chat/v1"
    api_key: str = ""
    extra_body: dict = {}
    stream: bool = True
    tools: list = []
    tool_choice: str = "auto"
    max_icp: int = 2048  # 最大输出 ICP；调用层映射到 API max_tokens
    temperature: float = 1.0
    top_p: float = 0.95
    reasoning_effort: str = "high"
    thinking_enabled: bool = True

    class Config:
        extra = "allow"


class IPUProvider(BaseModel):
    """供应商连接信息（api_key 从环境变量读取）。"""
    api_key: str = os.getenv("MINIMAX_API_KEY")
    base_url: str = "https://api.minimax.chat.v1"


# ══════════════════════════════════════════════════════════════════════════════
# 对话输出
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class ToolCall:
    id: str = ''
    name: str = ''
    arguments: str = ''


@dataclass
class RoundOutput:
    reasoning: str
    content: str
    tool_calls: list[ToolCall]
    finish_reason: str | None = None
    usage: dict | None = None


@dataclass
class ChatResult:
    """一轮对话的结构化结果。should_switch 替代了 IPUSwitched 异常。"""
    messages: list[dict]
    should_switch: bool = False
    switch_provider: str = ""
    switch_ipu: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# 每轮元数据
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class RoundMeta:
    api_time: float = 0.0
    usage: dict | None = None
    finish_reason: str | None = None
    error: str | None = None


# ══════════════════════════════════════════════════════════════════════════════
# 智能基元切换
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class IPUSwitch:
    provider: str
    ipu: str


# ══════════════════════════════════════════════════════════════════════════════
# 流式收集器状态
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class LineDedupState:
    """行级去重状态。"""
    _seen: set[str] = field(default_factory=set)


@dataclass
class ReasoningExtractState:
    """推理内容提取状态（支持 DeepSeek / MiniMax）。"""
    reasoning_field: str = 'reasoning_details'
    _last: str = ''
    _parts: list[str] = field(default_factory=list)
    _produced: bool = False


@dataclass
class OutputState:
    """输出状态（用于控制 header 显示）。"""
    _reasoning_shown: bool = False
    _content_shown: bool = False


@dataclass
class RoundCollectState:
    """单轮收集器状态（替代 20+ 闭包变量）。"""
    reasoning_field: str = 'reasoning_details'
    is_tool_round: bool = False
    dedup: LineDedupState = field(default_factory=LineDedupState)
    extract_state: ReasoningExtractState = field(default_factory=ReasoningExtractState)
    output_state: OutputState = field(default_factory=OutputState)

    think_buffer: str = ''
    in_think: bool = False
    think_acc: str = ''

    reasoning_parts: list[str] = field(default_factory=list)
    content_parts: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    tool_args: list[str] = field(default_factory=list)

    _content_buffer: list[str] = field(default_factory=list)
    _waiting_reasoning: bool = field(init=False)
    _primary_source: Literal['field', 'think'] | None = None
    finish_reason: str | None = None
    usage: dict | None = None
    deltas: list = field(default_factory=list)

    def __post_init__(self):
        self._waiting_reasoning = self.reasoning_field == 'reasoning_details'
        self.extract_state.reasoning_field = self.reasoning_field
