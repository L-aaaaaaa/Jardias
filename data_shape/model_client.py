"""
model_client.py — model_client 模块数据形状。
"""
import os
from dataclasses import dataclass

from pydantic import BaseModel


# ── LLM 客户端配置 ──

class AIModelConfig(BaseModel):
    model: str = "MiniMax-M2.7"
    base_url: str = "https://api.minimax.chat/v1"
    api_key: str = ""
    extra_body: dict = {}
    stream: bool = True
    tools: list = []
    tool_choice: str = "auto"
    max_completion_tokens: int = 2048
    temperature: float = 1.0
    top_p: float = 0.95
    reasoning_effort: str = "high"
    thinking_enabled: bool = True

    class Config:
        extra = "allow"


# ── 供应商 ──

class AIModelProvider(BaseModel):
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
    """一轮对话的结构化结果。should_switch 替代了 ModelSwitched 异常。"""
    messages: list[dict]
    should_switch: bool = False
    switch_provider: str = ""
    switch_model: str = ""


# ── 每轮元数据 ──

@dataclass
class RoundMeta:
    api_time: float = 0.0
    usage: dict | None = None
    finish_reason: str | None = None
    error: str | None = None


# ── 模型切换 ──

@dataclass
class ModelSwitch:
    provider: str
    model: str
