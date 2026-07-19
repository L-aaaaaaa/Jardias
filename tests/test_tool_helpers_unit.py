"""Unit tests for tool.builtin helpers and the ToolRegistry dispatch layer."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from data_shape import ToolDef
from tool.builtin import (
    ToolRegistry,
    _format_error,
    _format_circuit_error,
    _format_validation_error,
    _find_missing_param,
    _apply_field,
    set_actor,
    current_actor,
)


# ────────────────────────────────────────────────────────────────────
# _format_error
# ────────────────────────────────────────────────────────────────────


def test_format_error_includes_type_and_message():
    s = _format_error(ValueError("bad input"))
    assert s == "[Error] ValueError: bad input"


# ────────────────────────────────────────────────────────────────────
# _find_missing_param
# ────────────────────────────────────────────────────────────────────


def test_find_missing_param_extracts_positional_argument():
    err = TypeError("missing 1 required positional argument: 'path'")
    assert _find_missing_param(err, {}) == "path"


def test_find_missing_param_returns_none_when_not_missing():
    err = TypeError("got an unexpected keyword argument")
    assert _find_missing_param(err, {}) is None


# ────────────────────────────────────────────────────────────────────
# _format_circuit_error
# ────────────────────────────────────────────────────────────────────


def test_format_circuit_error_includes_provider_and_reset_window(monkeypatch):
    fake_status = {
        "dashscope": {
            "available": False, "reset_remaining_sec": 12, "last_error": "rate limit",
        },
        "deepseek": {"available": True},
    }
    monkeypatch.setattr(
        "yinao.weaver.get_circuit_status", lambda: fake_status)
    out = _format_circuit_error("dashscope")
    assert "dashscope" in out
    assert "12s" in out
    assert "rate limit" in out
    assert "[Error]" in out


def test_format_circuit_error_handles_no_status():
    out = _format_circuit_error("missing")
    assert "[Error]" in out
    assert "missing" in out


# ────────────────────────────────────────────────────────────────────
# _format_validation_error — pydantic ValidationError → 友好字符串
# ────────────────────────────────────────────────────────────────────


def test_format_validation_error_handles_pydantic_style_error(monkeypatch):
    class FakeVE(Exception):
        def errors(self):
            return [
                {"loc": ("UpdateRuntimeArgs", "temperature"), "msg": "field required",
                 "input": None},
                {"loc": ("UpdateRuntimeArgs", "max_icp"),
                 "msg": "Value error, must be positive", "input": -1},
            ]

    out = _format_validation_error(FakeVE("boom"), "UpdateRuntimeArgs")
    assert "temperature" in out
    assert "field required" in out
    assert "max_icp" in out
    assert "got -1" in out


def test_format_validation_error_falls_back_for_non_pydantic_exception():
    out = _format_validation_error(RuntimeError("x"), "tool")
    assert "[Error]" in out


# ────────────────────────────────────────────────────────────────────
# _apply_field — 字段更新 6 分支
# ────────────────────────────────────────────────────────────────────


def test_apply_field_returns_false_when_value_missing_and_not_in_args():
    class Args:
        def has(self, name):
            return False

    args = Args()
    rt = SimpleNamespace(temperature=0.5)
    changes = []
    assert _apply_field(args, rt, "temperature", changes) is False
    assert changes == []


def test_apply_field_writes_when_value_differs(monkeypatch):
    class Args:
        def has(self, name):
            return True

        def __getattr__(self, name):
            return 0.9

    args = Args()
    rt = SimpleNamespace(temperature=0.5)
    changes = []
    assert _apply_field(args, rt, "temperature", changes) is True
    assert rt.temperature == 0.9
    assert changes == ["temperature=0.9"]


def test_apply_field_skips_when_value_already_matches(monkeypatch):
    class Args:
        def has(self, name):
            return True

        def __getattr__(self, name):
            return 0.5

    args = Args()
    rt = SimpleNamespace(temperature=0.5)
    changes = []
    assert _apply_field(args, rt, "temperature", changes) is False
    assert changes == []


def test_apply_field_uses_log_value_override():
    args = object()  # not used because value is explicit
    rt = SimpleNamespace(temperature=0.5)
    changes = []
    assert _apply_field(args, rt, "temperature", changes, value=0.9,
                        log_value="temperature→0.9") is True
    assert changes == ["temperature→0.9"]


# ────────────────────────────────────────────────────────────────────
# set_actor / current_actor
# ────────────────────────────────────────────────────────────────────


def test_set_actor_updates_current_actor():
    set_actor("default")
    assert current_actor() == "default"
    set_actor("alice")
    assert current_actor() == "alice"


# ────────────────────────────────────────────────────────────────────
# ToolRegistry.execute — sync + async 双路径
# ────────────────────────────────────────────────────────────────────


def test_tool_registry_executes_async_tool_with_kwargs():
    called = []

    async def handler(a, b=2):
        called.append((a, b))
        return f"{a}+{b}"

    td = ToolDef(name="add", description="x", parameters={}, fn=handler)
    reg = ToolRegistry()
    reg.register(td)
    out = asyncio.run(reg.execute("add", {"a": 1, "b": 3}))
    assert out == "1+3"
    assert called == [(1, 3)]


def test_tool_registry_executes_builtin_handler_via_kwargs(monkeypatch):
    """_BUILTIN_HANDLERS 中的 sync handler 应通过 **arguments 路径调用。"""
    from tool.builtin import _BUILTIN_HANDLERS, ToolRegistry

    called = []

    def sync_handler(path: str = ""):
        called.append(path)
        return "ok"

    monkeypatch.setitem(_BUILTIN_HANDLERS, "_test_sync_handler", sync_handler)
    reg = ToolRegistry()
    out = asyncio.run(reg.execute("_test_sync_handler", {"path": "/x"}))
    assert out == "ok"
    assert called == ["/x"]


def test_tool_registry_executes_builtin_handler_with_dict_fallback(monkeypatch):
    """sync handler 当 kwargs 解包失败时回退到 dict 直传。"""
    from tool.builtin import _BUILTIN_HANDLERS, ToolRegistry

    received = {}

    def dict_handler(arguments):
        received["args"] = arguments
        return "got-dict"

    monkeypatch.setitem(_BUILTIN_HANDLERS, "_test_dict_handler", dict_handler)
    reg = ToolRegistry()
    out = asyncio.run(reg.execute("_test_dict_handler", {"path": "/x"}))
    assert out == "got-dict"
    assert received["args"] == {"path": "/x"}


def test_tool_registry_executes_async_tool():
    async def handler(path: str):
        return f"async:{path}"

    td = ToolDef(name="async_op", description="x", parameters={}, fn=handler)
    reg = ToolRegistry()
    reg.register(td)
    out = asyncio.run(reg.execute("async_op", {"path": "/tmp/x"}))
    assert out == "async:/tmp/x"


def test_tool_registry_returns_missing_param_error():
    def handler(path: str, mode: str):
        return f"{mode}:{path}"

    td = ToolDef(name="op", description="x", parameters={}, fn=handler)
    reg = ToolRegistry()
    reg.register(td)
    out = asyncio.run(reg.execute("op", {"path": "/x"}))  # missing mode
    assert "[Error] missing required param: mode" in out


def test_tool_registry_returns_error_for_unknown_tool():
    reg = ToolRegistry()
    out = asyncio.run(reg.execute("not_a_tool", {}))
    assert "tool not found" in out


def test_tool_registry_wraps_generic_exception():
    def handler(**kw):
        raise RuntimeError("kaboom")

    td = ToolDef(name="boom", description="x", parameters={}, fn=handler)
    reg = ToolRegistry()
    reg.register(td)
    out = asyncio.run(reg.execute("boom", {}))
    assert "RuntimeError" in out
    assert "kaboom" in out
