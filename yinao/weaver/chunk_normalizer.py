"""chunk_normalizer — 纯解析的 chunk 规范化器。

职责：
- 把供应商原始 chunk 流规范化为 ``RoundOutput``（reasoning / content / tool_calls / usage / finish_reason）。
- 支持实时流式输出回调（on_reasoning / on_content），边解析边输出。

设计：
- 状态机增量解析；不依赖 OpenAI SDK 任何具体类型，只通过 ``getattr`` 读字段。
- 支持两种推理字段协议：
    - ``reasoning_content``：DeepSeek 类增量流（每片都直接是新增片段）。
    - ``reasoning_details``：MiniMax 类累计流（每片是完整累计值，需要前缀差量）。
- 支持自定义 think 标记（默认：``【think`` … ``】/think``），跨 chunk 安全。
- 支持实时流式输出：传入 ``on_reasoning`` / ``on_content`` 回调，在解析过程中即时调用。
"""
from __future__ import annotations

import re as _re
from typing import Callable

from data_shape import ToolCall, RoundOutput

THINK_OPEN = chr(12304) + 'think'
THINK_CLOSE = chr(12305) + '/think'
_THINK_CLEANUP_RE = _re.compile(
    _re.escape(THINK_OPEN) + r'.*?' + _re.escape(THINK_CLOSE) + r'\s*', _re.DOTALL)


# ────────────────────────────── 内部辅助 ──────────────────────────────


def _strip_tail_ambiguous(buffer: str, marker: str) -> str:
    """保留 buffer 末尾可能拼成 marker 的最长子串，其余视为安全。

    例: buffer='abc【thi', marker='【think' → 返回 '【thi'
    例: buffer='【th正文前缀', marker='【think' → 返回 '【th'（前缀出现任位置）
    """
    if not buffer:
        return ''
    keep = min(len(marker) - 1, len(buffer))
    for length in range(keep, 0, -1):
        prefix = marker[:length]
        if buffer.endswith(prefix):
            return buffer[-length:]
        if prefix in buffer:
            idx = buffer.rfind(prefix)
            return buffer[idx:]
    return ''


def _diff_cumulative(prev: str, cur: str) -> str:
    """累计流：求 cur 相对 prev 的新增片段。

    - cur 是 prev 扩展 → 取后缀
    - cur 比 prev 短（服务端回退）→ 空
    - 否则 → 把 cur 当作新一段
    """
    if not cur:
        return ''
    if cur.startswith(prev):
        return cur[len(prev):]
    if prev.startswith(cur):
        return ''
    return cur


# ────────────────────────────── chunk 处理 ──────────────────────────────


def _process_chunk(chunk, accum: dict, *,
        on_reasoning: Callable[[str], None] | None = None,
        on_content: Callable[[str], None] | None = None) -> None:
    """单 chunk 解析：累加 reasoning/content/tool_calls/usage/finish_reason。"""
    usage = getattr(chunk, 'usage', None)
    if usage:
        accum['usage'] = usage.model_dump() if hasattr(usage, 'model_dump') else None

    choices = getattr(chunk, 'choices', None)
    if not choices:
        return

    delta = choices[0].delta
    accum['finish_reason'] = choices[0].finish_reason

    _extract_reasoning(delta, accum, on_reasoning=on_reasoning)
    _extract_content(delta, accum, on_content=on_content)
    _extract_tool_calls(delta, accum)


def _extract_reasoning(delta, accum: dict, *,
        on_reasoning: Callable[[str], None] | None = None) -> None:
    if accum['reasoning_field'] == 'reasoning_content':
        rc = getattr(delta, 'reasoning_content', None)
        if not rc:
            return
        prev, cur = accum['reasoning_prev'], rc
        if cur.startswith(prev):
            new_text = cur[len(prev):]
            accum['reasoning_prev'] = cur
        elif prev.startswith(cur):
            new_text = ''
            accum['reasoning_prev'] = cur
        else:
            new_text = cur
            accum['reasoning_prev'] = cur
        if new_text:
            _append_reasoning(accum, new_text)
            if on_reasoning:
                on_reasoning(new_text)
    else:
        details = getattr(delta, 'reasoning_details', None)
        if not details:
            return
        text_pieces = []
        for d in details:
            text = d.get('text', d.get('content', '')) if isinstance(d, dict) else str(d)
            if text:
                text_pieces.append(text)
        joined = ''.join(text_pieces)
        if not joined:
            return
        delta_text = _diff_cumulative(accum['reasoning_prev'], joined)
        accum['reasoning_prev'] = joined
        if delta_text:
            _append_reasoning(accum, delta_text)
            if on_reasoning:
                on_reasoning(delta_text)


def _append_reasoning(accum: dict, text: str) -> None:
    if accum['reasoning_source'] is None:
        accum['reasoning_source'] = 'field'
    if accum['reasoning_source'] == 'field':
        accum['reasoning_parts'].append(text)


def _extract_content(delta, accum: dict, *,
        on_content: Callable[[str], None] | None = None) -> None:
    dc = getattr(delta, 'content', None)
    if not dc:
        return
    accum['think_buffer'] += dc
    _flush_think_buffer(accum, on_content=on_content)


def _extract_tool_calls(delta, accum: dict) -> None:
    tcs = getattr(delta, 'tool_calls', None)
    if not tcs:
        return
    accum['terminated_by_tool'] = True
    for tc_d in tcs:
        idx = tc_d.index
        while idx >= len(accum['tool_calls']):
            accum['tool_calls'].append({'id': '', 'name': '', 'arguments': ''})
        entry = accum['tool_calls'][idx]
        tc_id = getattr(tc_d, 'id', None)
        name = getattr(tc_d.function, 'name', None) or ''
        args = getattr(tc_d.function, 'arguments', None) or ''
        if tc_id:
            entry['id'] = tc_id
        if name:
            entry['name'] = name
        if args:
            entry['arguments'] += args


# ────────────────────────────── think 状态机 ──────────────────────────────


def _flush_think_buffer(accum: dict, *,
        on_content: Callable[[str], None] | None = None) -> None:
    """在 think/正文状态间迁移。安全前缀保留到下次递归。"""
    buf = accum['think_buffer']
    if not buf:
        return

    if not accum['in_think']:
        idx = buf.find(THINK_OPEN)
        if idx >= 0:
            pre = buf[:idx]
            buf = buf[idx + len(THINK_OPEN):]
            if pre:
                accum['content_buffer'].append(pre)
                if on_content:
                    on_content(pre)
            accum['in_think'] = True
            accum['think_acc'] = ''
            if accum['reasoning_source'] is None:
                accum['reasoning_source'] = 'think'
            accum['think_buffer'] = buf
            _flush_think_buffer(accum, on_content=on_content)
            return
        safe = _strip_tail_ambiguous(buf, THINK_OPEN)
        if len(safe) < len(buf):
            emit = buf[:len(buf) - len(safe)]
            if emit:
                accum['content_buffer'].append(emit)
                if on_content:
                    on_content(emit)
            accum['think_buffer'] = safe
    else:
        idx = buf.find(THINK_CLOSE)
        if idx >= 0:
            pre = buf[:idx]
            post = buf[idx + len(THINK_CLOSE):]
            if pre:
                accum['think_acc'] += pre
                if accum['reasoning_source'] == 'think':
                    accum['reasoning_parts'].append(pre)
            accum['in_think'] = False
            accum['think_acc'] = ''
            if post:
                accum['content_buffer'].append(post)
                if on_content:
                    on_content(post)
            accum['think_buffer'] = ''
            if post:
                _flush_think_buffer(accum, on_content=on_content)
            return
        safe = _strip_tail_ambiguous(buf, THINK_CLOSE)
        if len(safe) < len(buf):
            emit = buf[:len(buf) - len(safe)]
            if emit:
                accum['think_acc'] += emit
                if accum['reasoning_source'] == 'think':
                    accum['reasoning_parts'].append(emit)
            accum['think_buffer'] = safe


# ────────────────────────────── 公开 API ──────────────────────────────


def collect_stream(stream, *, reasoning_field: str = 'reasoning_details',
        is_tool_round: bool = False,
        on_reasoning: Callable[[str], None] | None = None,
        on_content: Callable[[str], None] | None = None) -> RoundOutput:
    """消费原始 chunk 流，返回结构化 ``RoundOutput``。

    参数：
    - stream：可迭代（生成器或列表）；每个元素需要支持 ``choices`` / ``usage`` 属性。
    - reasoning_field：``'reasoning_details'``（累计流，MiniMax 类）
      或 ``'reasoning_content'``（增量流，DeepSeek 类）。
    - is_tool_round：仅作为元数据被附加到 ``RoundOutput``；当前不影响解析逻辑。
    - on_reasoning：推理内容实时回调（每解析到新推理片段时调用）。
    - on_content：正文内容实时回调（每解析到新正文片段时调用）。

    返回：``RoundOutput(reasoning, content, tool_calls, finish_reason, usage)``
    """
    accum = _new_accumulator(reasoning_field)
    for chunk in stream:
        _process_chunk(chunk, accum, on_reasoning=on_reasoning, on_content=on_content)

    # 收尾：未关闭的 think 标签视为正文（剥掉外壳）
    _finalize_think_buffer(accum, on_content=on_content)

    calls = [ToolCall(id=e['id'], name=e['name'], arguments=e['arguments'])
             for e in accum['tool_calls'] if e['name']]
    content = ''.join(accum['content_buffer']).strip()
    reasoning = ''.join(accum['reasoning_parts'])
    return RoundOutput(reasoning=reasoning, content=content, tool_calls=calls,
        finish_reason=accum['finish_reason'], usage=accum['usage'])


def _new_accumulator(reasoning_field: str) -> dict:
    return {
        'reasoning_field': reasoning_field,
        'in_think': False,
        'think_buffer': '',
        'think_acc': '',
        'content_buffer': [],
        'reasoning_parts': [],
        'reasoning_prev': '',
        'reasoning_source': None,
        'terminated_by_tool': False,
        'tool_calls': [],
        'usage': None,
        'finish_reason': None,
    }


def _finalize_think_buffer(accum: dict, *,
        on_content: Callable[[str], None] | None = None) -> None:
    if accum['in_think']:
        return  # 整段已记入 reasoning，不重复处理
    tail = accum['think_buffer']
    if not tail:
        return
    cleaned = _THINK_CLEANUP_RE.sub('', tail)
    for marker in (THINK_OPEN, THINK_CLOSE):
        while any(cleaned.startswith(marker[:k])
                for k in range(1, len(marker) + 1)):
            for k in range(len(marker), 0, -1):
                if cleaned.startswith(marker[:k]):
                    cleaned = cleaned[k:]
                    break
    if cleaned:
        accum['content_buffer'].append(cleaned)
        if on_content:
            on_content(cleaned)


__all__ = ['collect_stream', 'THINK_OPEN', 'THINK_CLOSE']
