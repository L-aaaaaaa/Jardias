"""
ipu_client.py — 智能基元（IPU）客户端与对话结果数据形状。
"""
import os
from dataclasses import dataclass

from pydantic import BaseModel


# ── IPU 客户端配置 ──

class IPUConfig(BaseModel):
    """单次 IPU 调用的客户端配置（IPURuntime 在调用层映射到此结构）。"""
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


# ── 供应商 ──

class IPUProvider(BaseModel):
    """供应商连接信息（api_key 从环境变量读取）。"""
    api_key: str = os.getenv("MINIMAX_API_KEY")
    base_url: str = "https://api.minimax.chat.v1"


# ── 对话输出 ──

@dataclass
class ToolCall:
    name: str
    arguments: str


@dataclass
class RoundOutput:
    reasoning: str
    content: str
    tool_calls: list[ToolCall]
    deltas: list  # 所有 delta，供重放用
    finish_reason: str | None = None
    usage: dict | None = None


@dataclass
class ChatResult:
    """一轮对话的结构化结果。should_switch 替代了 IPUSwitched 异常。"""
    messages: list[dict]
    should_switch: bool = False
    switch_provider: str = ""
    switch_ipu: str = ""


# ── 每轮元数据 ──

@dataclass
class RoundMeta:
    api_time: float = 0.0
    usage: dict | None = None
    finish_reason: str | None = None
    error: str | None = None


# ── 智能基元切换 ──

@dataclass
class IPUSwitch:
    provider: str
    ipu: str