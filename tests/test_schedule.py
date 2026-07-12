"""schedule/ — 时策系统。

真实 API：
- schedule/strategies.py: DelayCondition, wall_ms
- schedule/repository.py: ScheduleRepository (CRUD)
- schedule/shice.py: TemporalScheduler, JobFireContext, format_trigger
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from schedule.strategies import DelayCondition, wall_ms
from schedule.repository import ScheduleRepository
from schedule.shice import TemporalScheduler, JobFireContext
from schedule.types import Schedule


# ── DelayCondition ─────────────────────────────────────────

class TestDelayCondition:
    def test_not_met_when_init(self):
        c = DelayCondition(delay_ms=10_000)
        state = {}
        assert c.is_met(state) is False
        # next_run_ms 已被写入
        assert "next_run_ms" in state

    def test_met_after_delay(self, monkeypatch):
        c = DelayCondition(delay_ms=10)
        state = {}

        # 先触发 is_met 写入 next_run_ms
        c.is_met(state)
        # 把 wall_ms 推后 11ms 后再判
        orig_wall = wall_ms
        monkeypatch.setattr("schedule.strategies.wall_ms",
                            lambda: orig_wall() + 11)
        assert c.is_met(state) is True

    def test_update_returns_none(self):
        c = DelayCondition(delay_ms=10)
        assert c.update({}) is None


# ── ScheduleRepository ─────────────────────────────────────

class TestScheduleRepository:
    def test_empty(self, tmp_path):
        repo = ScheduleRepository(store_path=tmp_path / "s.json")
        assert repo.list() == []

    def test_add_and_get(self, tmp_path):
        repo = ScheduleRepository(store_path=tmp_path / "s.json")
        s = Schedule(id="x", name="test", condition=None, context=None)
        repo.add(s)
        assert repo.load("x") is s
        assert len(repo.list()) == 1

    def test_save(self, tmp_path):
        repo = ScheduleRepository(store_path=tmp_path / "s.json")
        s = Schedule(id="x", name="test", condition=None, context=None)
        repo.save(s)
        assert repo.load("x") is s

    def test_remove_existing(self, tmp_path):
        repo = ScheduleRepository(store_path=tmp_path / "s.json")
        s = Schedule(id="x", name="test", condition=None, context=None)
        repo.add(s)
        assert repo.remove("x") is True
        assert repo.load("x") is None

    def test_remove_missing(self, tmp_path):
        repo = ScheduleRepository(store_path=tmp_path / "s.json")
        assert repo.remove("ghost") is False

    def test_persistence(self, tmp_path):
        f = tmp_path / "s.json"
        repo = ScheduleRepository(store_path=f)
        s = Schedule(id="x", name="n", condition=None, context=None,
                     state={"k": 1})
        repo.add(s)
        repo2 = ScheduleRepository(store_path=f)
        got = repo2.load("x")
        assert got is not None
        assert got.state["k"] == 1

    def test_corrupt_file_loads_empty(self, tmp_path):
        f = tmp_path / "s.json"
        f.write_text("{not valid", encoding="utf-8")
        repo = ScheduleRepository(store_path=f)
        assert repo.list() == []


# ── JobFireContext ──────────────────────────────────────────

class TestJobFireContext:
    def test_remaining_count(self):
        ctx = JobFireContext(
            job_id="j", fire_index=2,
            timestamps=[1, 2, 3, 4, 5],
            character_id="c", message="m", late_sec=0.0,
        )
        assert ctx.remaining_count == 2  # 5 - 2 - 1

    def test_format_trigger_on_time(self):
        ctx = JobFireContext(
            job_id="j", fire_index=0,
            timestamps=[1, 2, 3], character_id="c", message="hello", late_sec=0.0,
        )
        s = ctx.format_trigger()
        assert "hello" in s
        assert "第 1/3" in s

    def test_format_trigger_late(self):
        ctx = JobFireContext(
            job_id="j", fire_index=1,
            timestamps=[1, 2, 3], character_id="c", message="x", late_sec=10.0,
        )
        s = ctx.format_trigger()
        assert "延迟 10s" in s

    def test_format_trigger_skipped(self):
        ctx = JobFireContext(
            job_id="j", fire_index=2,
            timestamps=[1, 2, 3, 4], character_id="c", message="y",
            late_sec=0.0, skipped_indices=[1],
        )
        s = ctx.format_trigger()
        assert "错过" in s
        assert "#2" in s

    def test_format_trigger_with_placeholders(self):
        ctx = JobFireContext(
            job_id="j", fire_index=0,
            timestamps=[1, 2, 3], character_id="c",
            message="第 {pos}/{total} 次，剩余 {remaining}", late_sec=0.0,
        )
        s = ctx.format_trigger()
        assert "第 1/3" in s
        assert "剩余 2" in s


# ── TemporalScheduler ───────────────────────────────────────

class TestTemporalScheduler:
    def test_construct(self, tmp_path):
        sched = TemporalScheduler(store_path=tmp_path / "s.json")
        assert sched is not None
        assert sched._running is False

    def test_add_recurring(self, tmp_path):
        sched = TemporalScheduler(store_path=tmp_path / "s.json")
        now = wall_ms()
        ts = [now + 10_000, now + 20_000]
        jid = sched.add_recurring(name="t", message="m", timestamps=ts,
                                    character_id="alice")
        assert jid != ""
        # 列表里能找到这个 job
        jobs = sched.list_jobs()
        assert any(j["job_id"] == jid for j in jobs)

    def test_add_empty_returns_empty_id(self, tmp_path):
        sched = TemporalScheduler(store_path=tmp_path / "s.json")
        assert sched.add_recurring(name="x", message="", timestamps=[],
                                    character_id="alice") == ""

    def test_remove_remaining(self, tmp_path):
        sched = TemporalScheduler(store_path=tmp_path / "s.json")
        now = wall_ms()
        ts = [now + 10_000]
        jid = sched.add_recurring(name="t", message="m", timestamps=ts,
                                    character_id="alice")
        assert sched.remove_remaining(jid) is True

    def test_list_jobs_empty(self, tmp_path):
        sched = TemporalScheduler(store_path=tmp_path / "s.json")
        assert sched.list_jobs() == []
