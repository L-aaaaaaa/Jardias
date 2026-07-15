"""experience 模块 — 对话经验管理（适配器层）。

聚焦：
- 4 段结构（message0..message3）的 IO 层读写
- 适配层：on_user_input（写块3）/ on_round_complete（写块2 + 清空块3）
- 业务装配：build_context_from_experience
- 渲染工具：_render_single_message / _extract_pure_text

接口已迁移：
    - update_experience(name, "用户输入"/"对话完成"/"dump"...) → 适配层 on_user_input / on_round_complete
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from experience import (
    load_experience, init_experience,
    on_user_input, on_round_complete,
    build_context_from_experience,
    _extract_pure_text, _render_single_message,
)
from experience.io.reader import _CHARACTER_NAME_CACHE
from character import ensure_dirs, get_character_dir
from data_shape import ActorConfig, RoleConfig, IPURuntime


def _make_actor(title="alice", system="hi"):
    return ActorConfig(identity=RoleConfig(title=title, system_prompt=system),
                       runtime=IPURuntime())


@pytest.fixture(autouse=True)
def _clear_char_cache():
    """每个用例前后清空 _CHARACTER_NAME_CACHE，避免相互干扰。"""
    _CHARACTER_NAME_CACHE.clear()
    yield
    _CHARACTER_NAME_CACHE.clear()


# ── init / load 主流程 ───────────────────────────

class TestInitLoadRoundTrip:
    def test_init_creates_md(self, tmp_workdir):
        cfg = _make_actor("alice", "你是 alice")
        init_experience("alice", cfg)
        blocks = load_experience("alice")
        assert set(blocks.keys()) >= {0, 1, 2, 3}

    def test_load_after_init_has_name(self, tmp_workdir):
        init_experience("bob", _make_actor("bob"))
        blocks = load_experience("bob")
        # init 写入的 message0 应含角色名
        assert "bob" in blocks[0]


# ── 适配层：on_user_input / on_round_complete ─────────────────────────

class TestOnUserInput:
    def test_writes_block3(self, tmp_workdir):
        init_experience("alice", _make_actor("alice"))
        on_user_input("alice", "今天聊什么", timestamp="2026-07-12 18:00:00")
        blocks = load_experience("alice")
        assert "今天聊什么" in blocks[3]
        assert "2026-07-12 18:00:00" in blocks[3]

    def test_default_timestamp(self, tmp_workdir):
        init_experience("alice", _make_actor("alice"))
        on_user_input("alice", "hi")
        blocks = load_experience("alice")
        assert "hi" in blocks[3]


class TestOnRoundComplete:
    def test_dump_appends_to_block2_and_clears_block3(self, tmp_workdir):
        init_experience("alice", _make_actor("alice"))
        on_user_input("alice", "你好")
        new_messages = [
            {"role": "user", "time": "2026-07-12 18:00:01", "content": "你好"},
            {"role": "assistant", "time": "2026-07-12 18:00:02", "content": "你好我是 alice"},
        ]
        on_round_complete("alice", new_messages)
        blocks = load_experience("alice")
        # 块2 应有对话原文
        assert "你好" in blocks[2]
        assert "你好我是 alice" in blocks[2]
        assert "## 摘要" in blocks[2]
        assert "## 近期对话原文" in blocks[2]
        # 块3 应清空（落到磁盘时由 writer 填入占位符"（等待用户输入）"）
        assert "你好" not in blocks[3]
        assert "（等待用户输入）" in blocks[3]

    def test_dump_with_empty_messages_no_op(self, tmp_workdir):
        init_experience("alice", _make_actor("alice"))
        on_round_complete("alice", [])
        blocks = load_experience("alice")
        # 块2 应保持空骨架（由 init 写入）
        assert "## 近期对话原文" not in blocks[2] or blocks[2] == ""


# ── build_context_from_experience ─────────────────────────

class TestBuildContext:
    def test_returns_messages_with_4_blocks(self, tmp_workdir):
        cfg = _make_actor("alice", "sys")
        init_experience("alice", cfg)
        msgs = build_context_from_experience(cfg, "alice", user_input="x")
        # 4 个固定 block
        assert len(msgs) == 4
        # 第 0 块 role 固定 system
        assert msgs[0]["role"] == "system"
        # message3 应含 user_input
        assert "x" in msgs[3]["content"]


# ── 内部 helper ────────────────────────────────────────

class TestExtractPureText:
    def test_strips_wrapping_fences(self):
        raw = "```markdown\nhello\n```"
        out = _extract_pure_text(raw)
        assert "hello" in out
        assert "```" not in out

    def test_empty(self):
        out = _extract_pure_text("")
        assert out == ""

    def test_passthrough_when_no_fence(self):
        out = _extract_pure_text("plain")
        assert "plain" in out


class TestRenderSingleMessage:
    def test_user_message(self):
        lines = _render_single_message({"role": "user", "content": "hi", "time": "t"})
        assert isinstance(lines, list)
        assert any("hi" in line for line in lines)

    def test_assistant_message(self):
        lines = _render_single_message({"role": "assistant", "content": "hello"})
        assert any("hello" in line for line in lines)

    def test_system_message_returns_empty(self):
        lines = _render_single_message({"role": "system", "content": "sys"})
        assert lines == []
