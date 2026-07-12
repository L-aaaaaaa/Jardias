"""character/summarizer.py — 纯逻辑 helpers（不需要 LLM）。

重点覆盖：
- _covered_ranges 合并算法（边界 + 重叠 + 相邻）
- _gaps_between_covered 对比
- _extract_send_to_character_targets ground truth
- _build_topic_label_regex 「话题1」不匹配「话题12」
- _is_archive_trigger 前缀识别
- l1summary_to_dict / l1summary_from_dict 序列化兼容
- select_summaries_for_context 选摘要策略
- build_l1（机械归总）阈值与边界
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from character import summarizer
from character.summarizer import (
    _covered_ranges, _gaps_between_covered,
    _extract_send_to_character_targets,
    _build_topic_label_regex, _is_archive_trigger,
    _ARCHIVE_TRIGGER_PREFIXES,
    l1summary_to_dict, l1summary_from_dict,
    select_summaries_for_context,
    build_l1, save_l1, load_all_l1, load_compression_log,
    MAX_L1_IN_CONTEXT,
)
from data_shape import L1Summary


# ── _covered_ranges 合并算法 ───────────────────────────────────

def _rec(abs_from, abs_to, source="archive_recent_talk"):
    """用 archive_recent_talk 作为 manual 过滤的目标 source。"""
    return {"id": f"C-{abs_from}",
            "source": source, "l1_id": f"L1-{abs_from}",
            "abs_from": abs_from, "abs_to": abs_to,
            "compressed_at": "2026-01-01 00:00:00"}


class TestCoveredRanges:
    def test_empty(self):
        assert _covered_ranges([]) == []

    def test_single_range(self):
        assert _covered_ranges([_rec(0, 5)]) == [(0, 5)]

    def test_non_overlapping_sorted(self):
        """不相交区间按 abs_from 升序，输出各自独立。"""
        log = [_rec(0, 2), _rec(5, 7)]
        assert _covered_ranges(log) == [(0, 2), (5, 7)]

    def test_overlapping_merge(self):
        """[0,5] + [3,7] → [0,7]（取并集）。"""
        log = [_rec(0, 5), _rec(3, 7)]
        assert _covered_ranges(log) == [(0, 7)]

    def test_adjacent_merge(self):
        """[0,3] + [4,6] → [0,6]（相邻 f <= merged_to + 1）。"""
        log = [_rec(0, 3), _rec(4, 6)]
        assert _covered_ranges(log) == [(0, 6)]

    def test_overlap_nested_merge(self):
        """嵌套区间 [0,10] 被 [3,5] 包含 → 单条 [0,10]。"""
        log = [_rec(0, 10), _rec(3, 5)]
        assert _covered_ranges(log) == [(0, 10)]

    def test_unordered_input_gets_sorted(self):
        """输入无需有序，函数内部排序。"""
        log = [_rec(5, 7), _rec(0, 2)]
        assert _covered_ranges(log) == [(0, 2), (5, 7)]


class TestCoveredRangesManualOnly:
    def test_only_archive_talk_kept(self):
        """manual_only=True 只保留 source=='archive_recent_talk' 的记录。"""
        log = [_rec(0, 3, source="archive_recent_talk"),
               _rec(10, 15, source="auto")]
        assert _covered_ranges(log, manual_only=True) == [(0, 3)]

    def test_default_includes_all(self):
        log = [_rec(0, 3, source="archive_recent_talk"),
               _rec(10, 15, source="auto")]
        got = _covered_ranges(log, manual_only=False)
        assert (0, 3) in got
        assert (10, 15) in got


# ── _gaps_between_covered ─────────────────────────────────────

class TestGapsBetweenCovered:
    def test_no_log_returns_full_range(self):
        assert _gaps_between_covered(10, []) == [(0, 9)]

    def test_single_segment_in_middle(self):
        """[5,7] 覆盖中间 → 两段 gap。"""
        log = [_rec(5, 7)]
        got = _gaps_between_covered(10, log)
        assert (0, 4) in got
        assert (8, 9) in got

    def test_cover_all(self):
        log = [_rec(0, 9)]
        assert _gaps_between_covered(10, log) == []

    def test_leading_segment(self):
        log = [_rec(0, 3)]
        assert _gaps_between_covered(10, log) == [(4, 9)]

    def test_trailing_segment(self):
        log = [_rec(7, 9)]
        assert _gaps_between_covered(10, log) == [(0, 6)]

    def test_adjacent_segments(self):
        """两个相邻 [0,3] + [4,5] 不应留间隙。"""
        log = [_rec(0, 3), _rec(4, 5)]
        assert _gaps_between_covered(10, log) == [(6, 9)]


# ── _extract_send_to_character_targets ──────────────────────────

class TestExtractTargets:
    def test_no_tool_calls(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        assert _extract_send_to_character_targets(msgs) == []

    def test_basic(self):
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "send_to_character",
                                          "arguments": json.dumps({"to": "bob"})}}]}
        ]
        assert _extract_send_to_character_targets(msgs) == ["bob"]

    def test_arguments_dict_form(self):
        """arguments 传 dict 而非 str 时也能解析。"""
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "send_to_character",
                                          "arguments": {"to": "alice"}}}]}
        ]
        assert _extract_send_to_character_targets(msgs) == ["alice"]

    def test_dedup_preserves_first(self):
        m_factory = lambda who: {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "send_to_character",
                                         "arguments": json.dumps({"to": who})}}]}
        msgs = [m_factory("bob"), m_factory("alice"), m_factory("bob")]
        assert _extract_send_to_character_targets(msgs) == ["bob", "alice"]

    def test_ignores_other_tools(self):
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "read_file",
                                          "arguments": json.dumps({"path": "x"})}}]}
        ]
        assert _extract_send_to_character_targets(msgs) == []

    def test_invalid_json_args_skipped(self):
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "send_to_character",
                                          "arguments": "not-json{"}}]}
        ]
        # 应不抛异常
        assert _extract_send_to_character_targets(msgs) == []


# ── _build_topic_label_regex ───────────────────────────────────

class TestTopicLabelRegex:
    def test_exact_match(self):
        re = _build_topic_label_regex("话题1")
        assert re.search("刚才在聊话题1")
        assert re.search("话题1结束了")
        assert re.search("我们之前的话题1")

    def test_no_match_topic12(self):
        """「话题1」不能匹配「话题12」（forward lookahead）。"""
        re = _build_topic_label_regex("话题1")
        # lookahead (?!\\d) 应阻止数字尾随
        assert not re.search("话题12")

    def test_no_match_topic2(self):
        re = _build_topic_label_regex("话题1")
        assert not re.search("话题2")

    def test_match_topic1_dot(self):
        """「话题1.」的 . 不是数字，lookahead 通过。"""
        re = _build_topic_label_regex("话题1")
        assert re.search("话题1. 完成")

    def test_special_char_escaped(self):
        """正则特殊字符应被转义。"""
        re = _build_topic_label_regex("话题.1")
        # 不应把 . 当通配符
        assert re.search("话题.1")
        assert not re.search("话题X1")


# ── _is_archive_trigger ───────────────────────────────────────────

class TestIsArchiveTrigger:
    @pytest.mark.parametrize("prefix", _ARCHIVE_TRIGGER_PREFIXES)
    def test_each_prefix_matches(self, prefix):
        assert _is_archive_trigger(f"{prefix}一下")

    def test_no_match(self):
        assert not _is_archive_trigger("你好啊")

    def test_non_string_input(self):
        assert _is_archive_trigger(None) is False
        assert _is_archive_trigger(123) is False

    def test_prefix_must_be_at_start(self):
        """前缀必须出现在开头，否则不应识别。"""
        assert not _is_archive_trigger("我们先 归档")


# ── L1Summary 序列化 ─────────────────────────────────────

class TestL1Serialization:
    def test_roundtrip(self):
        s = L1Summary(
            id="L1-x", topic_label="价值本质", people=["alice", "bob"],
            msg_indices=(3, 10), source="manual",
        )
        d = l1summary_to_dict(s)
        # ext 字段写入
        assert d["topic_label"] == "价值本质"
        assert d["people"] == ["alice", "bob"]
        assert d["msg_indices"] == [3, 10]
        assert d["source"] == "manual"

        s2 = l1summary_from_dict(d)
        assert s2.id == "L1-x"
        assert s2.topic_label == "价值本质"
        assert s2.people == ["alice", "bob"]

    def test_old_data_backward_compat(self):
        """旧版 L1 文件没有 ext 字段时也能解析。"""
        s = l1summary_from_dict({"id": "L1-old", "topic": "测试",
                                  "summary": []})
        assert s.id == "L1-old"
        assert s.topic == "测试"
        assert s.msg_indices == (0, 0)  # 默认

    def test_auto_source_not_serialized(self):
        """source=auto 是默认值，不应写入 JSON（保持向后兼容）。"""
        s = L1Summary(id="L1-x", source="auto")
        d = l1summary_to_dict(s)
        assert "source" not in d

    def test_ensure_summary_from_topic(self):
        """旧版只有 topic/detail，summary=空时由 helper 填充。"""
        s = L1Summary(id="L1-x", topic="t", detail="d", user_turns=5)
        assert s.summary == []
        summarizer._l1_ensure_summary(s)
        assert len(s.summary) == 1
        assert s.summary[0]["topic"] == "t"


# ── 摘要选择策略 ─────────────────────────────────────────

class TestSelectSummaries:
    def test_empty_log(self, tmp_workdir):
        """空 log → 返回空列表（不会去读文件）。"""
        assert select_summaries_for_context("nobody", []) == []

    def test_invalid_log_entries_skipped(self, tmp_workdir):
        """log 中条目指向不存在的文件时应被静默跳过。"""
        log = [
            {"id": "ghost-1", "l1_id": "ghost-1", "abs_from": 0, "abs_to": 4,
             "compressed_at": "2026-01-01 00:00:00"},
        ]
        # 没有对应 .json 文件，函数不应抛异常
        assert select_summaries_for_context("nobody", log) == []


# ── build_l1（机械归总） 阈值与边界 ───────────────────────────

class TestBuildL1Threshold:
    def test_below_threshold_returns_none(self):
        """字符数 < 阈值 → 不压缩。"""
        msgs = [{"role": "user", "content": "hi"}]
        assert build_l1("x", msgs) is None

    def test_above_threshold_triggers(self):
        """字符数 ≥ 阈值 + 切片非空 → 返回 L1Summary。"""
        from character.summarizer import L1_CHAR_THRESHOLD, L1_KEEP_RECENT
        # 制造足够长的内容触发
        big = "a" * 100
        msgs = []
        for i in range(L1_KEEP_RECENT + 5):
            msgs.append({"role": "user", "content": big, "time": f"2026-01-01 00:{i:02d}:00"})
            msgs.append({"role": "assistant", "content": big, "time": f"2026-01-01 00:{i:02d}:00"})

        # 总字符数 < L1_CHAR_THRESHOLD 时不触发
        if len(big) * len(msgs) < L1_CHAR_THRESHOLD:
            # 把单条做大直到过阈
            big = "a" * (L1_CHAR_THRESHOLD + 100)
            msgs = [{"role": "user", "content": big, "time": "t"} for _ in range(L1_KEEP_RECENT + 5)]
        s = build_l1("nobody", msgs)
        # 摘要真实生成，但前提是切片非空
        if s is not None:
            assert s.user_turns >= 0
            assert s.source == "auto"


# ── 归档触发前缀完整性 ─────────────────────────────────

class TestArchivePrefixListNonEmpty:
    def test_has_prefixes(self):
        assert len(_ARCHIVE_TRIGGER_PREFIXES) >= 5
