"""Unit tests for launcher resolution and provider switch helpers."""
from __future__ import annotations

import pytest

from data_shape import ActorConfig, IPUConfig
from yinao.launcher import config_resolver
from yinao.launcher import ipu_switch


def test_config_resolver_exposes_registered_provider_and_ipu():
    providers = config_resolver.list_ipu_providers()

    assert "minimax" in providers
    assert "2.7" in config_resolver.list_ipus("minimax")
    assert config_resolver.choose_ipu("minimax", "2.7") == "MiniMax-M2.7"
    assert config_resolver.resolve_ipu_provider("2.7") == "minimax"


def test_config_resolver_reports_unknown_values():
    with pytest.raises(KeyError, match="不存在"):
        config_resolver.choose_ipu("minimax", "missing")
    with pytest.raises(KeyError, match="不存在"):
        config_resolver.choose_ipu_provider("missing")


def test_provider_switch_thinking_modes_update_extra_body():
    config = IPUConfig(thinking_enabled=False)
    ipu_switch._apply_thinking(config, ipu_switch.ProviderSpec(thinking_mode="toggle"))
    assert config.extra_body["thinking"] == {"type": "disabled"}

    config = IPUConfig(thinking_enabled=True)
    ipu_switch._apply_thinking(config, ipu_switch.ProviderSpec(thinking_mode="m3"))
    assert config.extra_body["reasoning_split"] is True


def test_sync_config_to_ipu_copies_runtime_values():
    actor = ActorConfig()
    actor.runtime.temperature = 0.2
    actor.runtime.top_p = 0.7
    actor.runtime.max_icp = 100
    actor.runtime.reasoning_effort = "max"
    actor.runtime.thinking_enabled = False
    target = IPUConfig()

    ipu_switch.sync_config_to_ipu(actor, target)

    assert target.temperature == 0.2
    assert target.top_p == 0.7
    assert target.max_icp == 100
    assert target.reasoning_effort == "max"
    assert target.thinking_enabled is False


def test_switch_log_and_notification_preserve_transition_details():
    log = ipu_switch.format_engine_switch_log(
        "old", "a", "new", "b", "old-full", "new-full", "reason",
    )
    note = ipu_switch.inform_ipu_switch(
        "old", "a", "new", "b", "old-full", "new-full", "reason",
    )

    assert log.startswith("[智能基元切换]")
    assert "old-full" in log and "new-full" in log and "reason" in log
    assert "身份" in note and "reason" in note


def test_switch_request_is_validated_and_consumed(reset_global_state):
    provider = "minimax"
    ipu = "2.7"
    ipu_switch.request_switch(provider, ipu)

    request = ipu_switch.pop_switch()
    assert request.provider == provider
    assert request.ipu == ipu
    assert ipu_switch.pop_switch() is None

    with pytest.raises(ValueError, match="未知供应商"):
        ipu_switch.request_switch("missing", "ipu")
