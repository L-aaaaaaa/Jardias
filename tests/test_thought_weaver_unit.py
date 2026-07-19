"""Unit tests for yinao/weaver/thought_weaver.

覆盖 _run_single_round 和 weave_thought 的核心分支（多轮工具调用、回合上限、
should_switch 退出、executor 异常处理）。LLM 流通过 monkeypatch yinao.launcher
的流生成接口来 stub；tool 执行通过注入自定义 ToolRunner 来 stub。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from data_shape import ToolCall
from yinao.weaver import thought_weaver
from yinao.weaver.tool_runner import ToolRunner


# ────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────


def _fake_chunk(content=None, finish_reason=None, tool_calls=None, usage=None,
                reasoning_content=None):
    delta = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
        reasoning_details=None,
    )
    if usage is not None:
        # collect_stream 通过 hasattr(usage, 'model_dump') 判定 pydantic 模式。
        # 注意：捕获原始 dict，别让 lambda 闭包到被覆盖的 ``usage`` 变量。
        d = usage
        usage = SimpleNamespace(model_dump=lambda d=d: d)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        usage=usage,
    )


def _stream(chunks):
    """构造同步可迭代流（OpenAI 流式响应是同步生成器）。"""
    return iter(list(chunks))


class _FakeChat:
    """模拟 resolve_chat 返回的同步句柄（直接返回流对象）。"""

    def __init__(self, streams):
        self.streams = list(streams)
        self.calls = 0

    def __call__(self, messages, ipu_config, character_name="", on_history_save=None):
        self.calls += 1
        return self.streams[self.calls - 1]


class _FakeExecutor:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    async def execute(self, name, arguments):
        self.calls.append((name, arguments))
        if self.results:
            return self.results.pop(0)
        return f"default-for-{name}"


def _runner_with_executor(executor):
    return ToolRunner(executor=executor)


# ────────────────────────────────────────────────────────────────────
# _run_single_round —— 基础单轮
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_single_round_appends_assistant_message_and_returns_output(monkeypatch):
    chat = _FakeChat([_stream([
        _fake_chunk(content="hello", finish_reason="stop",
                    usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}),
    ])])
    monkeypatch.setattr(thought_weaver, "get_ipu_stream_reply", chat)

    messages = [{"role": "user", "content": "hi"}]
    output, returned = await thought_weaver._run_single_round(
        messages, iteration=0, ipu_config=SimpleNamespace(),
        character_name="", on_history_save=None, tool_runner=None,
    )

    assert output.content == "hello"
    assert output.finish_reason == "stop"
    assert output.usage["total_tokens"] == 8
    assert returned[-1]["role"] == "assistant"
    assert returned[-1]["content"] == "hello"
    assert chat.calls == 1


@pytest.mark.asyncio
async def test_run_single_round_persists_reasoning_separately_when_not_inline(monkeypatch):
    chat = _FakeChat([_stream([
        _fake_chunk(content="final", reasoning_content="private", finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
    ])])
    monkeypatch.setattr(thought_weaver, "get_ipu_stream_reply", chat)

    messages = []
    output, returned = await thought_weaver._run_single_round(
        messages, iteration=0, ipu_config=SimpleNamespace(),
        reasoning_field="reasoning_content",  # DeepSeek 风格
        reasoning_inline=False,
        character_name="", on_history_save=None, tool_runner=None,
    )

    assert output.reasoning == "private"
    # 因为 reasoning_inline=False，推理被 append 为单独 _reasoning 消息
    reasoning_msgs = [m for m in returned if m.get("_reasoning")]
    assert reasoning_msgs and reasoning_msgs[0]["content"] == "private"
    # 正文消息不含 _reasoning 标记
    assert not returned[-1].get("_reasoning")


@pytest.mark.asyncio
async def test_run_single_round_keeps_reasoning_inline_when_requested(monkeypatch):
    chat = _FakeChat([_stream([
        _fake_chunk(content="final", reasoning_content="private",
                    finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
    ])])
    monkeypatch.setattr(thought_weaver, "get_ipu_stream_reply", chat)

    messages = []
    output, returned = await thought_weaver._run_single_round(
        messages, iteration=0, ipu_config=SimpleNamespace(),
        reasoning_field="reasoning_content",  # DeepSeek 风格
        reasoning_inline=True,
        character_name="", on_history_save=None, tool_runner=None,
    )

    assert returned[-1]["reasoning_content"] == "private"
    assert not any(m.get("_reasoning") for m in returned)


@pytest.mark.asyncio
async def test_run_single_round_attaches_tool_calls(monkeypatch):
    tc_dict = SimpleNamespace(index=0, id="c1",
        function=SimpleNamespace(name="echo", arguments='{"x":1}'))
    chat = _FakeChat([_stream([
        _fake_chunk(content="", tool_calls=[tc_dict], finish_reason="tool_calls",
                    usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
    ])])
    monkeypatch.setattr(thought_weaver, "get_ipu_stream_reply", chat)

    messages = []
    output, returned = await thought_weaver._run_single_round(
        messages, iteration=0, ipu_config=SimpleNamespace(),
        character_name="", on_history_save=None, tool_runner=None,
    )

    assert len(output.tool_calls) == 1
    assert output.tool_calls[0].name == "echo"
    assert returned[-1]["role"] == "assistant"
    assert returned[-1]["tool_calls"][0]["function"]["name"] == "echo"


@pytest.mark.asyncio
async def test_run_single_round_calls_on_history_save(monkeypatch):
    chat = _FakeChat([_stream([
        _fake_chunk(content="ok", finish_reason="stop",
                    usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
    ])])
    monkeypatch.setattr(thought_weaver, "get_ipu_stream_reply", chat)

    saved = []
    await thought_weaver._run_single_round(
        [], 0, SimpleNamespace(), character_name="", on_history_save=lambda: saved.append(1),
        tool_runner=None,
    )
    assert saved == [1]


# ────────────────────────────────────────────────────────────────────
# weave_thought —— 多轮主循环
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_weave_thought_returns_after_single_no_tool_round(monkeypatch):
    chat = _FakeChat([_stream([
        _fake_chunk(content="hi", finish_reason="stop",
                    usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
    ])])
    monkeypatch.setattr(thought_weaver, "get_ipu_stream_reply", chat)

    messages = [{"role": "user", "content": "hi"}]
    result = await thought_weaver.weave_thought(
        messages, SimpleNamespace(), character_name="", tool_runner=None)

    assert result.should_switch is False
    assert result.messages[-1]["content"] == "hi"
    assert chat.calls == 1


@pytest.mark.asyncio
async def test_weave_thought_runs_tool_then_returns_final_answer(monkeypatch):
    # 第一轮：模型请求工具调用；第二轮：模型返回最终答案
    tc_dict = SimpleNamespace(index=0, id="c1",
        function=SimpleNamespace(name="demo", arguments='{"x":1}'))
    chat = _FakeChat([
        _stream([_fake_chunk(content="", tool_calls=[tc_dict], finish_reason="tool_calls",
                             usage={"prompt_tokens": 1, "completion_tokens": 1,
                                    "total_tokens": 2})]),
        _stream([_fake_chunk(content="final answer", finish_reason="stop",
                             usage={"prompt_tokens": 1, "completion_tokens": 1,
                                    "total_tokens": 2})]),
    ])
    monkeypatch.setattr(thought_weaver, "get_ipu_stream_reply", chat)

    executor = _FakeExecutor(results=["tool-result"])
    runner = ToolRunner(executor=executor)

    messages = [{"role": "user", "content": "question"}]
    result = await thought_weaver.weave_thought(
        messages, SimpleNamespace(), character_name="", tool_runner=runner)

    assert result.messages[-1]["content"] == "final answer"
    # 工具消息已被插入
    tool_msgs = [m for m in result.messages if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["content"] == "tool-result"
    assert executor.calls == [("demo", {"x": 1})]
    assert chat.calls == 2  # 工具后第二轮


@pytest.mark.asyncio
async def test_weave_thought_returns_should_switch_when_switch_requested(monkeypatch):
    tc_dict = SimpleNamespace(index=0, id="c1",
        function=SimpleNamespace(name="x", arguments="{}"))
    chat = _FakeChat([
        _stream([_fake_chunk(content="", tool_calls=[tc_dict], finish_reason="tool_calls",
                             usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})]),
    ])
    monkeypatch.setattr(thought_weaver, "get_ipu_stream_reply", chat)
    monkeypatch.setattr(thought_weaver, "pop_switch", lambda: SimpleNamespace(provider="dashscope", ipu="new"))

    executor = _FakeExecutor(results=["ok"])
    runner = ToolRunner(executor=executor)

    result = await thought_weaver.weave_thought(
        [{"role": "user", "content": "q"}], SimpleNamespace(),
        character_name="", tool_runner=runner)

    assert result.should_switch is True
    assert result.switch_provider == "dashscope"
    assert result.switch_ipu == "new"


@pytest.mark.asyncio
async def test_weave_thought_caps_at_max_turns(monkeypatch):
    """当模型一直要求工具调用且无切换时，应停在 WEAVE_MAX_TURNS。"""
    tc_dict = SimpleNamespace(index=0, id="c1",
        function=SimpleNamespace(name="loop", arguments="{}"))
    # 一直循环返回工具调用
    streams = [
        _stream([_fake_chunk(content="", tool_calls=[tc_dict], finish_reason="tool_calls",
                             usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})])
        for _ in range(thought_weaver.WEAVE_MAX_TURNS + 2)
    ]
    chat = _FakeChat(streams)
    monkeypatch.setattr(thought_weaver, "get_ipu_stream_reply", chat)

    executor = _FakeExecutor(results=["ok"])
    runner = ToolRunner(executor=executor)

    result = await thought_weaver.weave_thought(
        [{"role": "user", "content": "q"}], SimpleNamespace(),
        character_name="", tool_runner=runner)

    # 上限触发后仍返回 ChatResult，但 messages 已含多轮数据
    assert isinstance(result.messages, list)
    # 调用次数应等于 WEAVE_MAX_TURNS
    assert chat.calls == thought_weaver.WEAVE_MAX_TURNS
