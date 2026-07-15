"""thought_weaver — 思绪编织器：ReAct 编排入口。

仅负责：
- 单轮：构造流 → 解析 → 展示 → 写历史。
- 多轮：循环调用单轮直到 LLM 不再要求工具调用，过程中检查引擎切换。

模块刻意保持小：解析在 ``chunk_normalizer``、展示在 ``presenter``、
工具调度在 ``tool_runner``。新增工具 hook 通过 ``ToolRunner.register_post_exec``。
"""
from __future__ import annotations

import time
from typing import Callable

from common.actor_log import (
    round_start, round_end, max_rounds_reached,
    format_api_ok, format_round_usage,
)
from common.logger import logger
from common.cli_output import set_display_name, get_silent, separate_print, stream_print, set_stream_color
from data_shape import ChatResult, RoundOutput
from .ipu_context import set_round_meta
from yinao.launcher import pop_switch, get_ipu_stream_reply
from .chunk_normalizer import collect_stream
from .tool_runner import (
    ToolRunner, log_tool_calls, display_tool_calls,
)

WEAVE_MAX_TURNS = 999


# ───────────────────────────── 单轮执行 ─────────────────────────────


async def _run_single_round(messages: list[dict], iteration: int, ipu_config,
        reasoning_field: str = 'reasoning_details',
        reasoning_inline: bool = False, character_name: str = '',
        is_tool_round: bool = False,
        on_history_save: Callable[[], None] | None = None,
        tool_runner: ToolRunner | None = None) -> tuple[RoundOutput, list[dict]]:
    """执行一轮：构造流 → 解析 → 展示 → 写历史。"""
    if character_name:
        set_display_name(character_name)
    round_start(iteration + 1, len(messages))
    stream = get_ipu_stream_reply(messages, ipu_config=ipu_config)
    t0 = time.perf_counter()
    silent = get_silent()

    # 实时流式输出回调
    reasoning_header_printed = [False]
    content_header_printed = [not is_tool_round]  # 非工具轮次默认打印回复标题

    def on_reasoning(text: str):
        """推理内容实时输出"""
        if silent:
            return
        if not reasoning_header_printed[0]:
            reasoning_header_printed[0] = True
            set_stream_color('yellow')
            separate_print(title='推理过程')
        stream_print(text)

    def on_content(text: str):
        """正文内容实时输出"""
        if silent:
            return
        if content_header_printed[0]:
            content_header_printed[0] = False
            separate_print(title='回复')
        stream_print(text)

    output = collect_stream(stream, reasoning_field=reasoning_field,
        is_tool_round=is_tool_round,
        on_reasoning=on_reasoning, on_content=on_content)

    if not silent:
        print()
        if output.content.strip():
            separate_print(end=True)
    line = format_round_usage(output.usage)
    if line and not silent:
        print(line)

    elapsed = time.perf_counter() - t0
    logger.info(f'    {format_api_ok(elapsed, output.usage, output.finish_reason)}')
    set_round_meta(elapsed, output.usage, output.finish_reason)

    if not reasoning_inline and output.reasoning:
        messages.append({'role': 'assistant', 'content': output.reasoning,
            '_reasoning': True})
    msg: dict = {'role': 'assistant', 'content': output.content}
    if reasoning_inline and output.reasoning:
        msg['reasoning_content'] = output.reasoning
    if output.tool_calls:
        msg['tool_calls'] = [{
            'id': getattr(tc, 'id', None) or f'call_{iteration}_{i}',
            'type': 'function',
            'function': {'name': tc.name, 'arguments': tc.arguments}}
            for i, tc in enumerate(output.tool_calls)]
    messages.append(msg)
    if on_history_save:
        on_history_save()
    return output, messages


# ───────────────────────────── 主循环 ─────────────────────────────


async def weave_thought(messages: list[dict], ipu_config,
        reasoning_field: str = 'reasoning_details',
        reasoning_inline: bool = False, character_name: str = '',
        on_history_save: Callable[[], None] | None = None,
        tool_runner: ToolRunner | None = None) -> ChatResult:
    """驱动 ReAct 循环直到模型停止调用工具或达到 WEAVE_MAX_TURNS。

    命名类比：每一轮把推理、工具调用、结果回填"织"成一段连续思绪；
    整段思绪（``weave_thought``）由多回合编织组成。
    """
    if tool_runner is None:
        tool_runner = _default_tool_runner()

    for i in range(WEAVE_MAX_TURNS):
        output, messages = await _run_single_round(
            messages, i, ipu_config,
            reasoning_field=reasoning_field,
            reasoning_inline=reasoning_inline,
            character_name=character_name,
            is_tool_round=(i > 0),
            on_history_save=on_history_save,
            tool_runner=tool_runner,
        )

        if output.tool_calls:
            log_tool_calls(output.tool_calls)
            display_tool_calls(output.tool_calls)
            messages = await tool_runner.run(output.tool_calls, messages, i,
                on_history_save=on_history_save)
            switch = pop_switch()
            if switch:
                return ChatResult(messages=messages, should_switch=True,
                    switch_provider=switch.provider, switch_ipu=switch.ipu)
        else:
            round_end(i + 1, 'no tool calls' if i == 0 else 'tool chain done')
            return ChatResult(messages=messages)

    max_rounds_reached(MAX_ITER)
    return ChatResult(messages=messages)


# ───────────────────────────── 默认工具注册入口 ─────────────────────────────


def _default_tool_runner() -> ToolRunner:
    """构造与 ``tool.builtin.tools`` 绑定的默认执行器，并把
    ``send_to_character`` 的特殊消息 hook 注册到工具自己的实现里。"""
    from tool.builtin import tools  # 延迟：避免循环导入

    runner = ToolRunner(executor=tools)

    # 让 ``tool/builtin_tools/characters.py`` 自己决定要追加什么消息。
    from tool.builtin_tools.characters import register_post_exec as _register  # type: ignore
    try:
        _register(runner)
    except (AttributeError, ImportError):
        pass
    return runner


__all__ = ['weave_thought', 'WEAVE_MAX_TURNS']  # 公开思绪编织入口与回合上限
