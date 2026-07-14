"""流式解析器与展示器的单元测试。

覆盖：
- 解析（``collect_stream``）：
    - think 标记偏移 / 跨 chunk / 增量重复
    - 推理字段（DeepSeek 增量 / MiniMax 累计）求差量
    - 工具调用结束后的 usage chunk
    - 工具调用 ID 保留
    - Markdown 重复行 / 空行保留
- 展示（``present_round``）：
    - 无推理时正文展示
    - 工具轮不显示回复标题
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from data_shape import RoundOutput
from yinao.ipu_client.chunk_normalizer import (
    THINK_OPEN, THINK_CLOSE, collect_stream,
)
from common.cli_output import present_round
from yinao.ipu_client import tool_runner as tr


def _chunk(content=None, finish=None, tool_calls=None, usage=None,
        choices_present=True, reasoning_content=None, reasoning_details=None):
    delta = NS(content=content, tool_calls=tool_calls,
        reasoning_content=reasoning_content, reasoning_details=reasoning_details)
    choices = [NS(delta=delta, finish_reason=finish)] if choices_present else []
    return NS(choices=choices, usage=usage)


# ── think 标记 ──────────────────────────────────────────────

class TestThinkMarker:
    def test_full_marker_preserves_surrounding(self):
        out = collect_stream([_chunk(THINK_OPEN + 'ABC' + THINK_CLOSE + 'XYZ',
            'stop')])
        assert out.reasoning == 'ABC'
        assert out.content == 'XYZ'

    def test_open_marker_spans_chunks(self):
        out = collect_stream([
            _chunk(THINK_OPEN[:4]),
            _chunk(THINK_OPEN[4:] + 'ABC' + THINK_CLOSE + 'XYZ', 'stop'),
        ])
        assert out.reasoning == 'ABC'
        assert out.content == 'XYZ'

    def test_close_marker_spans_chunks(self):
        mid = 'abcdef' + THINK_CLOSE + 'tail'
        out = collect_stream([
            _chunk(THINK_OPEN + mid[:len(mid)//2]),
            _chunk(mid[len(mid)//2:], 'stop'),
        ])
        assert out.reasoning == 'abcdef'
        assert out.content == 'tail'

    def test_partial_prefix_not_leaked(self):
        body = THINK_OPEN[:3] + '正文前缀'
        out = collect_stream([_chunk(body)])
        assert out.content == '正文前缀'
        assert out.reasoning == ''

    def test_incremental_reasoning_not_duplicated(self):
        out = collect_stream([
            _chunk(THINK_OPEN + 'abcdefgh'),
            _chunk('ijk'),
            _chunk(THINK_CLOSE + 'Z', 'stop'),
        ])
        assert out.reasoning == 'abcdefghijk'
        assert out.content == 'Z'

    def test_unclosed_marker_treated_as_content(self):
        out = collect_stream([_chunk(THINK_OPEN + 'abc')])
        assert out.content == ''
        assert out.reasoning == 'abc'


# ── 推理字段求差量 ─────────────────────────────────────────

class TestReasoningField:
    def test_deepseek_cumulative(self):
        chunks = [
            _chunk(reasoning_content='我'),
            _chunk(reasoning_content='我是'),
            _chunk(reasoning_content='我是思'),
            _chunk(reasoning_content='我是思考', content='你好', finish='stop'),
        ]
        out = collect_stream(chunks, reasoning_field='reasoning_content')
        assert out.reasoning == '我是思考'
        assert out.content == '你好'

    def test_minimax_cumulative(self):
        chunks = [
            _chunk(reasoning_details=[{'text': 'minimax思考'}]),
            _chunk(reasoning_details=[{'text': 'minimax思考中'}],
                content='final', finish='stop'),
        ]
        out = collect_stream(chunks, reasoning_field='reasoning_details')
        assert out.reasoning == 'minimax思考中'
        assert out.content == 'final'

    def test_no_reasoning_field(self):
        out = collect_stream([_chunk('plain answer', 'stop')],
            reasoning_field='reasoning_details')
        assert out.reasoning == ''
        assert out.content == 'plain answer'


# ── 工具调用 ──────────────────────────────────────────────

class TestToolCalls:
    def _tc(self, name='demo', args='{}', tid='call_real'):
        return NS(index=0, id=tid, function=NS(name=name, arguments=args))

    def test_usage_chunk_after_tool_calls(self):
        usage = NS(model_dump=lambda: {'prompt_tokens': 10, 'total_tokens': 12,
            'completion_tokens': 2})
        out = collect_stream([
            _chunk(finish='tool_calls', tool_calls=[self._tc()]),
            _chunk(usage=usage, choices_present=False),
        ])
        assert out.usage == {'prompt_tokens': 10, 'completion_tokens': 2,
            'total_tokens': 12}

    def test_original_tool_call_id_preserved(self):
        out = collect_stream([
            _chunk(finish='tool_calls', tool_calls=[self._tc(tid='call_real')]),
        ])
        assert out.tool_calls and out.tool_calls[0].id == 'call_real'

    def test_arguments_split_across_chunks(self):
        a = NS(index=0, id='c1', function=NS(name='fn', arguments='{"x":'))
        b = NS(index=0, function=NS(arguments='1}'))
        c = NS(index=0, function=NS(arguments=''))
        out = collect_stream([
            _chunk(finish='tool_calls', tool_calls=[a]),
            _chunk(tool_calls=[b]),
            _chunk(tool_calls=[c], choices_present=True),
        ])
        assert out.tool_calls and out.tool_calls[0].arguments == '{"x":1}'


# ── Markdown / 其它 ─────────────────────────────────────────

class TestContent:
    def test_markdown_repeated_lines_preserved(self):
        body = '# 标题\n\n```\npass\npass\n```\n---\n---\n'
        out = collect_stream([_chunk(body, 'stop')])
        assert 'pass\npass' in out.content
        assert out.content.count('---') == 2
        assert '\n\n' in out.content


# ── 终端展示 ──────────────────────────────────────────────

class TestPresenter:
    def test_no_reasoning_no_reasoning_header(self):
        """无推理时不输出"推理过程"，但仍输出"回复"。"""
        events = []
        out = RoundOutput(reasoning='', content='HELLO', tool_calls=[])
        with patch('common.cli_output.separate_print',
                lambda **kw: events.append(('header', kw.get('title')))), \
             patch('common.cli_output.stream_print',
                lambda text: events.append(('text', text))):
            present_round(out, silent=False)
        assert ('header', '回复') in events
        assert ('text', 'HELLO') in events
        assert ('header', '推理过程') not in events

    def test_tool_round_skips_reply_header(self):
        """工具调用轮不输出"回复"标题，正文仍可见。"""
        events = []
        out = RoundOutput(reasoning='', content='TOOL PRELUDE', tool_calls=[])
        with patch('common.cli_output.separate_print',
                lambda **kw: events.append(('header', kw.get('title')))), \
             patch('common.cli_output.stream_print',
                lambda text: events.append(('text', text))):
            present_round(out, silent=False, is_tool_round=True)
        assert ('header', '回复') not in events
        assert ('text', 'TOOL PRELUDE') in events

    def test_reasoning_header_before_text(self):
        """同时有推理与正文时按 推理 → 正文 顺序输出。"""
        events = []
        out = RoundOutput(reasoning='R', content='C', tool_calls=[])
        with patch('common.cli_output.separate_print',
                lambda **kw: events.append(('h', kw.get('title')))), \
             patch('common.cli_output.stream_print',
                lambda text: events.append(('t', text))):
            present_round(out, silent=False)
        assert events == [('h', '推理过程'), ('t', 'R'),
            ('h', '回复'), ('t', 'C')]

    def test_silent_emits_nothing(self):
        events = []
        out = RoundOutput(reasoning='R', content='C', tool_calls=[])
        with patch('common.cli_output.separate_print',
                lambda **kw: events.append(('h', kw.get('title')))), \
             patch('common.cli_output.stream_print',
                lambda text: events.append(('t', text))):
            present_round(out, silent=True)
        assert events == []


# ── ToolRunner 钩子 ─────────────────────────────────────────

class TestToolRunnerHooks:
    @pytest.fixture
    def captured_executor(self):
        class FakeExecutor:
            def __init__(self): self.calls = []
            async def execute(self, name, args):
                self.calls.append((name, args))
                return 'OK'
        return FakeExecutor()

    @pytest.mark.asyncio
    async def test_post_exec_appends_extra_messages(self, captured_executor):
        from data_shape import ToolCall
        runner = tr.ToolRunner(executor=captured_executor)
        extra = []
        runner.register_post_exec('fancy_tool',
            lambda name, result, args, r, i: [{'role': 'user', 'content': 'hint'}])

        msgs: list[dict] = []
        tc = ToolCall(name='fancy_tool', arguments='{}', id='call_0')
        await runner.run([tc], msgs, round_idx=0)

        assert msgs[0]['role'] == 'tool'
        assert msgs[0]['tool_call_id'] == 'call_0'
        assert msgs[1] == {'role': 'user', 'content': 'hint'}

    @pytest.mark.asyncio
    async def test_synthetic_id_when_api_id_missing(self, captured_executor):
        from data_shape import ToolCall
        runner = tr.ToolRunner(executor=captured_executor)
        msgs: list[dict] = []
        tc = ToolCall(name='demo', arguments='{}', id='')  # API 没给 id
        await runner.run([tc], msgs, round_idx=3)
        assert msgs[0]['tool_call_id'] == 'call_3_0'
