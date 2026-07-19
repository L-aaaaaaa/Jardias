"""Unit tests for yinao/weaver/tool_runner."""
from __future__ import annotations

import json

import pytest

from data_shape import ToolCall
from yinao.weaver import tool_runner
from yinao.weaver.tool_runner import (
    ToolRunner,
    _format_args,
    _try_parse_args,
    display_tool_calls,
    display_tool_result,
    log_tool_calls,
    log_tool_result,
)


# ────────────────────────────────────────────────────────────────────
# _format_args
# ────────────────────────────────────────────────────────────────────


def test_format_args_with_dict():
    assert _format_args({"a": 1, "b": "x"}) == "a=1, b=x"


def test_format_args_with_json_string():
    assert _format_args('{"a": 1}') == "a=1"


def test_format_args_with_invalid_json_returns_truncated_string():
    out = _format_args("not{json")
    # args[:80] 返回原始（因为长度 < 80）
    assert out == "not{json"


def test_format_args_with_non_dict_value():
    out = _format_args(123)
    assert out == "123"


# ────────────────────────────────────────────────────────────────────
# _try_parse_args
# ────────────────────────────────────────────────────────────────────


def test_try_parse_args_returns_dict_unchanged():
    d = {"a": 1}
    assert _try_parse_args(d) is d


def test_try_parse_args_parses_valid_json_string():
    assert _try_parse_args('{"x": 2}') == {"x": 2}


def test_try_parse_args_returns_empty_for_invalid_json():
    assert _try_parse_args("not-json") == {}


def test_try_parse_args_returns_empty_for_non_dict_types():
    assert _try_parse_args(123) == {}


# ────────────────────────────────────────────────────────────────────
# log_tool_calls — 静默路径
# ────────────────────────────────────────────────────────────────────


def test_log_tool_calls_is_noop_when_empty():
    # 不应抛
    log_tool_calls([])


# ────────────────────────────────────────────────────────────────────
# ToolRunner.run — 纯执行路径（mock executor）
# ────────────────────────────────────────────────────────────────────


class _FakeExecutor:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    async def execute(self, name, arguments):
        self.calls.append((name, arguments))
        if self.responses:
            return self.responses.pop(0)
        return f"default-result-for-{name}"


@pytest.mark.asyncio
async def test_run_executes_tool_calls_and_writes_tool_messages():
    executor = _FakeExecutor(responses=["reply-A", "reply-B"])
    runner = ToolRunner(executor=executor)
    tc1 = ToolCall(id="t1", name="echo", arguments='{"x": 1}')
    tc2 = ToolCall(id="t2", name="echo", arguments='{"x": 2}')
    messages = []

    out = await runner.run([tc1, tc2], messages, round_idx=0)

    assert len(executor.calls) == 2
    assert executor.calls[0] == ("echo", {"x": 1})
    assert executor.calls[1] == ("echo", {"x": 2})
    # 两条 tool message 都被追加
    tool_msgs = [m for m in out if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0]["tool_call_id"] == "t1"
    assert tool_msgs[0]["name"] == "echo"
    assert tool_msgs[0]["content"] == "reply-A"


@pytest.mark.asyncio
async def test_run_calls_on_history_save_after_each_tool():
    saves = []
    executor = _FakeExecutor(responses=["ok"])
    runner = ToolRunner(executor=executor)
    tc1 = ToolCall(id="t1", name="echo", arguments="{}")

    await runner.run([tc1], [], round_idx=2, on_history_save=lambda: saves.append(1))
    assert saves == [1]


@pytest.mark.asyncio
async def test_run_wraps_executor_exception_in_error_string():
    class _BoomExecutor:
        async def execute(self, name, arguments):
            raise RuntimeError("kaboom")
    runner = ToolRunner(_BoomExecutor())
    tc = ToolCall(id="t1", name="any", arguments="{}")

    out = await runner.run([tc], [], round_idx=0)

    assert out[-1]["role"] == "tool"
    assert "[Error] RuntimeError: kaboom" in out[-1]["content"]


@pytest.mark.asyncio
async def test_run_passes_arguments_dict_to_executor():
    executor = _FakeExecutor(responses=["ok"])
    runner = ToolRunner(executor=executor)
    tc = ToolCall(id="t1", name="x", arguments='{"k": "v"}')

    await runner.run([tc], [], round_idx=0)
    assert executor.calls == [("x", {"k": "v"})]


# ────────────────────────────────────────────────────────────────────
# post_exec hook — 注入额外 messages
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_invokes_registered_post_exec_hook():
    executor = _FakeExecutor(responses=["ok"])
    runner = ToolRunner(executor=executor)

    extras = [{"role": "user", "content": "[hook] injected"}]
    runner.register_post_exec("echo", lambda name, result, args, ri, idx: list(extras))

    out = await runner.run(
        [ToolCall(id="t1", name="echo", arguments='{}')], [], round_idx=0)

    # 顺序：tool message → hook extras
    assert out[-1]["content"] == "[hook] injected"
    assert out[-2]["role"] == "tool"


@pytest.mark.asyncio
async def test_run_skips_hook_for_tools_without_registration():
    executor = _FakeExecutor(responses=["ok"])
    runner = ToolRunner(executor=executor)
    runner.register_post_exec("registered", lambda *a: [{"role": "user", "content": "x"}])

    out = await runner.run(
        [ToolCall(id="t1", name="not_registered", arguments="{}")], [], round_idx=0)
    # 只应有 1 条 tool message
    assert len([m for m in out if m["role"] == "tool"]) == 1


# ────────────────────────────────────────────────────────────────────
# log_tool_result / display_tool_result — 代码风格格式化（不抛）
# ────────────────────────────────────────────────────────────────────


def test_log_tool_result_with_long_send_to_character_truncates_brief():
    # send_to_character 走特殊路径（前两行 + 截断）
    log_tool_result("send_to_character", "line1\nline2\nline3\n" + "x" * 400)


def test_log_tool_result_with_other_truncates_first_line():
    log_tool_result("echo", "first line\nsecond line\n" + "y" * 200)


def test_display_tool_result_silent_when_silent_set(monkeypatch):
    from common import cli_output
    cli_output.set_silent(True)
    try:
        display_tool_result("echo", "anything")
        display_tool_result("send_to_character", "anything")
        display_tool_result("shice_schedule_add", "anything")
    finally:
        cli_output.set_silent(False)
