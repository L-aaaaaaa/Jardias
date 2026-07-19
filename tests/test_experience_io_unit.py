"""Unit tests for the experience markdown persistence boundary."""
from __future__ import annotations

import json

from data_shape import L1Summary
from experience.io.reader import (
    _parse_user_input_from_message3,
    l1summary_from_dict,
    read_all,
)
from experience.io.writer import (
    _render_block2,
    _write_experience_file,
    append_compression_record,
    clear_block3,
    l1summary_to_context_string,
    l1summary_to_dict,
    save_l1,
    write_block0,
    write_block2_append,
    write_block2_rewrite,
    write_block3,
)


def test_write_and_read_blocks_round_trip(isolated_workspace):
    write_block0("alice", "identity")
    write_block3("alice", "hello", "2026-01-01 00:00:00")

    blocks = read_all("alice")
    assert blocks[0] == "identity"
    assert "2026-01-01 00:00:00" in blocks[3]
    assert _parse_user_input_from_message3(blocks[3]) == {
        "timestamp": "2026-01-01 00:00:00",
        "role": "user",
        "text": "hello",
    }

    clear_block3("alice")
    assert "等待用户输入" in read_all("alice")[3]


def test_write_experience_file_preserves_empty_block_markers(tmp_path):
    path = tmp_path / "experience.md"
    _write_experience_file(path, {0: "zero", 1: "", 2: "two", 3: ""})

    text = path.read_text(encoding="utf-8")
    assert text.count("<!--_msg_") == 4
    assert "<!--_msg_1_-->" in text
    assert "（等待用户输入）" in text


def test_block2_append_creates_skeleton_then_appends(isolated_workspace):
    first = write_block2_append("alice", "first")
    second = write_block2_append("alice", "second", meta=first)

    block = read_all("alice")[2]
    assert "## 摘要" in block
    assert "## 近期对话原文" in block
    assert block.count("first") == 1
    assert block.count("second") == 1
    assert second == first


def test_block2_rewrite_sorts_and_persists_summary(isolated_workspace):
    write_block2_append("alice", "recent")
    meta = write_block2_rewrite(
        "alice",
        {"id": "L1-2", "msg_indices": [5, 8], "summary": []},
        "recent",
        physical_total=9,
    )

    block = read_all("alice")[2]
    assert '"id": "L1-2"' in block
    assert meta["written_len"] == 9


def test_l1_summary_serialization_is_backward_compatible(isolated_workspace):
    summary = L1Summary(id="L1-x", topic="topic", detail="detail", user_turns=2)
    payload = l1summary_to_dict(summary)
    restored = l1summary_from_dict(payload)

    assert payload["summary"] == [{
        "from": 0, "to": 2, "topic": "topic", "detail": "detail",
    }]
    assert restored.msg_indices == (0, 0)
    assert restored.summary == payload["summary"]
    payload_text = l1summary_to_context_string(summary).split("```json\n", 1)[1].split("\n```", 1)[0]
    assert json.loads(payload_text)["id"] == "L1-x"


def test_save_l1_and_append_compression_record(isolated_workspace):
    path = save_l1("alice", L1Summary(id="L1-1", topic="topic"))
    compression_id = append_compression_record("alice", "manual", "L1-1", 1, 4)

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["id"] == "L1-1"
    records = json.loads((path.parent.parent / "compression_log.json").read_text(encoding="utf-8"))
    assert records[0]["l1_id"] == "L1-1"
    assert records[0]["id"] == compression_id


def test_render_block2_is_valid_json_block():
    block = _render_block2([{"id": "L1", "detail": "中文"}], "recent")

    payload = block.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert json.loads(payload)[0]["detail"] == "中文"
