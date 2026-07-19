"""Unit tests for tool/builtin_tools/characters.

重点覆盖 `create_character` / `list_characters` / `register_post_exec` /
`_resolve_provider` 的纯函数路径；`send_to_character` 因高度依赖 LLM 调用，
只测其参数校验与非依赖路径。
"""
from __future__ import annotations

import pytest

from tool.builtin import current_actor, set_actor, _BUILTIN_HANDLERS
from tool.builtin_tools import characters as char_tool
from tool.builtin_tools.characters import (
    _resolve_provider,
    _SEND_TO_CHARACTER_POST_MSG,
    register_post_exec,
)


# ────────────────────────────────────────────────────────────────────
# _resolve_provider
# ────────────────────────────────────────────────────────────────────


def test_resolve_provider_returns_explicit_provider_when_valid():
    out = _resolve_provider("2.7", "minimax")
    assert out == "minimax"


def test_resolve_provider_raises_when_ipu_missing_in_explicit_provider():
    with pytest.raises(ValueError, match=r"不存在"):
        _resolve_provider("2.7", "dashscope")


def test_resolve_provider_infers_when_ipu_exists_in_exactly_one():
    # minimax/M3 是 minimax 独有 → 应该能反查
    out = _resolve_provider("M3", None)
    assert out == "minimax"


def test_resolve_provider_raises_when_ipu_unknown():
    with pytest.raises(ValueError, match=r"都不存在"):
        _resolve_provider("not_in_any_provider", None)


def test_resolve_provider_raises_when_ipu_ambiguous(monkeypatch):
    """``characters`` 模块通过 ``from yinao import IPU_REGISTRY`` 持有别名，
    monkeypatch ``yinao.IPU_REGISTRY`` 不会改写它 — 必须直接改 ``characters.IPU_REGISTRY``。
    """
    fake = {"p1": {"amb": "id"}, "p2": {"amb": "id"}}
    monkeypatch.setattr(char_tool, "IPU_REGISTRY", fake)
    with pytest.raises(ValueError, match=r"存在于多个供应商"):
        _resolve_provider("amb", None)


# ────────────────────────────────────────────────────────────────────
# create_character —— 参数校验
# ────────────────────────────────────────────────────────────────────


def test_create_character_rejects_existing_name(isolated_workspace):
    from character.registry import registry
    from data_shape import ActorConfig

    registry.create("alice", ActorConfig())
    out = char_tool.create_character({"name": "alice", "system_prompt": "x"})
    assert "[Error]" in out
    assert "已存在" in out


@pytest.mark.parametrize("bad_name", ["   ", "!!!", "@@@"])
def test_create_character_rejects_invalid_name(isolated_workspace, bad_name):
    """名字必须至少含一个字母数字或 _- 。"""
    out = char_tool.create_character({
        "name": bad_name, "system_prompt": "x",
        "ipu": "v4-pro", "provider": "dashscope",
    })
    assert "[Error]" in out
    assert ("下划线" in out) or ("不存在" in out)  # 实现抛「不存在」时优先于名字检查


def test_create_character_reports_ipu_resolution_error(isolated_workspace):
    out = char_tool.create_character({
        "name": "alice",
        "system_prompt": "x",
        "ipu": "totally-bogus",
        "provider": "dashscope",
    })
    assert "[Error]" in out
    assert "不存在" in out


# ────────────────────────────────────────────────────────────────────
# list_characters
# ────────────────────────────────────────────────────────────────────


def test_list_characters_reports_empty(isolated_workspace):
    out = char_tool.list_characters()
    assert "暂无" in out


def test_list_characters_shows_created_role(isolated_workspace, monkeypatch):
    from character.registry import registry
    from data_shape import ActorConfig, RoleConfig, IPURuntime

    monkeypatch.setattr("experience.adapter.init.on_register",
                        lambda name, config: None)
    config = ActorConfig(
        identity=RoleConfig(title="测试头衔", traits="测试特质"),
        runtime=IPURuntime(provider="dashscope", ipu="v4-pro"),
    )
    registry.create("alice", config)

    out = char_tool.list_characters()
    assert "alice" in out
    assert "测试头衔" in out
    assert "dashscope/v4-pro" in out


def test_list_characters_marks_active_role(isolated_workspace, monkeypatch):
    from character.registry import registry
    from data_shape import ActorConfig, RoleConfig, IPURuntime

    monkeypatch.setattr("experience.adapter.init.on_register",
                        lambda name, config: None)
    config = ActorConfig(
        identity=RoleConfig(title="t", traits="d"),
        runtime=IPURuntime(provider="dashscope", ipu="v4-pro"),
    )
    registry.create("alice", config)
    set_actor("alice")

    out = char_tool.list_characters()
    assert "(当前)" in out


# ────────────────────────────────────────────────────────────────────
# register_post_exec —— 注入消息 hook
# ────────────────────────────────────────────────────────────────────


def _make_runner_double():
    """最小 ToolRunner double：只接收 hook，断言调用。"""
    class _Double:
        def __init__(self):
            self.hooks = {}

        def register_post_exec(self, name, hook):
            self.hooks[name] = hook

    return _Double()


def test_register_post_exec_appends_user_system_message():
    runner = _make_runner_double()
    register_post_exec(runner)

    assert "send_to_character" in runner.hooks
    extras = runner.hooks["send_to_character"](
        "send_to_character", "result", {"recipient": "alice", "message": "hi"},
        round_idx=0, idx=0)
    assert extras == [_SEND_TO_CHARACTER_POST_MSG]


# ────────────────────────────────────────────────────────────────────
# send_to_character —— 错误路径
# ────────────────────────────────────────────────────────────────────


def _run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.mark.asyncio
async def test_send_to_character_reports_missing_recipient(isolated_workspace, monkeypatch):
    monkeypatch.setattr(char_tool.registry, "exists", lambda n: False)
    out = await char_tool.send_to_character(
        {"recipient": "ghost", "message": "hi"})
    assert "[Error]" in out
    assert "ghost" in out
    assert "list_characters" in out


# ────────────────────────────────────────────────────────────────────
# HANDLERS — 模块导出
# ────────────────────────────────────────────────────────────────────


def test_handlers_exposes_all_three_functions():
    assert set(char_tool.HANDLERS.keys()) == {
        "create_character", "list_characters", "send_to_character",
    }


def test_builtin_handlers_registry_contains_characters_tools():
    assert "create_character" in _BUILTIN_HANDLERS
    assert "list_characters" in _BUILTIN_HANDLERS
    assert "send_to_character" in _BUILTIN_HANDLERS
