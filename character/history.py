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

    def load_slice(self, abs_from: int, abs_to: int) -> list[dict]:
        """返回 [abs_from, abs_to] 索引范围的原文切片。

        找不到文件 / 索引越界时回退到空列表（留给调用方降级）。
        自动 clamp 到 [0, len-1]；abs_from > abs_to 返回空列表。
        """
        msgs = self.load().messages
        if not msgs:
            return []
        if abs_from < 0:
            abs_from = 0
        if abs_to >= len(msgs):
            abs_to = len(msgs) - 1
        if abs_from > abs_to:
            return []
        return msgs[abs_from:abs_to + 1]

    def save(self):
        try:
            # 确保父目录存在（default 角色目录可能未显式创建）。
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
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

    def append_system(self, content: str, ts: str | None = None):
        """追加系统事件消息（引擎切换等元数据）。

        role=system, content 必须以 "[智能基元切换]" 等受控前缀开头，
        _render_single_message 才能与真正的 system prompt 区分开。
        """
        now = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.messages.append({"role": "system", "content": content, "time": now})
