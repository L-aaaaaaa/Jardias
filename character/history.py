"""History — 角色对话历史管理。"""
from __future__ import annotations

import json
import os
from datetime import datetime


class History:
    """角色对话历史，以 JSON 格式持久化。"""

    def __init__(self, path: str):
        self.path = path
        self.messages: list[dict] = []

    def load(self) -> "History":
        if os.path.exists(self.path):
            try:
                self.messages = json.load(open(self.path, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.messages = []
        return self

    def save(self):
        try:
            json.dump(
                self.messages,
                open(self.path, "w", encoding="utf-8"),
                ensure_ascii=False,
                indent=2,
            )
        except OSError:
            pass

    def append_pair(self, user: str, assistant: str):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.messages.append({"role": "user", "content": user, "time": now})
        self.messages.append({"role": "assistant", "content": assistant, "time": now})
