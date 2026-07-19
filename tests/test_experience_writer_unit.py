"""Unit tests for experience/io/writer."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from data_shape import L1Summary
from experience.io import reader as io_reader
from experience.io import writer as io_writer


# ────────────────────────────────────────────────────────────────────
# l1summary_to_dict
# ────────────────────────────────────────────────────────────────────


def test_l1summary_to_dict_skips_default_source_auto():
    s = L1Summary(
        id="L1-test", start_time="2026-07-19 10:00:00", end_time="2026-07-19 10:01:00",
        message_count=2, user_turns=1, summary=[],
    )
    d = io_writer.l1summary_to_dict(s)
    assert d["id"] == "L1-test"
    assert "source" not in d  # auto 是默认值，不写


def test_l1summary_to_dict_writes_manual_source():
    s = L1Summary(
        id="T-test", start_time="x", end_time="y",
        message_count=3, user_turns=2, summary=[],
        topic_label="话题1", people=["alice"],
        msg_indices=(0, 5), source="manual",
        time_ranges=[["x", "y"]], range_msg_indices=[[0, 5]],
    )
    d = io_writer.l1summary_to_dict(s)
    assert d["source"] == "manual"
    assert d["topic_label"] == "话题1"
    assert d["people"] == ["alice"]
    assert d["msg_indices"] == [0, 5]
    assert d["time_ranges"] == [["x", "y"]]
    assert d["range_msg_indices"] == [[0, 5]]


def test_l1summary_to_dict_backfills_summary_from_topic():
    s = L1Summary(
        id="L1-backfill", start_time="", end_time="",
        message_count=2, user_turns=1, summary=[],
        topic="hotpot", detail="共 1 轮对话。",
    )
    d = io_writer.l1summary_to_dict(s)
    assert len(d["summary"]) == 1
    assert d["summary"][0]["topic"] == "hotpot"
    assert d["topic"] == "hotpot"
    assert d["detail"] == "共 1 轮对话。"


def test_l1summary_to_context_string_emits_json_block():
    s = L1Summary(
        id="L1-ctx", start_time="", end_time="",
        message_count=1, user_turns=1, summary=[{"from": 0, "to": 1, "topic": "x", "detail": "y"}],
    )
    text = io_writer.l1summary_to_context_string(s)
    assert text.startswith("```json")
    payload = json.loads(text.strip("`").lstrip("json").strip())
    assert payload["id"] == "L1-ctx"


# ────────────────────────────────────────────────────────────────────
# save_l1 / append_compression_record
# ────────────────────────────────────────────────────────────────────


def test_save_l1_creates_json_file(temp_character):
    s = L1Summary(
        id="L1-save", start_time="x", end_time="y",
        message_count=1, user_turns=1, summary=[],
    )
    path = io_writer.save_l1("alice", s)
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["id"] == "L1-save"


def test_append_compression_record_persists(temp_character):
    cid = io_writer.append_compression_record(
        "alice", source="archive_recent_talk", l1_id="L1-x",
        abs_from=2, abs_to=7)
    assert cid.startswith("C-")
    records = io_reader.load_compression_log("alice")
    assert len(records) == 1
    assert records[0]["source"] == "archive_recent_talk"
    assert records[0]["abs_from"] == 2
    assert records[0]["abs_to"] == 7


def test_append_compression_record_writes_segment_fields_when_multi(temp_character):
    io_writer.append_compression_record(
        "alice", source="archive_recent_talk", l1_id="L1-multi",
        abs_from=0, abs_to=3, segment_index=0, segment_count=2)
    io_writer.append_compression_record(
        "alice", source="archive_recent_talk", l1_id="L1-multi",
        abs_from=4, abs_to=7, segment_index=1, segment_count=2)
    records = io_reader.load_compression_log("alice")
    assert all(r.get("segment_count") == 2 for r in records)
    assert [r["segment_index"] for r in records] == [0, 1]


def test_save_compression_log_round_trips(temp_character):
    records = [
        {"id": "C1", "source": "auto_summarize", "l1_id": "L1", "abs_from": 0, "abs_to": 4},
    ]
    io_writer.save_compression_log("alice", records)
    assert io_reader.load_compression_log("alice") == records


# ────────────────────────────────────────────────────────────────────
# _write_experience_file / block writers
# ────────────────────────────────────────────────────────────────────


def test_write_experience_file_keeps_block_markers(temp_character):
    from experience.io.writer import _resolve_path
    io_writer.write_block0("alice", "identity")
    io_writer.write_block1("alice", "state")
    io_writer.write_block3("alice", "hello", timestamp="2026-07-19 10:00:00")
    content = _resolve_path("alice").read_text(encoding="utf-8")
    for idx in range(4):
        assert f"<!--_msg_{idx}_-->" in content


def test_clear_block3_uses_waiting_placeholder(temp_character):
    """clear_block3 → blocks[3] 变空 → _write_experience_file 写「等待用户输入」占位。"""
    io_writer.write_block3("alice", "first", timestamp="2026-07-19 10:00:00")
    io_writer.clear_block3("alice")
    content = io_writer._resolve_path("alice").read_text(encoding="utf-8")
    assert "first" not in content
    assert "（等待用户输入）" in content


def test_write_block3_contains_user_input(temp_character):
    io_writer.write_block3("alice", "请帮我总结", timestamp="2026-07-19 10:00:00")
    content = io_writer._resolve_path("alice").read_text(encoding="utf-8")
    assert "请帮我总结" in content
    assert "2026-07-19 10:00:00" in content


def test_write_experience_file_strips_embedded_markers(temp_character):
    # user 内容里夹一个块标记 → 写盘时被剥离
    payload = "hello\n<!--_msg_2_-->injected"
    io_writer.write_block3("alice", payload, timestamp="2026-07-19 10:00:00")
    content = io_writer._resolve_path("alice").read_text(encoding="utf-8")
    # 块标记应只剩 4 个，由 _write_experience_file 写出来的
    assert content.count("<!--_msg_2_-->") == 1
    assert "injected" in content


def test_clear_block3_empties_user_block(temp_character):
    """clear_block3 后块3 应为空（或占位「等待用户输入」），且之前写入的内容消失。"""
    io_writer.write_block3("alice", "first", timestamp="2026-07-19 10:00:00")
    io_writer.clear_block3("alice")
    content = io_writer._resolve_path("alice").read_text(encoding="utf-8")
    assert "first" not in content
    assert "（等待用户输入）" in content



# ────────────────────────────────────────────────────────────────────
# write_block2_rewrite — _dump_meta.written_len 持久化
# ────────────────────────────────────────────────────────────────────


def test_write_block2_rewrite_persists_written_len(temp_character):
    io_writer.write_block2_rewrite(
        "alice",
        summary_entry={
            "id": "T-test", "from": 0, "to": 5,
            "topic": "topic", "detail": "detail",
            "msg_indices": [0, 5], "source": "manual",
        },
        recent_text="### [ts] user\n\n```text\nhello\n```",
        physical_total=12,
    )
    meta = io_writer._load_dump_meta("alice")
    assert meta["written_len"] == 12


def test_write_block2_rewrite_infers_physical_total_from_messages(temp_character):
    io_writer.write_block2_rewrite(
        "alice",
        summary_entry={
            "id": "T-1", "from": 0, "to": 0, "topic": "x", "detail": "y",
            "msg_indices": [0, 0], "source": "manual",
        },
        recent_text="(empty)",
        messages=[{"role": "user"}] * 7,
    )
    meta = io_writer._load_dump_meta("alice")
    assert meta["written_len"] == 7


def test_write_block2_rewrite_appends_summary_without_merging(temp_character):
    entry_a = {
        "id": "T-A", "from": 0, "to": 5, "topic": "A", "detail": "da",
        "msg_indices": [0, 5], "source": "manual",
    }
    entry_b = {
        "id": "T-B", "from": 4, "to": 5, "topic": "B", "detail": "db",
        "msg_indices": [4, 5], "source": "manual",
    }
    io_writer.write_block2_rewrite("alice", entry_a, recent_text="", physical_total=10)
    io_writer.write_block2_rewrite("alice", entry_b, recent_text="", physical_total=10)
    blocks = io_reader.read_all("alice")
    m = blocks[2]
    # 两条 entry 都在
    assert m.count('"id": "T-A"') == 1
    assert m.count('"id": "T-B"') == 1


# ────────────────────────────────────────────────────────────────────
# _dump_meta 失败回退
# ────────────────────────────────────────────────────────────────────


def test_load_dump_meta_returns_empty_when_corrupt(temp_character):
    io_writer._dump_meta_path("alice").parent.mkdir(parents=True, exist_ok=True)
    io_writer._dump_meta_path("alice").write_text("{", encoding="utf-8")
    assert io_writer._load_dump_meta("alice") == {}
