"""Unit tests for character paths, history, and config persistence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from character import (
    ensure_dirs,
    get_character_dir,
    get_display_name,
    get_history_path,
    list_characters,
)
from character.config_io import config_from_dict, init_config, load_config, save_config
from data_shape import ActorConfig, IPURuntime, RoleConfig


def test_display_name_supports_timestamped_and_legacy_directories():
    assert get_display_name("202607191104-alice") == "alice"
    assert get_display_name("default") == "default"


def test_ensure_dirs_creates_timestamped_character_directory(isolated_workspace):
    path = ensure_dirs("alice")

    assert path.is_dir()
    assert (path / "summaries" / "L1").is_dir()
    assert list_characters() == ["alice"]
    assert get_character_dir("alice") == path


def test_history_persists_all_supported_message_kinds(history):
    history.append_pair("hello", "world", ts="2026-01-01 00:00:00")
    history.append_user("next", ts="2026-01-01 00:00:01")
    history.append_assistant_msg("tool request", ts="2026-01-01 00:00:02",
                                  tool_calls=[{"id": "c1"}])
    history.append_tool("c1", "demo", "ok", ts="2026-01-01 00:00:03")
    history.append_trigger("trigger")
    history.append_system("[智能基元切换] old -> new", ts="2026-01-01 00:00:05")
    history.save()

    loaded = type(history)(history.path).load()
    assert [message["role"] for message in loaded.messages[:4]] == [
        "user", "assistant", "user", "assistant",
    ]
    assert loaded.messages[3]["tool_calls"] == [{"id": "c1"}]
    assert loaded.messages[4]["role"] == "tool"
    assert loaded.messages[-1]["role"] == "system"


def test_history_load_slice_clamps_bounds_and_rejects_reversed_range(history):
    history.append_pair("a", "b", ts="t")
    history.save()

    assert len(history.load_slice(-10, 100)) == 2
    assert history.load_slice(2, 1) == []


def test_history_invalid_json_falls_back_to_empty(tmp_path: Path):
    path = tmp_path / "broken.json"
    path.write_text("not json", encoding="utf-8")

    from character.history import History

    assert History(str(path)).load().messages == []


def test_config_round_trip_and_legacy_field_migration(isolated_workspace):
    config = ActorConfig(
        identity=RoleConfig(title="title", traits="traits"),
        runtime=IPURuntime(provider="deepseek", ipu="v3", max_icp=123),
    )
    save_config(config, "alice")
    loaded = load_config("alice")

    assert loaded.identity.title == "title"
    assert loaded.runtime.max_icp == 123

    migrated = config_from_dict({
        "identity": {"role": "old title", "description": "old traits"},
        "runtime": {"model": "old-model", "max_tokens": 77},
    })
    assert migrated.identity.title == "old title"
    assert migrated.identity.traits == "old traits"
    assert migrated.runtime.ipu == "old-model"
    assert migrated.runtime.max_icp == 77


def test_init_config_is_idempotent(isolated_workspace):
    first = init_config("alice", identity={"title": "first"})
    second = init_config("alice", identity={"title": "second"})

    assert first.identity.title == "first"
    assert second.identity.title == "first"
    assert json.loads((get_character_dir("alice") / "config.json").read_text(encoding="utf-8"))[
        "identity"]["title"] == "first"


def test_config_load_missing_or_invalid_returns_defaults(isolated_workspace):
    assert load_config("missing").runtime.provider == "minimax"
    directory = ensure_dirs("broken")
    (directory / "config.json").write_text("{", encoding="utf-8")

    assert load_config("broken").identity.system_prompt == "智能体项目测试助手。"


def test_registry_create_builds_character_skeleton(isolated_workspace, monkeypatch):
    from character.registry import CharacterRegistry

    monkeypatch.setattr("experience.adapter.init.on_register", lambda name, config: None)
    registry = CharacterRegistry()
    registry.create("alice", ActorConfig())
    directory = get_character_dir("alice")

    assert json.loads((directory / "history.json").read_text(encoding="utf-8")) == []
    assert "首次对话" in (directory / "experience.md").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="已存在"):
        registry.create("alice", ActorConfig())


def test_registry_cannot_delete_default(isolated_workspace):
    from character.registry import CharacterRegistry

    with pytest.raises(ValueError, match="default"):
        CharacterRegistry().delete("default")
