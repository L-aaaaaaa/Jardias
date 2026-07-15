"""build_round_context 端到端测试。

覆盖 yinao.ipu_client.ipu_context.build_round_context 的真实调用流程：

  模拟真实使用：建角色目录 → 落 _dump_meta.json → 写 last_round →
  写 icp_tracker 进程内累计 → 调用 build_round_context("角色") →
  验证输出字符串。

依赖 icp_tracker 的进程内 dict + _load_cumulative 的 JSON 读路径。
reset_circuit_breakers fixture 同时清零 ipu_switch / icp_tracker / circuit_breaker。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_shape import RoundMeta
from experience import icp_cost as ctx_mod
from yinao.weaver.icp_tracker import (
    cumulative_usage, provider_latency, update_cumulative, )


# ── Fixture：隔离环境下的"角色 + 持久化累计" ────────────────────


@pytest.fixture
def alice(tmp_workdir: Path, reset_circuit_breakers) -> str:
    """创建一个 alice 角色目录，返回角色名。"""
    from character import ensure_dirs
    ensure_dirs("alice")
    return "alice"


@pytest.fixture
def alice_with_meta(tmp_workdir: Path, reset_circuit_breakers) -> str:
    """alice + 写入 _dump_meta.json 持久化累计字段。"""
    from character import ensure_dirs, get_character_dir
    ensure_dirs("alice")
    meta_path = get_character_dir("alice") / "_dump_meta.json"
    meta_path.write_text(
        json.dumps({
            "prompt_icp": 50000,
            "completion_icp": 12000,
            "total_icp": 62000,
            "thinking_icp": 3500,
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return "alice"


# ── 工具：直接写 last_round ──────────────────────────────────────


def _set_round(usage: dict | None = None, finish_reason: str | None = None,
        error: str | None = None, api_time: float = 0.0) -> None:
    ctx_mod.last_round = RoundMeta(
        api_time=api_time, usage=usage, finish_reason=finish_reason, error=error,
    )


# ════════════════════════════════════════════════════════════════════
# ── 基础场景 ─────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════


class TestRoundContextE2E:
    """build_round_context 的真实调用测试。"""

    def test_empty_initial_round(self, alice: str):
        """无 usage / 无 finish_reason / 无 error → 只返回头 '# 状态'。"""
        _set_round()
        r = ctx_mod.build_round_context(alice)
        assert r == "# 状态"

    def test_with_usage_no_cumulative(self, alice: str):
        """有 usage，但 cumulative_usage=0 且无 _dump_meta.json → 只输出'上轮消耗'。"""
        _set_round(usage={
            "prompt_tokens": 1234, "completion_tokens": 567,
            "total_tokens": 1801,
        })
        r = ctx_mod.build_round_context(alice)
        assert "**上轮消耗**" in r
        assert "本轮输入 1234 智点" in r
        assert "567 智点的回答" in r
        assert "合计 1801 智点" in r
        # 没有累计数据 → 不出现'累计消耗'
        assert "**累计消耗**" not in r

    def test_with_usage_and_persistent_cumulative(self, alice_with_meta: str):
        """有 usage + alice 的 _dump_meta.json → 完整两段。"""
        _set_round(usage={
            "prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180,
        })
        r = ctx_mod.build_round_context(alice_with_meta)
        # 上轮段
        assert "本轮输入 100 智点" in r
        # 累计段
        assert "**累计消耗**" in r
        assert "累计输入 50000 智点" in r
        assert "含 3500 智点的思考和 8500 智点的回答" in r
        assert "累计合计 62000 智点" in r

    def test_thinking_token_in_completion(self, alice_with_meta: str):
        """有 thinking tokens → 完成段拆为'X 思考 + Y 回答'。"""
        _set_round(usage={
            "prompt_tokens": 100,
            "completion_tokens": 1000,
            "total_tokens": 1100,
            "completion_tokens_details": {"reasoning_tokens": 700},
        })
        r = ctx_mod.build_round_context(alice_with_meta)
        # completion - reasoning = 300
        assert "输出 700 智点的思考，300 智点的回答" in r


# ════════════════════════════════════════════════════════════════════
# ── 截断 / 错误 通知段 ───────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════


class TestRoundContextNotices:

    def test_truncation_notice(self, alice: str):
        """finish_reason='length' → 出现截断段。"""
        _set_round(usage={
            "prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20,
        }, finish_reason="length")
        r = ctx_mod.build_round_context(alice)
        assert "⚠️ **上轮回复被截断**（达到 max_icp 限制）" in r
        assert "update_runtime" in r

    def test_finish_reason_stop_no_truncation(self, alice: str):
        """finish_reason='stop' → 不出现截断段。"""
        _set_round(usage={"prompt_tokens": 10, "completion_tokens": 10,
                          "total_tokens": 20}, finish_reason="stop")
        r = ctx_mod.build_round_context(alice)
        assert "上轮回复被截断" not in r

    def test_error_notice(self, alice: str):
        """error 非空 → 出现错误段。"""
        _set_round(error="TimeoutError: 后端响应超时")
        r = ctx_mod.build_round_context(alice)
        assert "⚠️ **上轮调用异常**: TimeoutError: 后端响应超时" in r

    def test_truncation_and_error_coexist(self, alice: str):
        """截断 + 错误同时出现 → 两段同时存在。"""
        _set_round(
            usage={"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            finish_reason="length", error="partial: 服务端甩手",
        )
        r = ctx_mod.build_round_context(alice)
        assert "上轮回复被截断" in r
        assert "上轮调用异常" in r


# ════════════════════════════════════════════════════════════════════
# ── 延迟对比段 ───────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════


class TestRoundContextLatency:

    def test_single_provider_skips_latency(self, alice: str):
        """只 1 个 provider 在 provider_latency → 不出延迟段（需要 > 1 个才会出）。"""
        _set_round()
        update_cumulative(None, "dashscope", 2.5)
        r = ctx_mod.build_round_context(alice)
        assert "各供应商延迟" not in r

    def test_multi_provider_shows_latency(self, alice: str):
        """2+ 个 provider → 出现延迟段，按均值升序。"""
        _set_round()
        update_cumulative(None, "dashscope", 3.0)
        update_cumulative(None, "dashscope", 4.0)  # 均值 3.5
        update_cumulative(None, "deepseek", 2.0)
        update_cumulative(None, "deepseek", 2.5)  # 均值 2.25
        r = ctx_mod.build_round_context(alice)
        assert "**各供应商延迟**" in r
        # deepseek 2.2s 应排在 dashscope 3.5s 之前
        deepseek_pos = r.index("deepseek")
        dashscope_pos = r.index("dashscope")
        assert deepseek_pos < dashscope_pos, "延迟应按均值升序排列"

    def test_latency_avg_with_max_samples(self, alice: str):
        """provider_latency 队列 maxlen=5 → 超过 5 个样本只保留最近 5 个。"""
        _set_round()
        # 6 个样本的均值 = (1+2+3+4+5+6)/6 = 3.5，但只保留最近 5 个 → 2,3,4,5,6 均值 = 4.0
        for t in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0):
            update_cumulative(None, "deepseek", t)
        r = ctx_mod.build_round_context(alice)
        # 要让延迟段出现必须有 2+ provider，补一个
        update_cumulative(None, "dashscope", 1.0)
        r = ctx_mod.build_round_context(alice)
        # 提取 deepseek 后面的均值数字
        import re
        m = re.search(r"deepseek\s+([\d.]+)s", r)
        assert m, f"应找到 deepseek 均值，实际: {r}"
        assert m.group(1) == "4.0", f"应得 4.0（最近 5 个样本），实得 {m.group(1)}"


# ════════════════════════════════════════════════════════════════════
# ── 边界：用 icp_tracker 进程内累计（不传 character_name）──
# ════════════════════════════════════════════════════════════════════


class TestRoundContextWithoutCharacter:
    """向后兼容：不传 character_name 时退回进程内 cumulative_usage。"""

    def test_in_memory_cumulative_when_no_character(self, alice: str):
        _set_round(usage={
            "prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10,
        })
        # 走 update_cumulative 累加进 icp_tracker 进程内 dict
        update_cumulative(
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
             "completion_tokens_details": {"reasoning_tokens": 20}},
            "dashscope", 1.0,
        )
        assert cumulative_usage["prompt_icp"] == 100
        assert cumulative_usage["thinking_icp"] == 20

        r = ctx_mod.build_round_context()  # 不传 character_name
        assert "**累计消耗**" in r
        assert "累计输入 100 智点" in r
        assert "含 20 智点的思考和 30 智点的回答" in r

    def test_persistent_meta_takes_priority_over_memory(self, alice_with_meta: str):
        """传 character_name 时优先从 _dump_meta.json 读，不看进程内累计。"""
        _set_round(usage={
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
        })
        # 进程内累计 _dump_meta.json 完全不一致
        cumulative_usage.update({
            "prompt_icp": 1, "completion_icp": 1, "total_icp": 2, "thinking_icp": 0,
        })
        r = ctx_mod.build_round_context(alice_with_meta)
        assert "累计输入 50000 智点" in r
        assert "累计输入 1 智点" not in r


# ════════════════════════════════════════════════════════════════════
# ── _load_cumulative 容错 ────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════


class TestLoadCumulativeRobustness:
    """_load_cumulative 路径上：文件不存在 / JSON 损坏 / 字段缺失。"""

    def test_no_meta_file_returns_zeros(self, alice: str):
        """无 _dump_meta.json → 仍调用进程内分支（fallback）。"""
        _set_round(usage={
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
        })
        # 无 meta 文件 + 进程内也无累计 → 不输出'累计消耗'
        r = ctx_mod.build_round_context(alice)
        assert "**累计消耗**" not in r

    def test_meta_file_with_missing_fields(self, tmp_workdir: Path, reset_circuit_breakers):
        """_dump_meta.json 缺字段 → 不抛异常,缺字段按 0 算。

        当前行为：缺 total_icp → 整段'累计消耗'被 if cu.get('total_icp', 0) > 0 拦截掉。
        验证点：不会抛异常 + 不会输出伪造的零值。
        """
        from character import ensure_dirs, get_character_dir
        ensure_dirs("bob")
        meta_path = get_character_dir("bob") / "_dump_meta.json"
        meta_path.write_text(
            json.dumps({"prompt_icp": 1000}, ensure_ascii=False), encoding="utf-8",
        )
        _set_round(usage={
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
        })
        r = ctx_mod.build_round_context("bob")
        # 不抛异常 + total_icp 缺字段 → 累计段不出（避免'累计合计 0 智点'）
        assert "**累计消耗**" not in r
        assert "累计合计 0 智点" not in r

    def test_meta_file_corrupted_json(self, tmp_workdir: Path, reset_circuit_breakers):
        """_dump_meta.json 损坏 → 走异常路径，返回零字典，不崩。"""
        from character import ensure_dirs, get_character_dir
        ensure_dirs("carol")
        meta_path = get_character_dir("carol") / "_dump_meta.json"
        meta_path.write_text("{not json}", encoding="utf-8")
        _set_round(usage={
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
        })
        r = ctx_mod.build_round_context("carol")
        # 不抛异常 + 没累计段
        assert "**累计消耗**" not in r


# ════════════════════════════════════════════════════════════════════
# ── 集成：模拟一次完整流程后 build_round_context 的输出 ───────
# ════════════════════════════════════════════════════════════════════


class TestFullRoundThenContext:
    """模拟一个完整回合：LLM 返回 usage+error → 然后本轮外层注入。"""

    def test_round_with_error_accumulates_latency(self, alice: str):
        # 这一轮本 provider 调用失败，但仍记了延迟
        update_cumulative(None, "dashscope", 2.5)
        _set_round(error="HTTP 500")
        r = ctx_mod.build_round_context(alice)
        assert "上轮调用异常" in r
        # 只有一个 provider → 延迟段不出
        assert "各供应商延迟" not in r

    def test_round_with_full_metadata(self, alice: str):
        """完整一轮：usage + finish_reason=stop + 至少 2 个 provider。"""
        update_cumulative(
            {"prompt_tokens": 100, "completion_tokens": 200,
             "total_tokens": 300,
             "completion_tokens_details": {"reasoning_tokens": 50}},
            "dashscope", 1.5,
        )
        update_cumulative(None, "deepseek", 2.0)
        _set_round(
            usage={"prompt_tokens": 100, "completion_tokens": 200,
                   "total_tokens": 300,
                   "completion_tokens_details": {"reasoning_tokens": 50}},
            finish_reason="stop", api_time=1.5,
        )
        r = ctx_mod.build_round_context(alice)
        assert r.startswith("# 状态")
        assert "**上轮消耗**" in r
        assert "本轮输入 100 智点" in r
        assert "输出 50 智点的思考" in r
        # 无累计段（无 _dump_meta.json）
        # 但有延迟段（2 个 provider）
        assert "**各供应商延迟**" in r
        # 截断 / 错误段不应出现
        assert "截断" not in r
        assert "异常" not in r
