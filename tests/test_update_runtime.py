"""data_shape/update_args.py + tool/builtin._handle_update_runtime 的测试。

覆盖：
1. UpdateRuntimeArgs 强类型校验：合法值、范围越界、枚举错、未知字段、
   类型转换（str→float/int/bool）。
2. _handle_update_runtime 行为：通过 dataclass 路径返回的 [Error]/[OK] 字符串
   与旧实现字符串格式一致。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_shape.update_args import UpdateRuntimeArgs
from tool.builtin import (
    _format_validation_error as _format_validation_error_from_pydantic,
    _handle_update_runtime,
)


# ── UpdateRuntimeArgs dataclass 测试 ──────────────────────────────


class TestUpdateRuntimeArgsHappy:
    def test_empty_args(self):
        """空 dict 合法，所有字段 None，model_fields_set 为空。"""
        a = UpdateRuntimeArgs()
        assert a.ipu is None
        assert a.temperature is None
        assert a.model_fields_set == set()

    def test_explicit_fields_tracked(self):
        """显式传入的字段在 model_fields_set 中。"""
        a = UpdateRuntimeArgs(temperature=0.7, top_p=0.9)
        assert a.temperature == 0.7
        assert a.top_p == 0.9
        assert a.model_fields_set == {"temperature", "top_p"}

    def test_pass_none_explicitly(self):
        """显式传 None 也算 has=True（保留'传了'信息，与 dict 行为一致）。"""
        a = UpdateRuntimeArgs(temperature=None)
        assert a.temperature is None
        assert a.has("temperature") is True

    def test_string_coercion(self):
        """字符串 '1.5' / '500' / 'true' 自动转 float/int/bool。"""
        a = UpdateRuntimeArgs(
            temperature="1.5",
            max_icp="500",
            thinking_enabled="true",
        )
        assert a.temperature == 1.5
        assert a.max_icp == 500
        assert a.thinking_enabled is True

    def test_enumeration_case_insensitive(self):
        """reasoning_effort / thinking_mode 大小写归一化。"""
        a = UpdateRuntimeArgs(reasoning_effort="MAX", thinking_mode="Enabled")
        assert a.reasoning_effort == "max"
        assert a.thinking_mode == "enabled"


class TestUpdateRuntimeArgsValidation:
    """校验失败文案与旧代码一致：'[Error] field: must be ..., got {v}'。"""

    @pytest.mark.parametrize("kwargs, field, expected_substr", [
        ({"temperature": 3.5},   "temperature",     "must be in [0, 2]"),
        ({"temperature": -0.1},  "temperature",     "must be in [0, 2]"),
        ({"top_p": 1.5},         "top_p",           "must be in [0, 1]"),
        ({"top_p": -0.1},        "top_p",           "must be in [0, 1]"),
        ({"max_icp": 0},         "max_icp",         "must be positive"),
        ({"max_icp": -5},        "max_icp",         "must be positive"),
        ({"reasoning_effort": "low"},    "reasoning_effort", "must be high/max"),
        ({"reasoning_effort": "HIGH"},   "reasoning_effort", None),  # 合法
        ({"thinking_mode": "maybe"},     "thinking_mode",    "must be enabled/disabled/auto"),
    ])
    def test_validation_messages(self, kwargs, field, expected_substr):
        if expected_substr is None:
            UpdateRuntimeArgs(**kwargs)  # 不该抛
            return
        with pytest.raises(ValidationError) as ei:
            UpdateRuntimeArgs(**kwargs)
        # 用 builtin.py 的格式化 helper 拼出 LLM 字符串，并断言包含关键片段
        msg = _format_validation_error_from_pydantic(ei.value, "update_runtime")
        assert field in msg
        assert expected_substr in msg

    def test_extra_field_rejected(self):
        """未知字段应被拒绝（防止 LLM 拼写错用到错别名字段）。"""
        with pytest.raises(ValidationError) as ei:
            UpdateRuntimeArgs(ipuxx="M2.5")  # 错别字
        msg = _format_validation_error_from_pydantic(ei.value, "update_runtime")
        assert "ipuxx" in msg
        assert "Extra inputs" in msg

    def test_has_helper(self):
        """has() 区分未传 vs 传 None。"""
        a = UpdateRuntimeArgs()
        assert a.has("temperature") is False
        assert a.has("ipu") is False

        b = UpdateRuntimeArgs(temperature=None)
        assert b.has("temperature") is True  # 显式传了 None

        c = UpdateRuntimeArgs(ipu="M2.5")
        assert c.has("ipu") is True


# ── _format_validation_error_from_pydantic 行为 ─────────────────


class TestFormatValidationError:
    def test_simplifies_pydantic_prefix(self):
        """pydantic 'Value error, must be ...' 切成 'must be ...'。"""
        try:
            UpdateRuntimeArgs(temperature=3.5)
        except ValidationError as e:
            msg = _format_validation_error_from_pydantic(e, "update_runtime")
        assert msg == "[Error] temperature: must be in [0, 2], got 3.5"

    def test_aggregates_multiple_errors(self):
        """多个字段错误一次合并返回。"""
        try:
            UpdateRuntimeArgs(temperature=3.5, max_icp=-5)
        except ValidationError as e:
            msg = _format_validation_error_from_pydantic(e, "update_runtime")
        assert "[Error] temperature: must be in [0, 2], got 3.5" in msg
        assert "[Error] max_icp: must be positive, got -5" in msg

    def test_falls_back_on_non_pydantic(self):
        """非 pydantic 异常走 _format_error。"""
        exc = RuntimeError("random")
        msg = _format_validation_error_from_pydantic(exc, "update_runtime")
        assert msg == "[Error] RuntimeError: random"


# ── _handle_update_runtime 集成行为 ──────────────────────────────


@pytest.fixture
def with_alice(tmp_workdir: Path, reset_actor, reset_circuit_breakers):
    """临时角色 + 复位状态 + 让 _current_actor = alice。"""
    from character.registry import registry
    from data_shape import ActorConfig, RoleConfig, IPURuntime
    from tool import builtin

    config = ActorConfig(
        identity=RoleConfig(system_prompt="x", title="T", traits=""),
        runtime=IPURuntime(
            provider="anthropic",
            ipu="claude",
            temperature=1.0,
            top_p=0.95,
            max_icp=2048,
            thinking_mode="auto",
            reasoning_effort="high",
            thinking_enabled=True,
        ),
    )
    registry.create("alice", config)  # create 内已 ensure_dirs
    builtin._current_actor = "alice"
    return None


class TestHandleUpdateRuntime:
    def test_no_args_returns_no_changes(self, with_alice):
        """无字段传入 → [OK] no changes。"""
        result = _handle_update_runtime({})
        assert result == "[OK] no changes (all values match current)"

    def test_temperature_out_of_range(self, with_alice):
        """temperature 超范围 → 与旧文案一致。"""
        result = _handle_update_runtime({"temperature": 3.5})
        assert "[Error]" in result
        assert "temperature" in result
        assert "must be in [0, 2]" in result
        assert "got 3.5" in result

    def test_top_p_out_of_range(self, with_alice):
        result = _handle_update_runtime({"top_p": 1.5})
        assert "top_p" in result
        assert "must be in [0, 1]" in result

    def test_max_icp_non_positive(self, with_alice):
        result = _handle_update_runtime({"max_icp": -5})
        assert "max_icp" in result
        assert "must be positive" in result

    def test_invalid_enum(self, with_alice):
        result = _handle_update_runtime({"reasoning_effort": "low"})
        assert "reasoning_effort" in result
        assert "must be high/max" in result

    def test_invalid_thinking_mode(self, with_alice):
        result = _handle_update_runtime({"thinking_mode": "maybe"})
        assert "thinking_mode" in result
        assert "must be enabled/disabled/auto" in result

    def test_unknown_field_rejected(self, with_alice):
        """未知字段被 model 拒绝。"""
        result = _handle_update_runtime({"ipuxx": "M2.5"})
        assert "ipuxx" in result
        assert "Extra inputs" in result

    def test_aggregation_of_multiple_errors(self, with_alice):
        """多字段错误一次性返回，LLM 一次看到全部。"""
        result = _handle_update_runtime({"temperature": 3.5, "max_icp": -1})
        assert "temperature" in result
        assert "max_icp" in result

    def test_string_coercion_works(self, with_alice):
        """字符串 '0.7' 走 validator 转 float。"""
        result = _handle_update_runtime({"temperature": "0.7"})
        assert result.startswith("[OK]")
        assert "temperature=0.7" in result

    def test_valid_update_returns_ok(self, with_alice):
        """合法字段传入 → [OK]，变更日志列出。"""
        result = _handle_update_runtime({"temperature": 0.7, "max_icp": 4096})
        assert result.startswith("[OK] runtime updated")
        assert "temperature=0.7" in result
        assert "max_icp=4096" in result


# ── _apply_field helper 单元测试 ────────────────────────────────


class TestApplyField:
    """_apply_field 是 _handle_update_runtime 中抽出的「无副作用赋值」helper。"""

    @staticmethod
    def _fake_rt():
        """构造一个最小的类 rt 对象：只要有这些可写属性即可。"""
        class _RT:
            temperature = 1.0
            top_p = 0.95
            max_icp = 2048
            thinking_mode = "auto"
            reasoning_effort = "high"
            thinking_enabled = True
            ipu = "2.7"
        return _RT()

    def test_field_not_set_skipped(self):
        """字段未在 args 中提供 → 不改 rt、不写 changes。"""
        from tool.builtin import _apply_field

        args = UpdateRuntimeArgs()
        rt = self._fake_rt()
        changes: list[str] = []
        _apply_field(args, rt, "temperature", changes)
        assert rt.temperature == 1.0  # 未改
        assert changes == []

    def test_field_set_with_value(self):
        """字段已提供 → 赋值并追加日志。"""
        from tool.builtin import _apply_field

        args = UpdateRuntimeArgs(temperature=0.7)
        rt = self._fake_rt()
        changes: list[str] = []
        _apply_field(args, rt, "temperature", changes)
        assert rt.temperature == 0.7
        assert changes == ["temperature=0.7"]

    def test_zero_value_is_applied(self):
        """零值（0.0）是合法值，不是"未提供"——必须应用。"""
        from tool.builtin import _apply_field

        args = UpdateRuntimeArgs(temperature=0.0)
        rt = self._fake_rt()
        changes: list[str] = []
        _apply_field(args, rt, "temperature", changes)
        assert rt.temperature == 0.0
        assert changes == ["temperature=0.0"]

    def test_different_fields(self):
        """helper 通用：top_p / max_icp / thinking_mode 都能跑。"""
        from tool.builtin import _apply_field

        args = UpdateRuntimeArgs(top_p=0.5, max_icp=512, thinking_mode="disabled")
        rt = self._fake_rt()
        changes: list[str] = []
        _apply_field(args, rt, "top_p", changes)
        _apply_field(args, rt, "max_icp", changes)
        _apply_field(args, rt, "thinking_mode", changes)
        assert rt.top_p == 0.5
        assert rt.max_icp == 512
        assert rt.thinking_mode == "disabled"
        assert changes == [
            "top_p=0.5", "max_icp=512", "thinking_mode=disabled",
        ]

    def test_explicit_value_overrides_args(self):
        """value 显式给 → 用给定值，不读 args（互斥逻辑里的清空/开启用）。"""
        from tool.builtin import _apply_field

        args = UpdateRuntimeArgs()  # 不提供 reasoning_effort
        rt = self._fake_rt()
        changes: list[str] = []
        _apply_field(
            args, rt, "reasoning_effort", changes,
            value="",
            log_value="reasoning_effort=(自动清除 medium，关闭 thinking 时不可设 reasoning_effort)",
        )
        assert rt.reasoning_effort == ""
        assert changes == ["reasoning_effort=(自动清除 medium，关闭 thinking 时不可设 reasoning_effort)"]

    def test_returns_true_when_applied(self):
        """返回 True：已应用；False：未应用。"""
        from tool.builtin import _apply_field

        args = UpdateRuntimeArgs(temperature=0.5)
        rt = self._fake_rt()
        changes: list[str] = []
        assert _apply_field(args, rt, "temperature", changes) is True
        assert _apply_field(args, rt, "top_p", changes) is False  # 未提供
