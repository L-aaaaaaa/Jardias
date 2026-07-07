"""
character.py — character 模块数据形状（仅字段声明，零行为）。
"""
from dataclasses import dataclass, field


@dataclass
class TopicSegment:
    """单个话题片段：记录原始消息在 history.json 中的位置和内容。"""
    from_msg_idx: int          # 起始消息在 history.json 中的绝对索引
    to_msg_idx: int            # 终止消息在 history.json 中的绝对索引
    topic: str                 # 本片段的子话题（自动推断）
    detail: str                # 本片段的认知摘要
    key_points: list[str] = field(default_factory=list)  # 关键观点列表


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

    # ── 话题归档扩展字段 ──
    topic_label: str = ""      # 话题标签（用户/角色指定，如"价值本质讨论"）
    people: list[str] = field(default_factory=list)   # 关联人物列表
    msg_indices: tuple[int, int] = (0, 0)  # (from_msg_idx, to_msg_idx) 原始消息范围
    source: str = "auto"      # "auto"=阈值触发压缩  "manual"=用户主动归档
