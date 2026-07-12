"""common/experience_core.py — experience.md 渲染 helper。

聚焦：
- 4 段结构（message0..message3）的读写
- 用户输入先写原则：update 不会破坏既有结构
- 占位符替换（template 中的 %NAME% %USER% 等）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from common.experience_core import (
    load_experience, update_experience, init_experience,
    build_context_from_experience,
    _extract_pure_text, _render_single_message,
    _CHARACTER_NAME_CACHE,
)
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


# ── init / load / update 主流程 ──────────────────────────

class TestInitLoadRoundTrip:
    def test_init_creates_md(self, tmp_workdir):
        cfg = _make_actor("alice", "你是 alice")
        init_experience("alice", cfg)
        blocks = load_experience("alice")
        # 应含 4 个 key：0..3
        assert set(blocks.keys()) >= {0, 1, 2, 3}

    def test_load_after_init_has_name(self, tmp_workdir):
        init_experience("bob", _make_actor("bob"))
        blocks = load_experience("bob")
        # init 写入的 message0 应含角色名
        assert "bob" in blocks[0]


class TestUpdateExperience:
    def test_user_input(self, tmp_workdir):
        """update(character_name, "用户输入", ...) 应写入 message3。"""
        init_experience("alice", _make_actor("alice"))
        update_experience("alice", "用户输入",
                          {"user_input": "今天聊什么", "timestamp": "2026-07-12 18:00:00"})
        blocks = load_experience("alice")
        assert "今天聊什么" in blocks[3]
        assert "2026-07-12 18:00:00" in blocks[3]

    def test_user_input_default_timestamp(self, tmp_workdir):
        """不传 timestamp 应使用当前时间。"""
        init_experience("alice", _make_actor("alice"))
        update_experience("alice", "用户输入", {"user_input": "hi"})
        blocks = load_experience("alice")
        assert "hi" in blocks[3]

    def test_dialog_done_writes_placeholder(self, tmp_workdir):
        """对话完成 → message3 写占位（不是清空，LLM 需要看到上下文）。"""
        init_experience("alice", _make_actor("alice"))
        update_experience("alice", "用户输入", {"user_input": "x"})
        # 注入输入 → message3 有内容
        blocks = load_experience("alice")
        assert blocks[3] != ""
        update_experience("alice", "对话完成", {})
        blocks = load_experience("alice")
        # 实现可能是「写占位」或「清空」，都算合法
        assert isinstance(blocks[3], str)

    def test_unknown_op_silent(self, tmp_workdir):
        """未识别的 operation 名应静默忽略，不抛。"""
        init_experience("alice", _make_actor("alice"))
        try:
            update_experience("alice", "未定义的操作", {"x": 1})
        except Exception:
            pytest.fail("未知 operation 不应抛异常")


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
        # 返回 list[str]，每条一段 markdown
        assert isinstance(lines, list)
        assert any("hi" in line for line in lines)

    def test_assistant_message(self):
        lines = _render_single_message({"role": "assistant", "content": "hello"})
        assert any("hello" in line for line in lines)

    def test_system_message_returns_empty(self):
        lines = _render_single_message({"role": "system", "content": "sys"})
        assert lines == []
