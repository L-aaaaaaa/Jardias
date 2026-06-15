"""
schedule/shice.py — 时策调度器：时间戳队列模型。

核心抽象：
- 一个 job = 一个 LLM 预计算的时间戳列表
- 每个触发时间对应一个独立的 Schedule（共享 job_id）
- 触发后注入 system_trigger 消息到角色历史，LLM 自然响应
- 错过补偿：过期 ≤ 60s 仍旧触发，标记延迟秒数让 LLM 感知

设计原则：
- scheduler 不知道 LLM，纯时间队列管理
- 时间戳队列存储在每个 Schedule 的 state 中
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Callable, Awaitable

from common.logger import logger

from .repository import ScheduleRepository
from .strategies import DelayCondition, wall_ms
from .types import Schedule

# ── 类型 ──

OnJobFireCallback = Callable[["JobFireContext"], Awaitable[None]]


class JobFireContext:
    """on_job_fire 回调的上下文。"""

    def __init__(
        self,
        job_id: str,
        fire_index: int,
        timestamps: list[int],
        character_id: str,
        message: str,
        late_sec: float,
    ):
        self.job_id = job_id
        self.fire_index = fire_index
        self.timestamps = timestamps
        self.character_id = character_id
        self.message = message
        self.late_sec = late_sec  # 延迟秒数（0 = 准时）

    @property
    def remaining_count(self) -> int:
        return len(self.timestamps) - self.fire_index - 1

    def format_trigger(self) -> str:
        """格式化 system_trigger 消息。"""
        if self.late_sec > 0:
            return f"[时策任务 | 已延迟 {self.late_sec:.0f} 秒]\n{self.message}"
        return f"[时策任务]\n{self.message}"


# ── 辅助 ──

def _fmt_time(ts_ms: int) -> str:
    import time as _t
    lt = _t.localtime(ts_ms / 1000.0)
    return _t.strftime("%H:%M:%S", lt) + f".{ts_ms % 1000:03d}"


# ── TemporalScheduler ──

class TemporalScheduler:
    """
    时策调度器：基于时间戳队列模型。

    核心流程:
      LLM 计算绝对时间戳列表 → add_recurring()
      → 调度器到期触发 → on_job_fire 回调
      → 调用方注入 system_trigger → LLM 响应

    错过处理:
      过期 ≤ 60s → 立即触发，标记 late_sec
      过期 > 60s → 丢弃（rehydrate 时处理）
    """

    def __init__(
        self,
        store_path: str | Path,
        on_job_fire: OnJobFireCallback | None = None,
        concurrency: int = 3,
    ):
        self._store_path = Path(store_path)
        self._repo = ScheduleRepository(self._store_path)
        self._on_job_fire = on_job_fire
        self._concurrency = asyncio.Semaphore(concurrency)
        self._running = False
        self._timer_handle: asyncio.TimerHandle | None = None
        self._timer_seq: int = 0
        self._job_meta: dict[str, dict] = {}

    # ── 生命周期 ──

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._rehydrate()
        self._rearm_timer()
        logger.info(f"[时策] 调度器已启动 | 存储: {self._store_path}")

    def stop(self) -> None:
        self._running = False
        if self._timer_handle:
            self._timer_handle.cancel()
            self._timer_handle = None

    def _rehydrate(self) -> None:
        """从磁盘恢复：重建 condition，丢弃严重过期任务。"""
        to_remove = []
        for s in self._repo.list():
            if s.condition is None:
                ts_list = s.state.get("_timestamps", [])
                idx = s.state.get("_next_index", 0)
                if idx < len(ts_list):
                    next_ms = ts_list[idx]
                    now = wall_ms()
                    delay_ms = now - next_ms
                    if delay_ms > 60_000:
                        logger.info(f"[时策] 丢弃过期任务: {s.name} (延迟 {delay_ms / 1000:.1f}s)")
                        to_remove.append(s.id)
                        continue
                    remaining = max(1, next_ms - now)
                    cond = DelayCondition(delay_ms=remaining)
                    cond._start_ms = next_ms - remaining
                    cond._next_ms = next_ms
                    updated = Schedule(
                        id=s.id, name=s.name,
                        condition=cond, context=s.context,
                        enabled=s.enabled, missed_policy=s.missed_policy,
                        state=dict(s.state), playbook_ref=s.playbook_ref,
                    )
                    self._repo.save(updated)
        for sid in to_remove:
            self._repo.remove(sid)

    # ── Job 管理 API ──

    def add_recurring(
        self, name: str, message: str, timestamps: list[int],
        character_id: str,
    ) -> str:
        """创建批量定时任务。返回 job_id。"""
        if not timestamps:
            return ""
        timestamps = sorted(set(timestamps))
        job_id = str(uuid.uuid4())[:8]

        self._job_meta[job_id] = {
            "name": name, "character_id": character_id,
            "message": message, "timestamps": timestamps,
        }

        base_state = {
            "_job_id": job_id, "_timestamps": timestamps,
            "character_id": character_id, "message": message,
        }
        self._create_schedule_at_index(job_id, name, base_state, timestamps, 0)
        return job_id

    def remove_remaining(self, job_id: str) -> bool:
        schedules = self._get_job_schedules(job_id)
        for s in schedules:
            self._repo.remove(s.id)
        self._job_meta.pop(job_id, None)
        if self._running:
            self._rearm_timer()
        return len(schedules) > 0

    def list_jobs(self) -> list[dict]:
        seen = set()
        results = []
        for s in self._repo.list():
            jid = s.state.get("_job_id")
            if jid and jid not in seen:
                seen.add(jid)
                ts = s.state.get("_timestamps", [])
                idx = s.state.get("_next_index", 0)
                results.append({
                    "job_id": jid, "name": s.name,
                    "message": s.state.get("message", ""),
                    "total": len(ts), "fired": idx + 1,
                    "remaining": len(ts) - idx - 1,
                })
        return results

    # ── 定时器 ──

    def _rearm_timer(self) -> None:
        if self._timer_handle:
            self._timer_handle.cancel()
            self._timer_handle = None
        if not self._running:
            return
        next_wake = self._get_next_wake_ms()
        if next_wake is None:
            return
        now = wall_ms()
        delay_s = max(0.0, (next_wake - now) / 1000.0)
        self._timer_seq += 1
        seq = self._timer_seq
        logger.info(f"[时策] rearm: wake={_fmt_time(next_wake)}, delay={delay_s:.3f}s, seq={seq}")
        loop = asyncio.get_running_loop()

        def fire_and_rearm():
            if self._running and seq == self._timer_seq:
                asyncio.ensure_future(self._on_timer())

        self._timer_handle = loop.call_later(delay_s, fire_and_rearm)

    def _get_next_wake_ms(self) -> int | None:
        times = []
        for s in self._repo.list():
            if not s.enabled or s.condition is None:
                continue
            n = s.state.get("next_run_ms")
            if n is not None:
                times.append(n)
        return min(times) if times else None

    # ── 时策循环 ──

    async def _on_timer(self) -> None:
        if not self._running:
            return
        now = wall_ms()
        due = [s for s in self._repo.list()
               if s.enabled and s.condition is not None
               and now >= s.state.get("next_run_ms", now + 1) - 50]

        due.sort(key=lambda s: s.state.get("next_run_ms", 0))
        for s in due:
            await self._fire(s)

        self._repo._persist()
        self._rearm_timer()

    async def _fire(self, schedule: Schedule) -> None:
        job_id = schedule.state.get("_job_id", "")
        timestamps = schedule.state.get("_timestamps", [])
        idx = schedule.state.get("_next_index", 0)
        if not timestamps or idx >= len(timestamps):
            self._repo.remove(schedule.id)
            return

        expected_ms = timestamps[idx]
        late_sec = max(0.0, (wall_ms() - expected_ms) / 1000.0)

        ctx = JobFireContext(
            job_id=job_id, fire_index=idx, timestamps=timestamps,
            character_id=schedule.state.get("character_id", "default"),
            message=schedule.state.get("message", ""),
            late_sec=late_sec,
        )

        async with self._concurrency:
            if self._on_job_fire:
                try:
                    await self._on_job_fire(ctx)
                except Exception as e:
                    logger.error(f"[时策] on_job_fire 异常: {e}")

        self._advance_or_terminate(job_id)

    def _advance_or_terminate(self, job_id: str) -> None:
        """推进到下一个时间戳，或队列耗尽时终止。"""
        schedules = self._get_job_schedules(job_id)
        if not schedules:
            return
        s0 = schedules[0]
        ts = s0.state.get("_timestamps", [])
        idx = s0.state.get("_next_index", 0)
        for s in schedules:
            self._repo.remove(s.id)

        now = wall_ms()
        next_idx = None
        for i in range(idx + 1, len(ts)):
            if ts[i] > now:
                next_idx = i
                break

        if next_idx is not None:
            self._create_schedule_at_index(
                job_id, s0.name,
                {"_job_id": job_id, "_timestamps": ts,
                 "character_id": s0.state.get("character_id", "default"),
                 "message": s0.state.get("message", "")},
                ts, next_idx,
            )
        else:
            self._job_meta.pop(job_id, None)

        self._repo._persist()

    # ── 辅助 ──

    def _get_job_schedules(self, job_id: str) -> list[Schedule]:
        return sorted(
            [s for s in self._repo.list() if s.state.get("_job_id") == job_id],
            key=lambda s: s.state.get("_next_index", 0),
        )

    def _create_schedule_at_index(
        self, job_id: str, name: str, base_state: dict,
        timestamps: list[int], index: int,
    ) -> Schedule | None:
        if index >= len(timestamps):
            return None
        next_ms = timestamps[index]
        now = wall_ms()
        remaining = max(1, next_ms - now)
        cond = DelayCondition(delay_ms=remaining)
        cond._start_ms = next_ms - remaining
        cond._next_ms = next_ms

        state = dict(base_state)
        state["_next_index"] = index
        state["next_run_ms"] = next_ms

        logger.info(f"[时策] 注册: idx={index}, due={_fmt_time(next_ms)}, "
                    f"delay={remaining / 1000:.3f}s")

        s = Schedule(
            id=str(uuid.uuid4())[:8], name=name,
            condition=cond, context=None,
            enabled=True, missed_policy="fire_once",
            state=state,
        )
        self._repo.add(s)
        if self._running:
            self._rearm_timer()
        return s
