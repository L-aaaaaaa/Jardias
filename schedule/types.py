"""
schedule/types.py — 时策模块数据形状（从 Jardias0 data_shape/schedule.py 折入）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Schedule:
    """
    时策循环条目（Schedule Entry）。

    condition 字段是鸭子类型：任何有 is_met() 和 update() 方法的对象均可。
    """
    id: str
    name: str
    condition: Any  # 鸭子类型：is_met(schedule) -> bool, update(schedule) -> condition | None
    context: Any
    enabled: bool = True
    missed_policy: str = "fire_once"  # 简化：只保留 fire_once
    state: dict[str, Any] = field(default_factory=dict)
    playbook_ref: str | None = None


@dataclass
class ScheduleParams:
    """shice_schedule 工具的参数。"""
    message: str
    timestamps: list[int]  # 绝对时间戳（毫秒），LLM 直接计算
    character_id: str | None = None
