"""
schedule/repository.py — 时策持久化存储（JSON 文件，原子写入）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import Schedule


class ScheduleRepository:
    """Schedule JSON 持久化存储。"""

    def __init__(self, store_path: str | Path):
        self._store_path = Path(store_path)
        self._schedules: dict[str, Schedule] = {}
        self._load()

    # ── CRUD ──

    def save(self, schedule: Schedule) -> None:
        self._schedules[schedule.id] = schedule
        self._persist()

    def load(self, id: str) -> Schedule | None:
        return self._schedules.get(id)

    def add(self, schedule: Schedule) -> None:
        self._schedules[schedule.id] = schedule
        self._persist()

    def remove(self, id: str) -> bool:
        if id in self._schedules:
            del self._schedules[id]
            self._persist()
            return True
        return False

    def list(self) -> list[Schedule]:
        return list(self._schedules.values())

    # ── 持久化 ──

    def _persist(self) -> None:
        tmp_path = self._store_path.with_suffix(".tmp")
        data = {"schedules": [self._serialize(s) for s in self._schedules.values()]}
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._store_path)

    def _load(self) -> None:
        if not self._store_path.exists():
            return
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
            for s_data in data.get("schedules", []):
                s = self._deserialize(s_data)
                if s:
                    self._schedules[s.id] = s
        except Exception:
            self._schedules = {}

    def _serialize(self, s: Schedule) -> dict[str, Any]:
        return {
            "id": s.id,
            "name": s.name,
            "enabled": s.enabled,
            "missed_policy": s.missed_policy,
            "state": s.state,
            "playbook_ref": s.playbook_ref or "",
        }

    def _deserialize(self, data: dict[str, Any]) -> Schedule | None:
        try:
            return Schedule(
                id=data["id"],
                name=data["name"],
                condition=None,   # 启动时重建
                context=None,     # 启动时重建
                enabled=data.get("enabled", True),
                missed_policy=data.get("missed_policy", "fire_once"),
                state=data.get("state", {}),
                playbook_ref=data.get("playbook_ref") or None,
            )
        except Exception:
            return None
