"""
character.py — character 模块数据形状（仅字段声明，零行为）。
"""
from dataclasses import dataclass, field


@dataclass
class L1Summary:
    id: str
    start_time: str = ""
    end_time: str = ""
    message_count: int = 0
    user_turns: int = 0
    topic: str = ""
    detail: str = ""
    key_events: list[str] = field(default_factory=list)
    summary: list[dict] = field(default_factory=list)
