"""Unit tests for lifecycle parsing helpers."""
from __future__ import annotations

from types import SimpleNamespace

from common import lifecycle


def _ctx(messages):
    return SimpleNamespace(history=SimpleNamespace(messages=messages))


def test_extract_reply_returns_latest_clean_assistant_text():
    messages = [
        {"role": "assistant", "content": "old"},
        {"role": "assistant", "content": "[思考]internal\nanswer"},
    ]

    assert lifecycle.extract_reply(messages) == "answer"


def test_extract_reply_removes_xml_think_blocks():
    assert lifecycle.extract_reply([
        {"role": "assistant", "content": "<think>private</think> public"},
    ]) == "public"


def test_extract_reply_ignores_empty_or_non_assistant_messages():
    assert lifecycle.extract_reply([
        {"role": "tool", "content": "tool result"},
        {"role": "assistant", "content": ""},
    ]) == ""


def test_build_trigger_message_includes_pending_count():
    context = _ctx([
        {"role": "system_trigger", "content": "[时策任务]\nfirst"},
        {"role": "system_trigger", "content": "[时策任务]\nsecond"},
    ])

    result = lifecycle._build_trigger_message(context)

    assert "second" in result
    assert "前面已有 1 个任务过期" in result


def test_build_trigger_message_resets_pending_scan_after_assistant():
    context = _ctx([
        {"role": "system_trigger", "content": "[时策任务]\nold"},
        {"role": "assistant", "content": "handled"},
        {"role": "system_trigger", "content": "[时策任务]\nnew"},
    ])

    result = lifecycle._build_trigger_message(context)

    assert result.startswith("时策任务到期，请执行：new")
    assert "前面已有 0 个任务过期" in result
    assert "本次是第 1 个" in result


def test_format_trigger_display_extracts_action_and_limits_length():
    long_text = "x" * 100
    rendered = lifecycle._format_trigger_display("【时策】请执行：" + long_text)

    assert rendered == long_text[:60]
    assert lifecycle._format_trigger_display("plain") == "plain"


def test_get_pending_triggers_aggregates_current_batch():
    context = _ctx([
        {"role": "user", "content": "previous"},
        {"role": "system_trigger", "content": "延迟 2s | 第 1/4 个 | 错过: #1\nfirst"},
        {"role": "system_trigger", "content": "延迟 4s | 第 2/4 个 | 剩余 2\nsecond"},
    ])

    pending = lifecycle._get_pending_triggers(context)

    assert len(pending) == 1
    assert "#1-2/4" in pending[0]
    assert "延迟 4s" in pending[0]
    assert "错过  #1未补" in pending[0]
    assert "剩余 2项" in pending[0]
    assert "共 2 条待处理" in pending[0]


def test_get_pending_triggers_replaces_position_placeholder():
    context = _ctx([
        {"role": "system_trigger", "content": "延迟 0s | 第 3/5 个\nplaceholder-{N}"},
    ])

    pending = lifecycle._get_pending_triggers(context)
    assert len(pending) == 1
    assert "placeholder-3" in pending[0]


def test_get_pending_triggers_excludes_trigger_with_later_assistant():
    context = _ctx([
        {"role": "user", "content": "batch"},
        {"role": "system_trigger", "content": "[x]\naction"},
        {"role": "assistant", "content": "handled"},
    ])

    assert lifecycle._get_pending_triggers(context) == []
