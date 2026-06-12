"""
agent_config.py — agent_config 模块数据形状（仅字段声明，零行为）。
"""
from dataclasses import dataclass, field
from typing import Dict

from pydantic import BaseModel, Field


# ── 角色配置三层结构 ──

@dataclass
class RuntimeConfig:
    """智能体可以自由调整的运行时参数。"""
    provider: str = "minimax"
    model: str = "2.7"
    temperature: float = 1.0
    top_p: float = 0.95
    max_tokens: int = 8192
    thinking_mode: str = "auto"
    reasoning_effort: str = "high"
    thinking_enabled: bool = True


@dataclass
class IdentityConfig:
    """身份定义。"""
    system_prompt: str = "智能体项目测试助手。"
    title: str = ""   # 头衔，如"数据分析师"
    traits: str = ""  # 特质描述，如"擅长结构化报告"
    max_iterations: int = 10
    birth_time: str = ""


@dataclass
class AgentConfig:
    """一个角色的完整配置。"""
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


# ── Provider JSON5 配置 ──

class ModelEntry(BaseModel):
    id: str
    caps: list[str] = Field(default_factory=list)


class ProviderConfig(BaseModel):
    name: str
    api_key_env: str
    base_url: str
    models: Dict[str, dict] = Field(default_factory=dict)


class ConfigFile(BaseModel):
    version: int = 1
    providers: list[ProviderConfig] = Field(default_factory=list)
