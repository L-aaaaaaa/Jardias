"""Unit tests for tool metadata, dispatch, files, and actor-tool injection."""
from __future__ import annotations

import asyncio
import json

import pytest

from data_shape import ToolCall, ToolDef
from tool.actor_tool import actor_tool, list_actor_tools, set_actor_executor
from tool.builtin import ToolRegistry
from tool.metadata import build_tool_defs
from tool.builtin_tools.files import (
    _is_text_file,
    read_file,
    search_in_content,
    write_file,
)


def test_tool_metadata_contains_unique_described_definitions():
    definitions = build_tool_defs()
    names = [definition.name for definition in definitions]

    assert len(names) == len(set(names))
    assert "read_file" in names
    read_definition = next(item for item in definitions if item.name == "read_file")
    assert read_definition.description
    assert read_definition.parameters["properties"]["path"]["description"]


def test_tool_registry_dispatches_async_function_and_normalizes_result():
    registry = ToolRegistry()
    calls = []

    async def handler(value):
        calls.append(value)
        return value + 1

    registry.register(ToolDef(
        name="demo",
        description="demo",
        parameters={"type": "object", "properties": {}},
        fn=handler,
    ))

    assert asyncio.run(registry.execute("demo", {"value": 2})) == "3"
    assert calls == [2]


def test_tool_registry_reports_missing_and_unknown_tools():
    registry = ToolRegistry()
    registry.register(ToolDef(
        name="demo",
        description="demo",
        parameters={"type": "object", "properties": {"value": {}}},
        fn=None,
    ))

    assert "tool not found" in asyncio.run(registry.execute("missing", {}))
    assert "has no implementation" in asyncio.run(registry.execute("demo", {}))


def test_file_tools_round_trip_and_line_range(isolated_workspace):
    assert "wrote" in asyncio.run(write_file("nested/file.txt", "a\nb\nc"))
    assert asyncio.run(read_file("nested/file.txt", "2,2")) == "b"
    assert "file not found" in asyncio.run(read_file("missing.txt"))


def test_file_tools_reject_binary_and_find_regex_matches(isolated_workspace):
    binary = isolated_workspace / "binary.bin"
    binary.write_bytes(b"a\x00b")
    assert not _is_text_file(binary)
    assert "binary" in asyncio.run(read_file("binary.bin"))

    (isolated_workspace / "one.txt").write_text("Alpha\nbeta\n", encoding="utf-8")
    result = asyncio.run(search_in_content("alpha", ".", case_insensitive=True))
    assert "one.txt:1" in result
    assert "invalid regex" in asyncio.run(search_in_content("[", "."))


def test_actor_tool_builds_executor_request_without_running_original_function():
    requests = []

    async def executor(**kwargs):
        requests.append(kwargs)
        return {"ok": True}

    set_actor_executor(executor)

    @actor_tool(ipu="test-ipu", output_schema={"ok": "bool"}, system="system")
    async def demo(value: str):
        raise AssertionError("original actor tool function must not run")

    result = asyncio.run(demo(value="hello"))

    assert result == {"ok": True}
    assert requests == [{
        "ipu": "test-ipu",
        "system_prompt": "system",
        "user_message": "value:\nhello",
        "output_schema": {"ok": "bool"},
    }]
    assert "demo" in list_actor_tools()


def test_actor_tool_requires_executor(monkeypatch):
    import tool.actor_tool as actor_module

    monkeypatch.setattr(actor_module, "_executor", None)

    @actor_module.actor_tool(ipu="ipu", output_schema={}, system="system")
    async def demo():
        pass

    with pytest.raises(RuntimeError, match="executor"):
        asyncio.run(demo())
