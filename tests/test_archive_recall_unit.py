"""Unit tests for experience/adapter/archive_recall pure algorithms."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from experience.adapter import archive_recall as ar
from experience.adapter.archive_recall import (
    _analyze_slice,
    _build_topic_label_regex,
    _covered_ranges,
    _describe_slice,
    _extract_send_to_character_targets,
    _guess_topic,
    _gaps_between_covered,
    _is_archive_trigger,
)


# ────────────────────────────────────────────────────────────────────
# _is_archive_trigger / _build_topic_label_regex
# ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("prefix", [
    "归档", "总结", "转摘要", "压缩", "先放一放", "收尾",
    "聊完了", "话题结束", "这个话题结束", "把刚才的",
])
def test_archive_trigger_prefixes_are_recognised(prefix):
    assert _is_archive_trigger(prefix + " 我刚才说的话题")
    assert _is_archive_trigger("   " + prefix + " 收尾")  # 允许前导空格


def test_archive_trigger_rejects_non_matching_user_input():
    assert not _is_archive_trigger("你好啊")
    assert not _is_archive_trigger("继续刚才的话题")
    assert not _is_archive_trigger("")


def test_archive_trigger_rejects_non_string_input():
    assert _is_archive_trigger(None) is False  # type: ignore[arg-type]
    assert _is_archive_trigger(123) is False  # type: ignore[arg-type]


def test_topic_label_regex_rejects_trailing_digits():
    pattern = _build_topic_label_regex("话题1")
    assert pattern.search("我们讨论话题1吧").group() == "话题1"
    # 「话题12」不应被「话题1」匹配：lookahead 拒绝后接数字
    assert pattern.search("我们讨论话题12吧") is None


def test_topic_label_regex_allows_text_suffixes():
    pattern = _build_topic_label_regex("hotpot")
    assert pattern.search("hotpot?") is not None
    assert pattern.search("hotpot讨论") is not None


def test_topic_label_regex_special_characters_escaped():
    pattern = _build_topic_label_regex("a.b")
    assert pattern.search("see a.b here") is not None
    # regex 元字符不应被解释
    assert pattern.search("see aXb here") is None


# ────────────────────────────────────────────────────────────────────
# _analyze_slice / _guess_topic / _describe_slice
# ────────────────────────────────────────────────────────────────────


def test_analyze_slice_empty_returns_zero_turns():
    assert _analyze_slice([]) == (0, "", "", [])


def test_analyze_slice_counts_user_turns_and_detects_engine_switch():
    msgs = [
        {"role": "user", "time": "2026-07-19 10:00:00",
         "content": "我想了解"},
        {"role": "assistant", "time": "2026-07-19 10:00:30",
         "content": "好的，已切换到 dashscope 引擎，切换成功"},
        {"role": "user", "time": "2026-07-19 10:01:00",
         "content": "继续"},
        {"role": "assistant", "time": "2026-07-19 10:01:30",
         "content": "好的"},
    ]
    user_turns, start_t, end_t, events = _analyze_slice(msgs)
    assert user_turns == 2
    assert start_t == "2026-07-19 10:00:00"
    assert end_t == "2026-07-19 10:01:30"
    assert "引擎切换" in events
    assert "身份探索" in events  # user 提到「了解」


def test_analyze_slice_detects_token_and_image_topics():
    msgs = [
        {"role": "user", "content": "现在消耗多少 token 了？"},
        {"role": "user", "content": "帮我看看这张 image"},
    ]
    _, _, _, events = _analyze_slice(msgs)
    assert "智点感知" in events
    assert "图片理解" in events


def test_guess_topic_returns_default_when_empty():
    assert _guess_topic([]) == "基础对话测试"


def test_guess_topic_dedupes_and_truncates():
    events = ["引擎切换", "引擎切换", "身份探索", "智点感知", "日志调试"]
    topic = _guess_topic(events)
    assert topic == "引擎切换 + 身份探索 + 智点感知"


def test_describe_slice_handles_engine_and_identity_phrases():
    desc = _describe_slice(3, ["引擎切换", "身份探索"], "引擎切换")
    assert "共 3 轮对话" in desc
    # 涉及 + 引擎切换测试两个分支里，至少出现一条
    assert ("智能基元切换测试" in desc) or ("身份定义" in desc)


def test_describe_slice_omits_topic_phrase_when_default_topic():
    desc = _describe_slice(2, [], "基础对话测试")
    assert desc.startswith("共 2 轮对话")


# ────────────────────────────────────────────────────────────────────
# _covered_ranges / _gaps_between_covered / manual_only
# ────────────────────────────────────────────────────────────────────


def test_covered_ranges_returns_empty_when_log_empty():
    assert _covered_ranges([]) == []
    assert _covered_ranges([], manual_only=True) == []


def test_covered_ranges_sorts_and_merges_overlapping():
    log = [
        {"abs_from": 0, "abs_to": 5},
        {"abs_from": 3, "abs_to": 8},
        {"abs_from": 20, "abs_to": 25},
    ]
    assert _covered_ranges(log) == [(0, 8), (20, 25)]


def test_covered_ranges_merges_adjacent():
    log = [
        {"abs_from": 0, "abs_to": 5},
        {"abs_from": 6, "abs_to": 10},
    ]
    assert _covered_ranges(log) == [(0, 10)]


def test_covered_ranges_manual_only_filters_other_sources():
    log = [
        {"abs_from": 0, "abs_to": 5, "source": "archive_recent_talk"},
        {"abs_from": 6, "abs_to": 10, "source": "auto_summarize"},
        {"abs_from": 12, "abs_to": 15, "source": "archive_recent_talk"},
    ]
    assert _covered_ranges(log, manual_only=True) == [(0, 5), (12, 15)]
    assert _covered_ranges(log, manual_only=False) == [(0, 10), (12, 15)]


def test_gaps_between_covered_with_empty_log_returns_whole_range():
    assert _gaps_between_covered(10, []) == [(0, 9)]


def test_gaps_between_covered_handles_all_positions():
    # 全部 10 条都已被覆盖
    log = [{"abs_from": 0, "abs_to": 9}]
    assert _gaps_between_covered(10, log) == []
    # 头部 gap + 尾部 gap
    log = [{"abs_from": 3, "abs_to": 5}]
    assert _gaps_between_covered(10, log) == [(0, 2), (6, 9)]
    # 中间 gap
    log = [
        {"abs_from": 0, "abs_to": 2},
        {"abs_from": 5, "abs_to": 9},
    ]
    assert _gaps_between_covered(10, log) == [(3, 4)]


def test_gaps_between_covered_manual_only_ignores_auto_log():
    log = [{"abs_from": 0, "abs_to": 9, "source": "auto_summarize"}]
    assert _gaps_between_covered(10, log, manual_only=False) == []
    assert _gaps_between_covered(10, log, manual_only=True) == [(0, 9)]


# ────────────────────────────────────────────────────────────────────
# _extract_send_to_character_targets
# ────────────────────────────────────────────────────────────────────


def test_extract_send_to_character_targets_collects_unique_targets():
    msgs = [
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "send_to_character", "arguments": {"to": "alice"}}},
        ]},
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "send_to_character", "arguments": {"to": "bob"}}},
        ]},
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "send_to_character", "arguments": {"to": "alice"}}},
        ]},
    ]
    assert _extract_send_to_character_targets(msgs) == ["alice", "bob"]


def test_extract_send_to_character_targets_decodes_stringified_json_arguments():
    msgs = [
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "send_to_character",
                          "arguments": '{"to": "carol"}'}},
        ]},
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "send_to_character",
                          "arguments": "not-json-but-present"}},
        ]},
    ]
    assert _extract_send_to_character_targets(msgs) == ["carol"]


def test_extract_send_to_character_targets_ignores_user_and_unrelated_tools():
    msgs = [
        {"role": "user", "tool_calls": [
            {"function": {"name": "send_to_character", "arguments": {"to": "eve"}}},
        ]},
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "search_in_path", "arguments": {"to": "frank"}}},
        ]},
    ]
    assert _extract_send_to_character_targets(msgs) == []


# ────────────────────────────────────────────────────────────────────
# build_l1 threshold behaviour (no LLM)
# ────────────────────────────────────────────────────────────────────


def test_build_l1_returns_none_below_threshold():
    msgs = [{"role": "user", "content": "短对话" * 10}]
    assert ar.build_l1("alice", msgs) is None


def test_build_l1_returns_summary_above_threshold(temp_character):
    # 总字符数 >= L1_CHAR_THRESHOLD（10_000）才触发
    long_text = "用户问 " + ("x" * 200)
    msgs = [{"role": "user", "content": long_text} for _ in range(60)]
    summary = ar.build_l1("alice", msgs)
    assert summary is not None
    # build_l1 取除末尾 6 条之外的 54 条
    assert summary.user_turns == 54
    assert summary.source == "auto"
    assert summary.msg_indices == (0, len(msgs) - ar.L1_KEEP_RECENT - 1)
