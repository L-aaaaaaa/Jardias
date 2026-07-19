"""Unit tests for scheduling primitives and persistence."""
from __future__ import annotations

import json

import pytest

from schedule.repository import ScheduleRepository
from schedule.strategies import DelayCondition
from schedule.types import Schedule
from schedule.shice import JobFireContext, TemporalScheduler


def test_delay_condition_initializes_next_run_once(monkeypatch):
    values = iter([1000.0, 1000.0, 1001.0])
    monkeypatch.setattr("schedule.strategies.wall_ms", lambda: int(next(values) * 1000))
    state = {}
    condition = DelayCondition(5000)

    assert not condition.is_met(state)
    assert state["next_run_ms"] == 1005000
    assert not condition.is_met(state)


def test_delay_condition_can_be_met_after_target(monkeypatch):
    values = iter([1000.0, 1000.0, 1006.0])
    monkeypatch.setattr("schedule.strategies.wall_ms", lambda: int(next(values) * 1000))
    state = {}
    condition = DelayCondition(5000)

    assert not condition.is_met(state)
    assert condition.is_met(state)
    assert condition.update(state) is None


def test_schedule_repository_round_trips_serializable_state(tmp_path):
    path = tmp_path / "schedule.json"
    repository = ScheduleRepository(path)
    schedule = Schedule(
        id="s1",
        name="demo",
        condition=DelayCondition(1000),
        context=None,
        state={"next_run_ms": 123, "character_id": "alice"},
        playbook_ref="book",
    )

    repository.add(schedule)
    restored = ScheduleRepository(path).load("s1")

    assert restored is not None
    assert restored.condition is None
    assert restored.state["character_id"] == "alice"
    assert restored.playbook_ref == "book"
    assert json.loads(path.read_text(encoding="utf-8"))["schedules"][0]["id"] == "s1"


def test_schedule_repository_handles_corrupt_storage(tmp_path):
    path = tmp_path / "schedule.json"
    path.write_text("{", encoding="utf-8")

    assert ScheduleRepository(path).list() == []


def test_schedule_repository_remove_reports_presence(tmp_path):
    repository = ScheduleRepository(tmp_path / "schedule.json")
    schedule = Schedule("s1", "demo", None, None)
    repository.add(schedule)

    assert repository.remove("s1") is True
    assert repository.remove("s1") is False


def test_job_fire_context_formats_positions_skips_and_placeholders():
    context = JobFireContext(
        job_id="job",
        fire_index=1,
        timestamps=[100, 200, 300, 400],
        character_id="alice",
        message="do {pos}/{total}, left {remaining}",
        late_sec=2.4,
        skipped_indices=[0],
        batch_size=2,
    )

    assert context.remaining_count == 1
    assert context.format_trigger() == (
        "[时策任务 | 第 2/4 个 | 延迟 2s | 错过: #1（共 1 个） | 剩余 1]\n"
        "do 2/4, left 1"
    )


def test_scheduler_add_recurring_deduplicates_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr("schedule.shice.wall_ms", lambda: 1000)
    scheduler = TemporalScheduler(tmp_path / "schedule.json")

    job_id = scheduler.add_recurring("demo", "say hi", [3000, 2000, 2000], "alice")

    jobs = scheduler.list_jobs()
    assert job_id
    assert jobs[0]["total"] == 2
    assert jobs[0]["remaining"] == 1


def test_scheduler_empty_recurring_request_is_noop(tmp_path):
    scheduler = TemporalScheduler(tmp_path / "schedule.json")

    assert scheduler.add_recurring("demo", "say hi", [], "alice") == ""
    assert scheduler.list_jobs() == []


def test_concurrency_controller_rejects_invalid_limit():
    from schedule.concurrency import ConcurrencyController

    with pytest.raises(ValueError, match="max_concurrent"):
        ConcurrencyController(0)
