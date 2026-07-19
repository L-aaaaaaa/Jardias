"""
schedule/strategies.py — 时间策略：DelayCondition + 时钟工具。
"""
import time as _time


def wall_ms() -> int:
    """当前墙上时间（毫秒）。"""
    return int(_time.time() * 1000)


def now_ms() -> int:
    """当前时间（毫秒），同 wall_ms。"""
    return wall_ms()


class DelayCondition:
    """纯时间条件：延迟 delay_ms 后满足。"""

    def __init__(self, delay_ms: int):
        self._delay_ms = delay_ms
        self._start_ms: int | None = None
        self._next_ms: int | None = None

    def is_met(self, state: dict) -> bool:
        existing_next = state.get("next_run_ms")
        if self._start_ms is None:
            if existing_next is not None:
                self._start_ms = existing_next - self._delay_ms
            else:
                self._start_ms = wall_ms()
                state["next_run_ms"] = self._start_ms + self._delay_ms
        target = state.get("next_run_ms")
        return wall_ms() >= target

    def update(self, state: dict) -> None:
        """单次触发后不续期，返回 None 表示任务结束。"""
        return None
