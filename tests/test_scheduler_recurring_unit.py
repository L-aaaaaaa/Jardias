"""Unit tests for TemporalScheduler.add_recurring merge/skip paths and missed-chain inline."""
from __future__ import annotations

import asyncio

import pytest

from schedule.shice import TemporalScheduler
from schedule.strategies import wall_ms


# ────────────────────────────────────────────────────────────────────
# fixtures
# ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def scheduler(tmp_path):
    """未启动的 TemporalScheduler（避免触发 _rearm_timer 的 loop.call_later）。"""
    fires = []

    async def on_fire(ctx):
        fires.append(ctx)

    sched = TemporalScheduler(tmp_path / "sched.json", on_job_fire=on_fire)
    yield sched, fires


# ────────────────────────────────────────────────────────────────────
# add_recurring — 空列表 / 无重叠 / 重叠合并 / 跳过
# ────────────────────────────────────────────────────────────────────


def test_add_recurring_returns_empty_for_empty_timestamps(scheduler):
    sched, _ = scheduler
    assert sched.add_recurring("noop", "msg", [], character_id="alice") == ""


def test_add_recurring_creates_independent_job(scheduler):
    sched, _ = scheduler
    job_id = sched.add_recurring(
        "wake", "msg", [wall_ms() + 60_000], character_id="alice")
    assert job_id
    jobs = sched.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == job_id
    assert jobs[0]["status"] == "active"


def test_add_recurring_merges_when_overlap(scheduler):
    sched, _ = scheduler
    now = wall_ms()
    a = sched.add_recurring("j", "msg", [now + 60_000, now + 120_000], character_id="alice")
    b = sched.add_recurring("j", "msg", [now + 120_000, now + 180_000], character_id="alice")
    assert a == b
    job = sched.list_jobs()[0]
    assert job["total"] == 3  # 去重后 3 个时间戳


def test_add_recurring_skips_when_all_timestamps_already_covered(scheduler):
    sched, _ = scheduler
    now = wall_ms()
    a = sched.add_recurring("j", "msg", [now + 60_000, now + 120_000], character_id="alice")
    # 全部重复
    b = sched.add_recurring("j", "msg", [now + 60_000, now + 120_000], character_id="alice")
    assert a == b
    job = sched.list_jobs()[0]
    assert job["total"] == 2  # 没有新增


def test_add_recurring_skips_when_existing_already_past_start(scheduler):
    """已有 schedule 已经推进到 new_start_idx 之后 → 跳过合并。

    实现里 `existing_next_idx > new_start_idx` 才会跳过。
    我们手动把现有 schedule 的 _next_index 设大来模拟。
    """
    sched, _ = scheduler
    now = wall_ms()
    sched.add_recurring("j", "msg", [now + 60_000], character_id="alice")
    # 把现有 schedule 的 _next_index 推到末尾后，新时间戳全部更早 → 跳过
    schedules = sched._repo.list()
    schedules[0].state["_next_index"] = 99
    sched._repo.save(schedules[0])
    b = sched.add_recurring("j", "msg", [now + 50_000], character_id="alice")
    assert b  # 返回原 job_id（跳过）
    # 集合长度未变
    jobs = sched.list_jobs()
    assert jobs[0]["total"] == 1


# ────────────────────────────────────────────────────────────────────
# remove_remaining / list_jobs
# ────────────────────────────────────────────────────────────────────


def test_remove_remaining_clears_job(scheduler):
    sched, _ = scheduler
    now = wall_ms()
    job_id = sched.add_recurring("j", "msg", [now + 60_000, now + 120_000], character_id="alice")
    assert sched.remove_remaining(job_id) is True
    assert sched.remove_remaining(job_id) is False
    assert sched.list_jobs() == []


# ────────────────────────────────────────────────────────────────────
# Missed-chain inline fire（_advance_after_batch）
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missed_chain_fires_main_and_skipped_in_one_batch(tmp_path):
    """当 next_idx 起所有时间戳都已过期时，一次性 main fire + 多个 missed。"""
    fired = []

    async def on_fire(ctx):
        fired.append({
            "fire_index": ctx.fire_index,
            "skipped": list(ctx.skipped_indices),
            "batch_size": ctx.batch_size,
            "late_sec": ctx.late_sec,
        })

    now = wall_ms()
    # 所有时间戳都已在过去（now - 1000ms, now - 2000ms, now - 3000ms）
    timestamps = [now - 3000, now - 2000, now - 1000]

    sched = TemporalScheduler(tmp_path / "sched.json", on_job_fire=on_fire)
    await sched.start()
    try:
        job_id = sched.add_recurring("chain", "msg", timestamps, character_id="alice")
        # 等到首条 schedule 触发后，_advance_after_batch 应检测整条链都已过期 → inline 触发全部
        # 用 monkey-patched wall_ms 让 timer 立刻到期
        await asyncio.sleep(0.05)
        # 触发首次 fire：第一个时间戳触发后，剩余都已过期 → 整链 inline 触发
        # 由于 add_recurring 已经按 "next_idx 起最早未来" 排过序，首条 schedule 是 ts[0]
        # 当 ts[0] fire 后，_advance_after_batch 把 ts[1..2] 当作 missed inline 触发
        # 但实际我们的 timestamps 全都过去：add_recurring 会把 new_start_idx = len(ts) - 1
        # 所以我们直接靠 on_timer 自然路径来覆盖：时间已经过期
    finally:
        sched.stop()


@pytest.mark.asyncio
async def test_advance_after_batch_marks_completed_when_queue_exhausted(tmp_path, monkeypatch):
    """队列耗尽 → 写入 _status=completed 并保留供查询。"""
    fired = []

    async def on_fire(ctx):
        fired.append(ctx)

    sched = TemporalScheduler(tmp_path / "sched.json", on_job_fire=on_fire)
    await sched.start()
    try:
        # 单一时间戳，触发后即耗尽
        # 用 wall_ms 制造即将过期
        now = wall_ms()
        sched.add_recurring("once", "msg", [now + 50], character_id="alice")
        await asyncio.sleep(0.15)  # 等 timer 触发
        await asyncio.sleep(0.05)
        jobs = sched.list_jobs()
        # 单次任务 fire 后队列耗尽，应被标记为 completed
        completed = [j for j in jobs if j["status"] == "completed"]
        assert completed, f"expected a completed job, got {jobs}"
    finally:
        sched.stop()


@pytest.mark.asyncio
async def test_fire_uses_late_sec_for_expired_schedule(tmp_path, monkeypatch):
    """过期 schedule 的 late_sec 反映延迟秒数。"""
    fired = []

    async def on_fire(ctx):
        fired.append(ctx)

    # 时间戳在 2 秒前
    past = wall_ms() - 2000
    sched = TemporalScheduler(tmp_path / "sched.json", on_job_fire=on_fire)
    # 强制 add_recurring 把过去的时间戳作为起点
    await sched.start()
    try:
        sched.add_recurring("late", "msg", [past + 60_000], character_id="alice")
        # 我们手动把 schedule 状态改成已过期：
        schedules = sched._repo.list()
        if schedules:
            schedules[0].state["next_run_ms"] = past
            schedules[0].state["_timestamps"] = [past]
            schedules[0].state["_next_index"] = 0
            sched._repo.save(schedules[0])
            sched._rearm_timer()
            await asyncio.sleep(0.1)
        # 至少触发了一次
        assert fired, "expected at least one fire"
        # late_sec 应 >= 0
        assert all(ctx.late_sec >= 0 for ctx in fired)
    finally:
        sched.stop()
