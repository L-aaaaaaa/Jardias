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
        skipped_indices: list[int] | None = None,
    ):
        self.job_id = job_id
        self.fire_index = fire_index
        self.timestamps = timestamps
        self.character_id = character_id
        self.message = message
        self.late_sec = late_sec  # 延迟秒数（0 = 准时）
        self.skipped_indices = skipped_indices or []

    @property
    def remaining_count(self) -> int:
        return len(self.timestamps) - self.fire_index - 1

    def format_trigger(self) -> str:
        """格式化 system_trigger 消息（稳定结构前缀，供 LLM 感知）。

        message 模板支持 {pos}/{total}/{remaining} 占位符，触发时自动替换。
        不含占位符时，直接拼接在 header 之后。
        """
        total = len(self.timestamps)
        pos = self.fire_index + 1
        remaining = self.remaining_count
        skipped_count = len(self.skipped_indices)

        parts = [f"[时策任务 | 第 {pos}/{total} 个"]
        if self.late_sec > 0:
            parts.append(f" | 延迟 {self.late_sec:.0f}s")
        if self.skipped_indices:
            nums = "#" + ", #".join(str(i + 1) for i in self.skipped_indices)
            parts.append(f" | 错过: {nums}（共 {skipped_count} 个）")
        parts.append(f" | 剩余 {remaining}")
        parts.append("]")

        header = "".join(parts)

        # 动态注入占位符（pos/total/remaining 映射到任务序列位置）
        filled = self.message
        if "{pos}" in filled or "{total}" in filled or "{remaining}" in filled:
            filled = filled.replace("{pos}", str(pos)).replace("{total}", str(total)).replace("{remaining}", str(remaining))

        return header + "\n" + filled


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
        """创建批量定时任务。返回 job_id。

        如果新时间戳与同一角色的已有 job 时间戳集合有重叠，
        将重叠部分合并到已有 job 的 _timestamps 中，
        让同一 job 统一管理所有时间点。
        """
        if not timestamps:
            return ""
        timestamps = sorted(set(timestamps))

        # 查找同一角色已有的 job
        existing_job_id: str | None = None
        existing_schedules = []
        for s in self._repo.list():
            if s.state.get("character_id") == character_id and s.state.get("_job_id"):
                existing_job_id = s.state["_job_id"]
                existing_schedules.append(s)
                break  # 取任意一个即可（同一个 job_id 的所有 schedule 在 _get_job_schedules 里获取）

        if existing_job_id and existing_schedules:
            existing_ts = set(existing_schedules[0].state.get("_timestamps", []))
            new_ts = [t for t in timestamps if t not in existing_ts]

            s0 = existing_schedules[0]
            existing_next_idx = s0.state.get("_next_index", 0)

            # 计算新时间戳集合的起始位置
            all_ts = sorted(existing_ts | set(new_ts))
            new_start_idx = 0
            now = wall_ms()
            for i, t in enumerate(all_ts):
                if t > now:
                    new_start_idx = i
                    break
            else:
                new_start_idx = len(all_ts) - 1

            # 如果新时间戳已全部在已有集合中，或已有 schedule 已推进到
            # 新时间戳的起始位置之后，则不需要合并。
            if not new_ts or existing_next_idx > new_start_idx:
                logger.info(f"[时策] 跳过合并（已有 schedule 已覆盖）: "
                            f"existing_idx={existing_next_idx}, new_start={new_start_idx}")
                return existing_job_id

            # 合并时间戳，删除旧 schedule，从第一个未触发的新时间点开始调度
            merged = sorted(existing_ts | set(new_ts))
            for s in self._get_job_schedules(existing_job_id):
                self._repo.remove(s.id)
            self._job_meta[existing_job_id] = {
                "name": s0.name,
                "character_id": character_id,
                "message": message,
                "timestamps": merged,
            }
            base_state = {
                "_job_id": existing_job_id,
                "_timestamps": merged,
                "character_id": character_id,
                "message": message,
                "_total_fired": s0.state.get("_total_fired", 0) or 0,
                "_skipped_indices": s0.state.get("_skipped_indices", []) or [],
            }
            self._create_schedule_at_index(
                existing_job_id, s0.name, base_state, merged, new_start_idx
            )
            if self._running:
                self._rearm_timer()
            logger.info(f"[时策] 合并到已有 job {existing_job_id}: "
                        f"新增 {len(new_ts)} 个，合并后 {len(merged)} 个，"
                        f"从 idx={new_start_idx} 开始")
            return existing_job_id

        # 无重叠，创建独立 job
        job_id = str(uuid.uuid4())[:8]
        self._job_meta[job_id] = {
            "name": name, "character_id": character_id,
            "message": message, "timestamps": timestamps,
        }
        base_state = {
            "_job_id": job_id, "_timestamps": timestamps,
            "character_id": character_id, "message": message,
            "_total_fired": 0,
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
                loop.call_soon(asyncio.ensure_future, self._on_timer())

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
        now = wall_ms()
        late_sec = max(0.0, (now - expected_ms) / 1000.0)

        skipped_indices = schedule.state.get("_skipped_indices", []) or []

        ctx = JobFireContext(
            job_id=job_id, fire_index=idx, timestamps=timestamps,
            character_id=schedule.state.get("character_id", "default"),
            message=schedule.state.get("message", ""),
            late_sec=late_sec,
            skipped_indices=skipped_indices,
        )

        async with self._concurrency:
            if self._on_job_fire:
                try:
                    await self._on_job_fire(ctx)
                except Exception as e:
                    logger.error(f"[时策] on_job_fire 异常: {e}")

        await self._advance_or_terminate(job_id)

    async def _advance_or_terminate(self, job_id: str) -> None:
        """推进到下一个未过期时间戳（跳过已过期的则内联触发），或队列耗尽时终止。"""
        schedules = self._get_job_schedules(job_id)
        if not schedules:
            return
        s0 = schedules[0]
        ts = s0.state.get("_timestamps", [])
        idx = s0.state.get("_next_index", 0)
        for s in schedules:
            self._repo.remove(s.id)

        now = wall_ms()
        total_fired = (s0.state.get("_total_fired", 0) or 0) + 1  # 本次 fire 完成，+1

        # ── 正常推进 ──
        next_idx = None
        for i in range(idx + 1, len(ts)):
            if ts[i] > now:
                next_idx = i
                break

        if next_idx is not None:
            skipped = list(range(idx + 1, next_idx))

            base_state = {
                "_job_id": job_id, "_timestamps": ts,
                "character_id": s0.state.get("character_id", "default"),
                "message": s0.state.get("message", ""),
                "_skipped_indices": skipped,
                "_total_fired": total_fired,
            }
            self._create_schedule_at_index(job_id, s0.name, base_state, ts, next_idx)

            # 如果新 schedule 的时间戳已经过期，不走定时器，在当前帧内联触发
            if ts[next_idx] <= now:
                new_schedules = self._get_job_schedules(job_id)
                if new_schedules:
                    await self._fire(new_schedules[0])
                    return  # _fire 内部已递归推进/终止，不需要额外清理
        else:
            # 全部时间戳已过期：把最后一个任务连带所有跳过的索引一起触发
            # AI 看到后一次性补发全部
            last_idx = len(ts) - 1
            if idx < last_idx:
                all_skipped = list(range(idx + 1, last_idx))
                base_state = {
                    "_job_id": job_id, "_timestamps": ts,
                    "character_id": s0.state.get("character_id", "default"),
                    "message": s0.state.get("message", ""),
                    "_skipped_indices": all_skipped,
                    "_total_fired": total_fired,
                }
                self._create_schedule_at_index(job_id, s0.name, base_state, ts, last_idx)
                new_schedules = self._get_job_schedules(job_id)
                if new_schedules:
                    await self._fire(new_schedules[0])
                    return
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
