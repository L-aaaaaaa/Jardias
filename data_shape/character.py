"""
character.py — character 模块数据形状（仅字段声明，零行为）。

合并来源：
- yinao_config.py : ActorConfig / RoleConfig / IPURuntime
- character.py    : L1Summary / TopicSegment
"""
from dataclasses import dataclass, field
from typing import Literal


# ══════════════════════════════════════════════════════════════════════════════
# 角色配置（Actor / Role / Runtime）
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class IPURuntime:
    """智能演员可以自由调整的运行时参数。"""
    provider: str = "minimax"
    ipu: str = "2.7"
    temperature: float = 1.0
    top_p: float = 0.95
    max_icp: int = 8192  # 最大输出 ICP（智点）；映射到 API 层 max_tokens
    thinking_mode: str = "auto"
    reasoning_effort: str = "high"
    thinking_enabled: bool = True


@dataclass
class RoleConfig:
    """身份定义。"""
    system_prompt: str = "智能体项目测试助手。"
    title: str = ""  # 头衔，如"数据分析师"
    traits: str = ""  # 特质描述，如"擅长结构化报告"
    max_iterations: int = 10
    birth_time: str = ""


@dataclass
class ActorConfig:
    """一个角色的完整配置。"""
    identity: RoleConfig = field(default_factory=RoleConfig)
    runtime: IPURuntime = field(default_factory=IPURuntime)


# ══════════════════════════════════════════════════════════════════════════════
# 角色摘要（L1Summary / TopicSegment）
# ══════════════════════════════════════════════════════════════════════════════


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
    time_ranges: list[list[str]] = field(default_factory=list)  # 归档的多区间
                              # 单段归档存一个 [(start,end)]；聚合归档存多个；
                              # backward compat: 旧数据无此字段、数组空、压缩时取整体 [start_time, end_time]
    range_msg_indices: list[list[int]] = field(default_factory=list)  # 每个区间的绝对消息索引
