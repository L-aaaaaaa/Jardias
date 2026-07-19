"""tool_runner — 工具执行调度。

职责：
- 接收 ``consume_stream`` 产生的 ``ToolCall`` 列表，逐个执行并写回 messages。
- 格式化日志与终端展示（不依赖具体工具名）。
- 通过 ``post_exec`` 扩展点允许工具模块在执行后注入额外 messages。

设计：
- 默认情况下，``ToolRunner(executor)`` 直接调用 ``executor.execute(name, args) -> str``。
- 工具模块可以通过 ``register_post_exec(name, hook)`` 注册一个 hook，
  当 ``ToolRunner.run`` 调用该工具时，hook 收到 ``(result, round_idx, idx)``
  并返回 ``list[dict]``（将作为附加 messages 插入到结果中）。

这样通用执行器不会因为 ``send_to_character`` 等具名工具而退化成单点胶水。
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Protocol

from common.logger import logger
from common.cli_output import get_silent, separate_print
from data_shape import ToolCall


class ToolExecutor(Protocol):
    """最小执行器接口：``tools.execute`` 必须返回字符串。"""

    async def execute(self, name: str, arguments: dict) -> str: ...


PostExecHook = Callable[[str, str, dict, int, int], list[dict]]
"""签名: (tool_name, result, arguments, round_idx, idx) -> extra_messages"""


def _format_args(args: dict | str) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return args[:80]
    if not isinstance(args, dict):
        return str(args)[:80]
    return ', '.join(f'{k}={v}' for k, v in args.items())


def _try_parse_args(args: dict | str) -> dict:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            return json.loads(args)
        except Exception:
            return {}
    return {}


def log_tool_calls(tool_calls: list[ToolCall]) -> None:
    if not tool_calls:
        return
    calls = [f'{tc.name}({_format_args(tc.arguments)})' for tc in tool_calls]
    logger.info(f'    [TOOL] 工具调用 | {len(tool_calls)} 个: {"; ".join(calls)}')


def display_tool_calls(tool_calls: list[ToolCall]) -> None:
    if get_silent() or not tool_calls:
        return
    separate_print(title='工具调用')
    for tc in tool_calls:
        args = _try_parse_args(tc.arguments)
        if tc.name == 'send_to_character':
            recipient = args.get('recipient', '?')
            message = args.get('message', '')
            print(f'  >> {tc.name} -> {recipient}:')
            print(f'     {message}')
        elif tc.name == 'shice_schedule_add':
            desc = args.get('message', args.get('description', '')) or '?'
            timestamps = args.get('timestamps', [])
            count = len(timestamps) if isinstance(timestamps, list) else '?'
            print(f'  >> {tc.name}: {desc[:60]}（{count} 个时间点）')
        else:
            print(f'  >> {tc.name}')


def log_tool_result(tool_name: str, result: str) -> None:
    if tool_name == 'send_to_character':
        lines = result.split('\n')
        brief = '\n'.join(lines[:2])
        if len(brief) > 300:
            brief = brief[:300] + '...'
        logger.info(f'    [RESULT] 工具结果 | {tool_name} ->\n{brief}')
    else:
        first_line = result.split('\n')[0]
        if len(first_line) > 120:
            first_line = first_line[:120] + '...'
        logger.info(f'    [RESULT] 工具结果 | {tool_name} -> {first_line}')


def display_tool_result(tool_name: str, result: str) -> None:
    if get_silent():
        return
    if tool_name == 'send_to_character':
        separate_print(title=f'{tool_name} 回复')
        try:
            print(f'  {result}')
        except UnicodeEncodeError:
            safe = result.encode('ascii', errors='ignore').decode('ascii')
            print(f'  [内容已简化] {safe[:500]}')
    elif tool_name == 'shice_schedule_add':
        print(f'  [OK] {tool_name}: {result[:200]}')
    else:
        print(f'\n  [OK] {tool_name}:\n{result[:300]}'
              f'{"..." if len(result) > 300 else ""}')


class ToolRunner:
    """异步执行 ``ToolCall`` 列表，把工具结果写回 messages。"""

    def __init__(self, executor: ToolExecutor):
        self._executor = executor
        self._post_execs: dict[str, PostExecHook] = {}

    def register_post_exec(self, name: str, hook: PostExecHook) -> None:
        """由工具模块在导入时调用：注册“执行后注入 messages”的钩子。"""
        self._post_execs[name] = hook

    async def run(self, tool_calls: list[ToolCall], messages: list[dict],
            round_idx: int, on_history_save: Callable[[], None] | None = None,
            ) -> list[dict]:
        """执行并把 ``tool`` 消息追加到 ``messages``，返回更新后的 messages。"""
        for idx, tc in enumerate(tool_calls):
            try:
                args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                result = await self._executor.execute(tc.name, args)
            except Exception as e:
                result = f'[Error] {type(e).__name__}: {e}'
            log_tool_result(tc.name, result)
            display_tool_result(tc.name, result)
            tc_id = getattr(tc, 'id', None) or f'call_{round_idx}_{idx}'
            messages.append({'role': 'tool', 'tool_call_id': tc_id,
                'name': tc.name, 'content': result})
            if on_history_save:
                on_history_save()
            hook = self._post_execs.get(tc.name)
            if hook is not None:
                for extra in hook(tc.name, result, args, round_idx, idx):
                    messages.append(extra)
        return messages


__all__ = [
    'ToolExecutor', 'ToolRunner', 'PostExecHook',
    'log_tool_calls', 'display_tool_calls',
    'log_tool_result', 'display_tool_result',
]
