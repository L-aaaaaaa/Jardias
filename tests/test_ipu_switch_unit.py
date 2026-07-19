"""Unit tests for yinao/launcher/ipu_switch."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from data_shape import IPUSwitch
from yinao.launcher import ipu_switch
from yinao.launcher.ipu_switch import (
    PROVIDER_SPECS,
    format_engine_switch_log,
    inform_ipu_switch,
    pick_fallback_ipu,
    request_switch,
    pop_switch,
    set_active_ipu,
    get_active_ipu,
    sync_config_to_ipu,
)


# ────────────────────────────────────────────────────────────────────
# PROVIDER_SPECS — 三种配置
# ────────────────────────────────────────────────────────────────────


def test_dashscope_provider_spec_uses_enable_thinking():
    spec = PROVIDER_SPECS["dashscope"]
    assert spec.thinking_mode == "enable"
    assert spec.stream_opts["include_usage"] is True
    assert spec.reasoning_field == "reasoning_content"


def test_deepseek_provider_spec_uses_toggle_and_inline_reasoning():
    spec = PROVIDER_SPECS["deepseek"]
    assert spec.ipu_default == "v4-pro"
    assert spec.thinking_mode == "toggle"
    assert spec.reasoning_field == "reasoning_content"
    assert spec.reasoning_inline is True


def test_minimax_provider_spec_uses_m3_reasoning_details():
    spec = PROVIDER_SPECS["minimax"]
    assert spec.thinking_mode == "m3"
    assert spec.reasoning_field == "reasoning_details"


# ────────────────────────────────────────────────────────────────────
# _apply_thinking — extra_body 注入
# ────────────────────────────────────────────────────────────────────


def test_apply_thinking_enable_sets_enable_thinking():
    from yinao.launcher.ipu_switch import _apply_thinking, ProviderSpec
    from data_shape import IPUConfig

    config = IPUConfig(ipu="test")
    _apply_thinking(config, ProviderSpec(thinking_mode="enable"))
    assert config.extra_body["enable_thinking"] is True


def test_apply_thinking_disable_omits_enable_key():
    from yinao.launcher.ipu_switch import _apply_thinking, ProviderSpec
    from data_shape import IPUConfig

    config = IPUConfig(ipu="test")
    _apply_thinking(config, ProviderSpec(thinking_mode="disable"))
    assert "enable_thinking" not in config.extra_body
    assert "thinking" not in config.extra_body


def test_apply_thinking_m3_uses_reasoning_split():
    from yinao.launcher.ipu_switch import _apply_thinking, ProviderSpec
    from data_shape import IPUConfig

    config = IPUConfig(ipu="test", thinking_enabled=False)
    _apply_thinking(config, ProviderSpec(thinking_mode="m3"))
    assert config.extra_body["reasoning_split"] is True
    assert config.extra_body["thinking"]["type"] == "disabled"


def test_apply_thinking_toggle_follows_thinking_enabled():
    from yinao.launcher.ipu_switch import _apply_thinking, ProviderSpec
    from data_shape import IPUConfig

    enabled_config = IPUConfig(ipu="t", thinking_enabled=True)
    _apply_thinking(enabled_config, ProviderSpec(thinking_mode="toggle"))
    assert enabled_config.extra_body["thinking"]["type"] == "enabled"

    disabled_config = IPUConfig(ipu="t", thinking_enabled=False)
    _apply_thinking(disabled_config, ProviderSpec(thinking_mode="toggle"))
    assert disabled_config.extra_body["thinking"]["type"] == "disabled"


def test_apply_thinking_respects_extra_body_overrides():
    from yinao.launcher.ipu_switch import _apply_thinking, ProviderSpec
    from data_shape import IPUConfig

    config = IPUConfig(ipu="t")
    spec = ProviderSpec(thinking_mode="enable", extra_body_overrides={"temperature": 0.5})
    _apply_thinking(config, spec)
    assert config.extra_body["temperature"] == 0.5
    assert config.extra_body["enable_thinking"] is True


# ────────────────────────────────────────────────────────────────────
# sync_config_to_ipu — 字段同步
# ────────────────────────────────────────────────────────────────────


def test_sync_config_to_ipu_copies_runtime_fields():
    config = SimpleNamespace(
        runtime=SimpleNamespace(
            temperature=0.7, top_p=0.9, max_icp=2048,
            reasoning_effort="medium", thinking_enabled=True,
        )
    )
    ipu_config = SimpleNamespace()
    sync_config_to_ipu(config, ipu_config)

    assert ipu_config.temperature == 0.7
    assert ipu_config.top_p == 0.9
    assert ipu_config.max_icp == 2048
    assert ipu_config.reasoning_effort == "medium"
    assert ipu_config.thinking_enabled is True


# ────────────────────────────────────────────────────────────────────
# request_switch / pop_switch / set_active_ipu 共享状态
# ────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_switch_state():
    """每次测试前后清理共享状态。"""
    ipu_switch.switch_request = None
    ipu_switch._actual_provider = ""
    ipu_switch._actual_ipu = ""
    yield
    ipu_switch.switch_request = None
    ipu_switch._actual_ipu = ""


def test_request_switch_stores_request_for_provider():
    request_switch("minimax", "M3")
    pending = pop_switch()
    assert isinstance(pending, IPUSwitch)
    assert pending.provider == "minimax"
    assert pending.ipu == "M3"
    assert pop_switch() is None  # 第二次取不到


def test_request_switch_rejects_unknown_provider():
    with pytest.raises(ValueError, match="未知供应商"):
        request_switch("not-a-provider", "any")


def test_request_switch_rejects_unknown_ipu():
    with pytest.raises(ValueError, match="未知智能基元"):
        request_switch("minimax", "bogus-model")


def test_set_active_ipu_round_trips_via_get_active_ipu():
    set_active_ipu("deepseek", "v4-pro")
    assert get_active_ipu() == "v4-pro"


# ────────────────────────────────────────────────────────────────────
# pick_fallback_ipu — 视觉优先
# ────────────────────────────────────────────────────────────────────


def test_pick_fallback_ipu_returns_first_ipu_by_default(monkeypatch):
    fake_registry = {"dashscope": {"m1": "url1", "m2": "url2"}}
    monkeypatch.setattr(ipu_switch, "IPU_REGISTRY", fake_registry)
    monkeypatch.setattr(
        ipu_switch, "get_ipu_capabilities",
        lambda prov, name: "vision" if name == "m2" else "chat")

    assert pick_fallback_ipu("dashscope") == "m1"
    assert pick_fallback_ipu("dashscope", vision_first=True) == "m2"


def test_pick_fallback_ipu_raises_when_provider_empty(monkeypatch):
    monkeypatch.setattr(ipu_switch, "IPU_REGISTRY", {"empty": {}})
    with pytest.raises(ValueError, match="无智能基元"):
        pick_fallback_ipu("empty")


# ────────────────────────────────────────────────────────────────────
# format_engine_switch_log / inform_ipu_switch
# ────────────────────────────────────────────────────────────────────


def test_format_engine_switch_log_minimal():
    out = format_engine_switch_log(
        "dashscope", "qwen-max", "deepseek", "v4-pro",
        "qwen-max (dashscope/qwen-max)", "v4-pro (deepseek/v4-pro)")
    assert out.startswith("[智能基元切换] 引擎从 ")
    assert "dashscope/qwen-max" in out
    assert "deepseek/v4-pro" in out
    assert "原因" not in out


def test_format_engine_switch_log_appends_reason():
    out = format_engine_switch_log(
        "dashscope", "qwen-max", "deepseek", "v4-pro",
        "qwen-max", "v4-pro", reason="rate limit")
    assert "原因: rate limit" in out


def test_inform_ipu_switch_reassures_identity_unchanged():
    note = inform_ipu_switch(
        "dashscope", "qwen-max", "deepseek", "v4-pro",
        "qwen-max", "v4-pro", reason="quota")
    assert "智能基元切换通知" in note
    assert "身份" in note  # 明确说身份未变
    assert "无需再次调用 update_runtime" in note
    assert "原因: quota" in note


def test_inform_ipu_switch_omits_reason_when_empty():
    note = inform_ipu_switch(
        "dashscope", "qwen-max", "deepseek", "v4-pro",
        "qwen-max", "v4-pro")
    assert "原因" not in note
