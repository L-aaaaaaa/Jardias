"""Unit tests for context rendering and model-message construction."""
from __future__ import annotations

from data_shape import ActorConfig, L1Summary
from experience.adapter.conversation import (
    _choose_fence,
    _extract_pure_text,
    _render_messages_to_recent_section,
    _render_single_message,
    build_system_message,
    form_full_context,
)
from experience.io.writer import save_l1


def test_choose_fence_exceeds_longest_embedded_backtick_run():
    assert _choose_fence("a````b") == "`````"
    assert len(_choose_fence("plain")) == 3


def test_extract_pure_text_removes_wrapper_and_protects_nested_fences():
    raw = "```text\nhello world\n```"

    text = _extract_pure_text(raw)
    assert text == "hello world"

    # 在仍有 3+ 连续 backtick 的情况下，函数会替换为零宽字符避免破坏外层 fence。
    # 直接调用 _replace_backtick_run 验证行为契约。
    from experience.adapter.conversation import _replace_backtick_run
    import re as _re
    replaced = _re.sub(r"`{3,}", _replace_backtick_run, "abc```xyz")
    assert "\u200B" in replaced
    assert "```" not in replaced


def test_extract_pure_text_handles_legacy_wrapper_without_code_fence():
    raw = "## 本次用户消息\n### [2026-01-01 00:00:00] user\nhello world"
    assert _extract_pure_text(raw) == "hello world"


def test_render_single_message_handles_system_and_tool_messages():
    assert _render_single_message({"role": "system", "content": "private"}) == []
    switch = _render_single_message({
        "role": "system",
        "content": "[智能基元切换] old -> new",
        "time": "2026-01-01 00:00:00",
    })
    tool = _render_single_message({
        "role": "tool", "name": "demo", "content": "ok",
        "time": "2026-01-01 00:00:01",
    })

    assert "assistant" not in switch[0]
    assert "system" in switch[0]
    assert "tool(demo)" in tool[0]
    assert "[tool_call: demo]" in tool[0]


def test_render_messages_sorts_by_timestamp():
    recent = _render_messages_to_recent_section([
        {"role": "assistant", "content": "second", "time": "2026-01-01 00:00:02"},
        {"role": "user", "content": "first", "time": "2026-01-01 00:00:01"},
    ])

    assert recent.index("first") < recent.index("second")


def test_build_system_message_injects_identity_and_switch_note(monkeypatch):
    config = ActorConfig()
    config.identity.system_prompt = "Hi #{character_name}"
    config.identity.title = "Tester"
    config.identity.traits = "careful"
    monkeypatch.setattr(
        "experience.adapter.conversation.build_config_context",
        lambda config, character_name=None: "ENGINE",
    )

    message = build_system_message(config, "alice", "SWITCHED")

    assert message["role"] == "system"
    assert "你的名字叫 alice" in message["content"]
    assert "Hi alice" in message["content"]
    assert "SWITCHED" in message["content"]
    assert "ENGINE" in message["content"]


def test_form_full_context_writes_user_input_and_returns_four_messages(
        isolated_workspace, monkeypatch):
    config = ActorConfig()
    monkeypatch.setattr(
        "experience.adapter.conversation.build_config_context",
        lambda config, character_name=None: "ENGINE",
    )

    messages = form_full_context(config, [], "hello", character_name="alice")

    assert [message["role"] for message in messages] == [
        "system", "user", "user", "user",
    ]
    assert "hello" in messages[3]["content"]


def test_build_system_message_uses_current_character_name(monkeypatch):
    monkeypatch.setattr(
        "experience.adapter.conversation.build_config_context",
        lambda config, character_name=None: "ENGINE",
    )

    message = build_system_message(ActorConfig(), "测试角色")

    assert "你的名字叫 测试角色" in message["content"]
