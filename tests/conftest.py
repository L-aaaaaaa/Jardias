"""Shared pytest fixtures for isolated, deterministic unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run filesystem-backed tests without touching repository data."""
    import character as character_module

    character_root = tmp_path / "character_data"
    character_root.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(character_module, "CHAR_ROOT", str(character_root))
    return tmp_path


@pytest.fixture
def character_root(isolated_workspace: Path) -> Path:
    return isolated_workspace / "character_data"


@pytest.fixture
def temp_character(character_root: Path):
    """Create a temporary character directory tree (alphanumeric name)."""
    from character.registry import ensure_dirs

    ensure_dirs("alice")
    return character_root / "alice"


@pytest.fixture
def actor_config():
    from data_shape import ActorConfig

    return ActorConfig()


@pytest.fixture
def reset_global_state(monkeypatch: pytest.MonkeyPatch):
    """Reset module-level state that can leak between otherwise independent tests."""
    from common import cli_output, i18n
    from tool import builtin
    from yinao.weaver import circuit_breaker, icp_tracker
    from yinao.weaver.round_state import set_round_meta

    cli_output.set_display_name("")
    cli_output.set_silent(False)
    cli_output.set_stream_color(None)
    i18n.set_lang("zh")

    builtin.set_actor("default")
    builtin._pending_switch = None
    circuit_breaker._circuit_breakers.clear()
    icp_tracker.cumulative_usage.update({
        "prompt_icp": 0,
        "completion_icp": 0,
        "total_icp": 0,
        "thinking_icp": 0,
    })
    icp_tracker.provider_latency.clear()
    set_round_meta(0.0)
    yield
    cli_output.set_display_name("")
    cli_output.set_silent(False)
    cli_output.set_stream_color(None)
    i18n.set_lang("zh")


@pytest.fixture
def history(tmp_path: Path):
    from character.history import History

    return History(str(tmp_path / "history.json"))


@pytest.fixture
def fake_chunk_factory():
    """Build provider-like stream chunks without importing an SDK type."""
    from types import SimpleNamespace

    def make(*, content=None, finish_reason=None, tool_calls=None, usage=None,
             reasoning_content=None, reasoning_details=None, choices=True):
        delta = SimpleNamespace(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            reasoning_details=reasoning_details,
        )
        choice_list = ([SimpleNamespace(delta=delta, finish_reason=finish_reason)]
                       if choices else [])
        return SimpleNamespace(choices=choice_list, usage=usage)

    return make
