"""History — 角色对话历史管理。

数据结构（持久化到 history.json）：
  [
    {"role": "user"|"assistant"|"system_trigger"|"tool",
     "content": <str or structured>,
     "time": "YYYY-MM-DD HH:MM:SS",
     "name": <optional, for tool msgs>,
     "tool_call_id": <optional, for tool msgs>,
     "tool_calls": <optional, for assistant-with-tool-call msgs>}
  ]

向后兼容：旧版 history.json 只有 {role, content, time} 三字段，读入仍然 OK。
"""
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

    def append_pair(self, user: str, assistant: str, ts: str | None = None):
        now = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.messages.append({"role": "user", "content": user, "time": now})
        self.messages.append({"role": "assistant", "content": assistant, "time": now})

    def append_user(self, content: str, ts: str | None = None):
        """追加单条 user 消息（用于实时落盘：_run_turn 入口立即记录本轮输入）。"""
        now = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.messages.append({"role": "user", "content": content, "time": now})

    def append_tool(self, tool_call_id: str, name: str, content: str,
                    ts: str | None = None):
        """追加 tool 结果消息（用于实时落盘：on_history_save 钩子调用）。"""
        now = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
            "time": now,
        })

    def append_assistant_msg(self, content: str, ts: str | None = None,
                             tool_calls: list | None = None):
        """追加单条 assistant 消息（可选带 tool_calls）。
        与 append_assistant 的区别：本方法保留 tool_calls 元数据。
        """
        now = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg: dict = {"role": "assistant", "content": content, "time": now}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)

    def append_trigger(self, content: str):
        """追加 system_trigger 消息（时策触发，隐式注入）。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.messages.append({"role": "system_trigger", "content": content, "time": now})

    def append_assistant(self, content: str):
        """追加 assistant 消息（用于时策触发后的 LLM 回复，不重复记录 trigger）。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.messages.append({"role": "assistant", "content": content, "time": now})
