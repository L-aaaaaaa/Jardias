"""Fast unit tests for the data-shape layer."""
from __future__ import annotations

import dataclasses
import json

import pytest
from pydantic import ValidationError

from data_shape import (
    ActorConfig,
    IPUConfig,
    IPUConfigFile,
    IPUEntry,
    IPUProviderConfig,
    IPURuntime,
    L1Summary,
    RoleConfig,
    TopicSegment,
    UpdateRuntimeArgs,
)


def test_actor_config_uses_independent_nested_defaults():
    first = ActorConfig()
    second = ActorConfig()

    first.identity.title = "first"
    assert second.identity.title == ""
    assert first.runtime.provider == "minimax"


def test_dataclass_configuration_round_trips_as_json():
    config = ActorConfig(
        identity=RoleConfig(system_prompt="hello", title="tester"),
        runtime=IPURuntime(provider="deepseek", ipu="v4-flash"),
    )

    payload = dataclasses.asdict(config)
    assert json.loads(json.dumps(payload)) == payload
    assert payload["runtime"]["provider"] == "deepseek"


def test_l1_summary_defaults_are_not_shared():
    first = L1Summary(id="one")
    second = L1Summary(id="two")

    first.key_events.append("event")
    first.summary.append({"topic": "x"})
    assert second.key_events == []
    assert second.summary == []
    assert first.msg_indices == (0, 0)


def test_topic_segment_keeps_explicit_message_range():
    segment = TopicSegment(2, 8, "topic", "detail", ["point"])

    assert (segment.from_msg_idx, segment.to_msg_idx) == (2, 8)
    assert segment.key_points == ["point"]


def test_ipu_config_allows_provider_specific_extra_fields():
    config = IPUConfig(custom_body={"thinking": True})

    assert config.model_dump()["custom_body"] == {"thinking": True}
    assert config.max_icp == 2048


def test_ipu_config_file_serializes_nested_provider_data():
    config = IPUConfigFile(providers=[IPUProviderConfig(
        name="test",
        api_key_env="TEST_KEY",
        base_url="https://example.test",
        ipus={"fast": {"id": "fast-v1", "caps": ["text"]}},
    )])

    payload = config.model_dump()
    assert payload["providers"][0]["ipus"]["fast"]["id"] == "fast-v1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", -0.1),
        ("temperature", 2.1),
        ("top_p", -0.1),
        ("top_p", 1.1),
        ("max_icp", 0),
        ("reasoning_effort", "low"),
        ("thinking_mode", "manual"),
    ],
)
def test_update_runtime_args_rejects_invalid_values(field, value):
    with pytest.raises(ValidationError):
        UpdateRuntimeArgs(**{field: value})


def test_update_runtime_args_normalizes_values_and_tracks_explicit_fields():
    args = UpdateRuntimeArgs(
        temperature="0.5",
        reasoning_effort="MAX",
        thinking_mode="ENABLED",
    )

    assert args.temperature == 0.5
    assert args.reasoning_effort == "max"
    assert args.thinking_mode == "enabled"
    assert args.has("temperature")
    assert not args.has("top_p")


def test_update_runtime_args_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        UpdateRuntimeArgs(unknown=True)


def test_ipu_entry_defaults_capabilities_to_empty_list():
    assert IPUEntry(id="model").caps == []
