"""Unit tests for stream normalization and tool-call assembly."""
from __future__ import annotations

from types import SimpleNamespace

from data_shape import ToolCall
from yinao.weaver.chunk_normalizer import (
    THINK_CLOSE,
    THINK_OPEN,
    _diff_cumulative,
    _strip_tail_ambiguous,
    collect_stream,
)


def _tool_delta(index=0, call_id=None, name=None, arguments=None):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, function=function)


def test_diff_cumulative_handles_extension_rewind_and_reset():
    assert _diff_cumulative("abc", "abcdef") == "def"
    assert _diff_cumulative("abcdef", "abc") == ""
    assert _diff_cumulative("abc", "xyz") == "xyz"


def test_strip_tail_ambiguous_keeps_marker_prefix():
    assert _strip_tail_ambiguous("safe" + THINK_OPEN[:4], THINK_OPEN) == THINK_OPEN[:4]
    assert _strip_tail_ambiguous("safe", THINK_OPEN) == ""


def test_collect_stream_parses_split_think_markers(fake_chunk_factory):
    chunks = [
        fake_chunk_factory(content=THINK_OPEN[:3]),
        fake_chunk_factory(content=THINK_OPEN[3:] + "reason" + THINK_CLOSE[:2]),
        fake_chunk_factory(content=THINK_CLOSE[2:] + "answer", finish_reason="stop"),
    ]
    reasoning_events = []
    content_events = []

    output = collect_stream(
        chunks,
        on_reasoning=reasoning_events.append,
        on_content=content_events.append,
    )

    assert output.reasoning == "reason"
    assert output.content == "answer"
    assert "".join(content_events) == "answer"


def test_collect_stream_uses_incremental_reasoning_protocol(fake_chunk_factory):
    output = collect_stream([
        fake_chunk_factory(reasoning_content="I"),
        fake_chunk_factory(reasoning_content="I think"),
        fake_chunk_factory(content="done", finish_reason="stop"),
    ], reasoning_field="reasoning_content")

    assert output.reasoning == "I think"
    assert output.content == "done"


def test_collect_stream_uses_cumulative_reasoning_details(fake_chunk_factory):
    output = collect_stream([
        fake_chunk_factory(reasoning_details=[{"text": "a"}]),
        fake_chunk_factory(reasoning_details=[{"text": "ab"}], content="answer",
                           finish_reason="stop"),
    ])

    assert output.reasoning == "ab"
    assert output.content == "answer"


def test_collect_stream_accumulates_split_tool_calls(fake_chunk_factory):
    first = _tool_delta(call_id="call-1", name="demo", arguments='{"x":')
    second = _tool_delta(arguments="1}")

    output = collect_stream([
        fake_chunk_factory(tool_calls=[first], finish_reason="tool_calls"),
        fake_chunk_factory(tool_calls=[second]),
    ])

    assert output.tool_calls == [ToolCall(id="call-1", name="demo", arguments='{"x":1}')]


def test_collect_stream_keeps_usage_chunk_without_choices(fake_chunk_factory):
    usage = SimpleNamespace(model_dump=lambda: {"total_tokens": 3})

    output = collect_stream([
        fake_chunk_factory(usage=usage, choices=False),
    ])

    assert output.usage == {"total_tokens": 3}


def test_collect_stream_treats_unclosed_think_as_reasoning(fake_chunk_factory):
    output = collect_stream([fake_chunk_factory(content=THINK_OPEN + "unfinished")])

    assert output.reasoning == "unfinished"
    assert output.content == ""
