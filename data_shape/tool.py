"""
tool.py — 工具定义数据形状。
"""
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ToolParam:
    name: str
    description: str
    parameters: dict


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    fn: Callable = field(default=None, repr=False)
