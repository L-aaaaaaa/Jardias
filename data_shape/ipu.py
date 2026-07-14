"""
ipu.py — 智能基元（IPU）数据形状

智能基元（Intelligence Primitive Unit, IPU）：以权重等参数操作为核心方法，
将输入编码解码为智能输出的运算载体。本模块仅声明字段，零行为。
"""
from dataclasses import dataclass
from typing import Dict
from typing import Optional

from pydantic import BaseModel, Field


class IPUEntry(BaseModel):
    """单个智能基元的注册条目（简称 → API ID + 能力标签）。"""
    id: str
    caps: list[str] = Field(default_factory=list)


class IPUProviderConfig(BaseModel):
    """一个供应商的完整配置。"""
    name: str
    api_key_env: str
    base_url: str
    ipus: Dict[str, dict] = Field(default_factory=dict)


class IPUConfigFile(BaseModel):
    """整份 providers.json 的类型化结构。"""
    version: int = 1
    providers: list[IPUProviderConfig] = Field(default_factory=list)


@dataclass
class AddIPURequest:
    provider_name: str
    short_name: str
    ipu_id: str
    caps: Optional[list[str]] = None


@dataclass
class UpdateIPURequest:
    provider_name: str
    short_name: str
    new_ipu_id: str
    caps: Optional[list[str]] = None


@dataclass
class RemoveIPURequest:
    provider_name: str
    short_name: str
