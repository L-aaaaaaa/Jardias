"""Unit tests for experience/adapter/state (build_round_context, on_state_update)."""
from __future__ import annotations

from collections import deque

import pytest

from experience.adapter import state as state_adapter
from experience.adapter.state import build_round_context, on_state_update
from experience.io import writer as io_writer
from yinao.weaver import icp_tracker
import yinao.weaver.round_state as round_state_mod  # noqa: F401  用 _round_state.last_round 取新值


def _set_round(elapsed, usage=None, finish_reason=None, error=None):
    """通过模块属性调用 set_round_meta（避免 import-binding 的旧值陷阱）。"""
    round_state_mod.set_round_meta(elapsed, usage, finish_reason=finish_reason,
                                   error=error)


# ────────────────────────────────────────────────────────────────────
# build_round_context — ICP 视角的描述
# ────────────────────────────────────────────────────────────────────


def test_build_round_context_returns_empty_when_no_usage(temp_character, reset_global_state):
    _set_round(0.5, None)
    assert build_round_context("alice") == "# 状态"


def test_build_round_context_describes_round_usage(temp_character, reset_global_state):
    _set_round(0.0, {
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "total_tokens": 20,
    })
    text = build_round_context("alice")
    assert "上轮消耗" in text
    assert "本轮输入 12 智点" in text
    assert "输出 8 智点的回答" in text
    assert "合计 20 智点" in text


def test_build_round_context_splits_thinking_and_answer(temp_character, reset_global_state):
    _set_round(0.0, {
        "prompt_tokens": 10,
        "completion_tokens": 7,
        "total_tokens": 17,
        "completion_tokens_details": {"reasoning_tokens": 5},
    })
    text = build_round_context("alice")
    assert "输出 5 智点的思考" in text
    assert "2 智点的回答" in text


def test_build_round_context_includes_cumulative_when_persisted(temp_character, reset_global_state):
    _set_round(0.0, {
        "prompt_tokens": 5,
        "completion_tokens": 3,
        "total_tokens": 8,
    })
    # 在 _dump_meta.json 写入累计
    io_writer._save_dump_meta("alice", {
        "prompt_icp": 100, "completion_icp": 50, "total_icp": 150,
        "thinking_icp": 10,
    })
    text = build_round_context("alice")
    assert "累计消耗" in text
    assert "累计输入 100 智点" in text
    assert "含 10 智点的思考和 40 智点的回答" in text or "含 40 智点的回答" in text


def test_build_round_context_includes_length_truncation_warning(temp_character, reset_global_state):
    _set_round(0.0, {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                   finish_reason="length")
    text = build_round_context("alice")
    assert "截断" in text
    assert "max_icp" in text


def test_build_round_context_includes_error_warning(temp_character, reset_global_state):
    _set_round(0.0, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                   error="rate limited")
    text = build_round_context("alice")
    assert "上轮调用异常" in text
    assert "rate limited" in text


def test_build_round_context_includes_latency_report(temp_character, reset_global_state):
    _set_round(0.0, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
    # 注入两个 provider 的延迟
    icp_tracker.provider_latency.setdefault("prov-A", deque(maxlen=icp_tracker._MAX_LATENCY_SAMPLES)).append(0.5)
    icp_tracker.provider_latency.setdefault("prov-A", deque(maxlen=icp_tracker._MAX_LATENCY_SAMPLES)).append(0.7)
    icp_tracker.provider_latency.setdefault("prov-B", deque(maxlen=icp_tracker._MAX_LATENCY_SAMPLES)).append(1.5)

    text = build_round_context("alice")
    assert "各供应商延迟" in text
    assert "prov-A" in text
    assert "prov-B" in text


def test_build_round_context_skips_latency_when_only_one_provider(temp_character, reset_global_state):
    _set_round(0.0, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
    icp_tracker.provider_latency.setdefault("solo", deque(maxlen=icp_tracker._MAX_LATENCY_SAMPLES)).append(0.5)

    text = build_round_context("alice")
    assert "各供应商延迟" not in text


def test_build_round_context_falls_back_to_global_cumulative(temp_character, reset_global_state):
    """不传 character_name → 使用进程内 cumulative_usage。"""
    _set_round(0.0, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
    icp_tracker.cumulative_usage["total_icp"] = 88

    text = build_round_context(None)
    assert "累计消耗" in text
    assert "88 智点" in text


# ────────────────────────────────────────────────────────────────────
# on_state_update — 写入块1
# ────────────────────────────────────────────────────────────────────


def test_on_state_update_skips_when_empty(temp_character):
    on_state_update("alice", "")  # 不应抛
    blocks = io_writer.read_all("alice") or {1: ""}
    # 块1 应该是空的（没写入）
    assert blocks[1] == "" if 1 in blocks else True


def test_on_state_update_skips_when_only_header(temp_character):
    on_state_update("alice", "# 状态")
    assert io_writer.read_all("alice").get(1, "") == ""


def test_on_state_update_writes_block1(temp_character):
    on_state_update("alice", "# 状态\n**上轮消耗**: 共 5 智点。")
    assert io_writer.read_all("alice")[1] == "# 状态\n**上轮消耗**: 共 5 智点。"


def test_on_state_update_skips_when_unchanged(temp_character):
    on_state_update("alice", "# 状态\n内容 A")
    on_state_update("alice", "# 状态\n内容 A")
    # 第二次不应触发无意义写 — 但要确认内容一致
    assert io_writer.read_all("alice")[1] == "# 状态\n内容 A"


# ────────────────────────────────────────────────────────────────────
# 模块导出
# ────────────────────────────────────────────────────────────────────


def test_module_all_exports_expected_functions():
    assert "build_round_context" in state_adapter.__all__
    assert "on_state_update" in state_adapter.__all__
