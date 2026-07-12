from __future__ import annotations

import json
import os
import re
import time

from openai import OpenAI

from character import get_character_dir
from common.actor_log import round_start, round_end, max_rounds_reached, format_api_ok, format_round_usage
from common.logger import logger
from common.utils import separate_print, stream_print, set_display_name, get_silent, set_stream_color
from data_shape import IPUProvider, IPUConfig, ToolCall, RoundOutput, ChatResult
from .ipu_context import set_round_meta, pop_switch


# ————————————————————————————————————————————————————————
#  Client
# ————————————————————————————————————————————————————————


def form_client(provider: IPUProvider | None = None):
    if provider is None:
        provider = IPUProvider()
    return OpenAI(api_key=provider.api_key, base_url=provider.base_url)


def single_completion(
        client: OpenAI,
        ipu: str,
        messages: list[dict],
        temperature: float = 0.0,
        max_icp: int = 512,
) -> str:
    """非流式单次 API 调用，返回纯文本（@actor_tool 用）。

    API 协议层仍使用 max_completion_tokens / model，IPU 抽象层用 max_icp / ipu。
    """
    response = client.chat.completions.create(
        messages=messages,
        model=ipu,
        temperature=temperature,
        max_completion_tokens=max_icp,
    )
    if not response.choices:
        return ""
    return response.choices[0].message.content or ""


def form_stream(full_context_list: list, client=None, ipu_config=None):
    """构造流式请求。

    API 协议层使用 OpenAI 兼容字段（model / max_completion_tokens），
    IPUConfig 字段（ipu / max_icp）在调用层映射。
    """
    if ipu_config is None:
        ipu_config = IPUConfig()
    if client is None:
        client = OpenAI(api_key=ipu_config.api_key, base_url=ipu_config.base_url)

    return client.chat.completions.create(
        messages=[{k: v for k, v in m.items() if k != "_reasoning"} for m in full_context_list],
        model=ipu_config.ipu,
        extra_body=ipu_config.extra_body,
        stream=ipu_config.stream,
        # 流式响应必须显式 include_usage，OpenAI 默认不返回 usage
        stream_options={"include_usage": True},
        temperature=ipu_config.temperature,
        top_p=ipu_config.top_p,
        max_completion_tokens=ipu_config.max_icp,
        tools=ipu_config.tools if ipu_config.tools else None,
        tool_choice=ipu_config.tool_choice if ipu_config.tool_choice else None,
        reasoning_effort=ipu_config.reasoning_effort,
    )


# ————————————————————————————————————————————————————————
#  输出工具 (委托给 utils.py)
# ————————————————————————————————————————————————————————


# ————————————————————————————————————————————————————————
#  流式响应收集
# ── 流式响应收集 ──


def collect_round(stream, reasoning_field: str = "reasoning_details", is_tool_round: bool = False) -> RoundOutput:
    """
    消费流式响应，边接收边流式输出，同时返回结构化结果。
    reasoning_field: "reasoning_details" (MiniMax) | "reasoning_content" (DeepSeek/DashScope)
    """
    reasoning_parts, content_parts, fc_names, fc_args_parts, deltas = [], [], [], [], []
    _think_parts: list[str] = []  # <think> 内容独立存储，不混入 reasoning_parts
    _printed_reasoning_len = 0  # 诊断：实际 stream_print 输出的字符数
    reasoning_header = False
    content_header = False
    response_header = False
    # MiniMax <think> 标签状态机 — 检测嵌在 content 里的思考内容
    _in_think = False
    _think_acc = ""
    _reasoning_from_field = False  # 专用字段（reasoning_details/content）是否已提供推理
    _primary_source = None  # 推理主源：先到者赢，另一源整段跳过（防双源重复）
    _rc_last = ""  # reasoning_content 累积式 diff 基准（DeepSeek 每个 delta 带全量文本）
    _rd_last = ""  # reasoning_details 累积式 diff 基准（MiniMax 同样累积）
    _rd_printed_lines: list[str] = []  # 行级去重：MiniMax 自修改导致累积 diff 失配时的兜底
    _content_buf: list[str] = []  # 推理到达前的 content 缓冲（MinMax 推理泄漏）
    _waiting_reasoning = (reasoning_field == "reasoning_details")  # MinMax 推理先于 content 到达
    finish_reason = None
    usage: dict | None = None

    def _ensure_reasoning_header():
        """统一管理推理标题——无论来源，只打印一次。"""
        nonlocal reasoning_header
        if not reasoning_header:
            reasoning_header = True
            set_stream_color("yellow")
            separate_print(title="推理过程")

    def _ensure_content_header():
        """非工具轮次：从推理切换到回复。工具轮次静默。"""
        nonlocal content_header
        if not content_header and not is_tool_round:
            content_header = True
            separate_print(title="回复")

    for chunk in stream:
        # 尾 chunk 可能不带 choices（仅含 usage），跳过
        if not getattr(chunk, "choices", None):
            if hasattr(chunk, "usage") and chunk.usage:
                usage = chunk.usage.model_dump() if hasattr(chunk.usage, "model_dump") else None
            continue
        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta
        deltas.append(delta)

        # 推理文本 — 实时流式输出（双源去重：先到者为主源独占；reasoning_content 累积式 diff）
        if reasoning_field == "reasoning_content":
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                _reasoning_from_field = True
                if _primary_source is None:
                    _primary_source = "reasoning_content"
                    _ensure_reasoning_header()
                if _primary_source == "reasoning_content":
                    # DeepSeek reasoning_content：可能是累积式也可能是非累积式
                    if rc.startswith(_rc_last):
                        # 累积式：只打印增量
                        new_text = rc[len(_rc_last):]
                        _rc_last = rc
                        if new_text:
                            reasoning_parts.append(new_text)
                            stream_print(new_text)
                    else:
                        # 非累积式：每个 delta 仅含新分块 → 手动累积 _rc_last
                        reasoning_parts.append(rc)
                        stream_print(rc)
                        _rc_last += rc
        elif hasattr(delta, reasoning_field) and getattr(delta, reasoning_field):
            details = getattr(delta, reasoning_field)
            if details:
                _reasoning_from_field = True
                if _primary_source is None:
                    _primary_source = "reasoning_details"
                    _ensure_reasoning_header()
                for d in details:
                    text = d.get("text", d.get("content", "")) if isinstance(d, dict) else str(d)
                    if text and _primary_source == "reasoning_details":
                        # MiniMax reasoning_details 也是累积全量 → 只打印增量
                        if text.startswith(_rd_last):
                            new_text = text[len(_rd_last):]
                            _rd_last = text
                            if new_text:
                                # 行级去重兜底：LLM 自修改时累积 diff 可能失配，逐行比对
                                _output_lines = []
                                for line in new_text.split("\n"):
                                    stripped = line.rstrip()
                                    if stripped and stripped not in _rd_printed_lines:
                                        _rd_printed_lines.append(stripped)
                                        _output_lines.append(line)
                                if _output_lines:
                                    merged = "\n".join(_output_lines)
                                    reasoning_parts.append(merged)
                                    stream_print(merged)
                        else:
                            # LLM 自修改导致 startswith 失败 → 全量文本但行级去重
                            new_lines = []
                            for line in text.split("\n"):
                                stripped = line.rstrip()
                                if stripped and stripped not in _rd_printed_lines:
                                    _rd_printed_lines.append(stripped)
                                    new_lines.append(line)
                            if new_lines:
                                merged = "\n".join(new_lines)
                                reasoning_parts.append(merged)
                                stream_print(merged)
                            _rd_last = text
                # 推理首轮到达 → 冲刷缓冲的 content（行级去重）
                if _content_buf:
                    _buf_text = "".join(_content_buf)
                    # 检查缓冲内容是否与已输出的推理行重复
                    _buf_lines = [l.rstrip() for l in _buf_text.split("\n") if l.rstrip()]
                    _new_buf_lines = [l for l in _buf_lines if l not in _rd_printed_lines]
                    if _new_buf_lines:
                        if not response_header:
                            response_header = True
                            _ensure_content_header()
                        merged = "\n".join(_new_buf_lines)
                        content_parts.append(merged)
                        stream_print(merged)
                    _content_buf.clear()

        # 回复内容 — 实时流式输出
        # <think> 内容存入独立 _think_parts，不与 reasoning_parts 混合。
        # _reasoning_from_field=True → 专用字段已提供推理，最终合并时丢弃 _think_parts。
        if delta.content:
            dc = delta.content

            if _in_think:
                if "</think>" in dc:
                    pre, post = dc.split("</think>", 1)
                    _think_acc += pre
                    _think_parts.append(_think_acc)
                    if _primary_source is None:
                        _primary_source = "think"
                        _ensure_reasoning_header()
                    if _primary_source == "think":
                        # 行级去重（统一所有推理源的输出口径）
                        _new_think = []
                        for line in _think_acc.split("\n"):
                            stripped = line.rstrip()
                            if stripped and stripped not in _rd_printed_lines:
                                _rd_printed_lines.append(stripped)
                                _new_think.append(line)
                        if _new_think:
                            merged = "\n".join(_new_think)
                            stream_print(merged)
                    _in_think = False
                    _think_acc = ""
                    if post:
                        if not response_header:
                            response_header = True
                            _ensure_content_header()
                        content_parts.append(post)
                        stream_print(post)
                else:
                    _think_acc += dc
                    if _primary_source is None:
                        _primary_source = "think"
                        _ensure_reasoning_header()
                    if _primary_source == "think":
                        # 行级去重：小 chunk 逐行比对
                        _new_think = []
                        for line in dc.split("\n"):
                            stripped = line.rstrip()
                            if stripped and stripped not in _rd_printed_lines:
                                _rd_printed_lines.append(stripped)
                                _new_think.append(line)
                        if _new_think:
                            stream_print("\n".join(_new_think))
            elif "<think>" in dc:
                pre, post = dc.split("<think>", 1)
                if pre:
                    if not response_header:
                        response_header = True
                        separate_print(title="回复")
                    content_parts.append(pre)
                    stream_print(pre)
                _in_think = True
                _think_acc = post
                if _primary_source is None:
                    _primary_source = "think"
                    _ensure_reasoning_header()
                if _primary_source == "think":
                    # 行级去重
                    _new_think = []
                    for line in post.split("\n"):
                        stripped = line.rstrip()
                        if stripped and stripped not in _rd_printed_lines:
                            _rd_printed_lines.append(stripped)
                            _new_think.append(line)
                    if _new_think:
                        stream_print("\n".join(_new_think))
            else:
                # MinMax: reasoning_details 到达前 content 可能是推理泄漏 → 缓冲，等推理到达后去重输出
                if _waiting_reasoning and _primary_source is None:
                    _content_buf.append(dc)
                else:
                    if not response_header:
                        response_header = True
                        separate_print(title="回复")
                    content_parts.append(dc)
                    stream_print(dc)

        # 工具调用（name 和 arguments 分块到达，按 index 拼装）
        if hasattr(delta, "tool_calls") and delta.tool_calls:
            for tc_d in delta.tool_calls:
                idx = tc_d.index
                while idx >= len(fc_names):
                    fc_names.append("")
                    fc_args_parts.append("")
                name = getattr(tc_d.function, "name", None) or ""
                args = getattr(tc_d.function, "arguments", None) or ""
                if name:
                    fc_names[idx] = name
                if args:
                    fc_args_parts[idx] += args

        # 用量（仅尾 chunk）
        if hasattr(chunk, "usage") and chunk.usage:
            usage = chunk.usage.model_dump() if hasattr(chunk.usage, "model_dump") else None
        if not chunk.choices:
            continue
        finish_reason = chunk.choices[0].finish_reason

        if finish_reason == "tool_calls":
            break

    reasoning = _rd_last or _rc_last or "".join(reasoning_parts)
    # 非累积式 reasoning_content 修复：DeepSeek 每个 delta 仅含新分块
    # _rc_last 可能只保留最后一个分块（远短于完整推理），此时以 parts 拼接为准
    _reasoning_from_parts = "".join(reasoning_parts)
    if _rc_last and len(_rc_last) < len(_reasoning_from_parts):
        reasoning = _reasoning_from_parts
    # 诊断：对比 API 累积推理 vs stream_print 拼回，不一致时打日志
    _streamed_len = len("".join(reasoning_parts))
    _cumulative_len = len(_rc_last) if _rc_last else 0
    if _rc_last and _streamed_len != _cumulative_len:
        logger.debug(
            f"    [推理诊断] API累积={_cumulative_len}字 stream拼回={_streamed_len}字 "
            f"差值={_cumulative_len - _streamed_len}"
        )
    # 行级去重时 reasoning_parts 只含增量行 → 用 _rd_printed_lines 重建完整推理
    if _rd_printed_lines and not (_rd_last or _rc_last):
        reasoning = "\n".join(_rd_printed_lines)
    # <think> 内容：仅当专用字段未产出推理时，才作为推理源
    if not _reasoning_from_field and _think_parts:
        reasoning = "".join(_think_parts)
    content = "".join(content_parts)
    # 安全网：清除 content 中残留的 <think> 标签（MiniMax 嵌入行为）
    content = _re.sub(r"<think>.*?</think>\s*", "", content, flags=_re.DOTALL).strip()
    # 未闭合的 <think> 标签 → 归入推理
    if _in_think and _think_acc:
        reasoning += _think_acc
    calls = [ToolCall(n, a) for n, a in zip(fc_names, fc_args_parts) if n]
    return RoundOutput(reasoning, content, calls, deltas, finish_reason=finish_reason, usage=usage)


import re as _re


def _has_dedicated_reasoning(deltas: list) -> bool:
    """检查 deltas 中是否有专用推理字段（reasoning_content 或 reasoning_details）产生过内容。"""
    for delta in deltas:
        if getattr(delta, "reasoning_content", None):
            return True
        if hasattr(delta, "reasoning_details") and delta.reasoning_details:
            return True
    return False


def replay_deltas(deltas: list):
    """将缓冲的 deltas 重放为流式输出（推理 + 内容）。

    若专用字段（reasoning_details/reasoning_content）提供了推理 → 重放专用字段，
    同时从 content 中清除 <think> 标签避免重复。
    若仅靠 <think> 标签嵌入 → 保留 content 中的 <think> 内容作为推理显示。
    """
    has_dedicated = _has_dedicated_reasoning(deltas)

    if has_dedicated:
        separate_print(title="推理过程")
        _rc_last_replay = ""
        _rseen_replay: set[str] = set()
        for delta in deltas:
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                # 累积式 reasoning_content：只打印增量
                if rc.startswith(_rc_last_replay):
                    new = rc[len(_rc_last_replay):]
                    _rc_last_replay = rc
                    if new and new not in _rseen_replay:
                        _rseen_replay.add(new)
                        stream_print(new)
                else:
                    if rc not in _rseen_replay:
                        _rseen_replay.add(rc)
                        stream_print(rc)
                    _rc_last_replay = rc
            if hasattr(delta, "reasoning_details") and delta.reasoning_details:
                _rd_seen_replay: list[str] = []
                for d in delta.reasoning_details:
                    text = d.get("text", d.get("content", "")) if isinstance(d, dict) else str(d)
                    if text:
                        # 行级去重：只输出新行
                        new_lines = []
                        for line in text.split("\n"):
                            stripped = line.rstrip()
                            if stripped and stripped not in _rd_seen_replay:
                                _rd_seen_replay.append(stripped)
                                new_lines.append(line)
                        if new_lines:
                            stream_print("\n".join(new_lines))

    # 拼装全部 content，根据是否有专用推理决定是否清除 <think> 标签
    full = "".join(d.content or "" for d in deltas)
    if has_dedicated:
        clean = _re.sub(r"<think>.*?</think>\s*", "", full, flags=_re.DOTALL).strip()
    else:
        clean = full.strip()
    if clean:
        separate_print(title="回复")
        stream_print(clean)


# ————————————————————————————————————————————————————————
#  工具执行
# ————————————————————————————————————————————————————————


async def execute_tool(name: str, arguments: dict) -> str:
    from tool.builtin import tools
    return await tools.execute(name, arguments)


# ————————————————————————————————————————————————————————
#  纯流式（无 Reason-Action）
# ————————————————————————————————————————————————————————


class StreamState:
    def __init__(self):
        self.accumulated_thought = ""
        self.accumulated_content = ""
        self.is_thinking = False
        self.content_started = False


def handle_reasoning(delta, state: StreamState):
    rc = getattr(delta, "reasoning_content", None)
    if rc:
        if not state.is_thinking:
            state.is_thinking = True
            separate_print(title="推理过程")
        state.accumulated_thought += rc
        stream_print(rc)
    if hasattr(delta, "reasoning_details") and delta.reasoning_details:
        if not state.is_thinking:
            state.is_thinking = True
            separate_print(title="推理过程")
        for detail in delta.reasoning_details:
            text = detail.get("text", detail.get("content", "")) if isinstance(detail, dict) else str(detail)
            state.accumulated_thought += text
            stream_print(text)


def handle_tool_calls(delta, state: StreamState):
    tc_list = getattr(delta, "tool_calls", None)
    if not tc_list:
        return
    for tc_d in tc_list:
        if not state.content_started:
            state.content_started = True
            separate_print(title="工具调用")
        fname = getattr(tc_d.function, "name", "") or ""
        fargs = getattr(tc_d.function, "arguments", "") or ""
        print(f"  >> 工具调用: {fname}")
        print(f"     参数: {fargs}")


def handle_content(delta, state: StreamState):
    if not delta.content:
        return
    if not state.content_started:
        state.content_started = True
        if not state.is_thinking:
            separate_print(title="无推理")
        separate_print(title="回复")
    state.accumulated_content += delta.content
    stream_print(delta.content)


def stream_chat(full_context_list: list[dict[str, str]], ipu_config=None):
    stream = form_stream(full_context_list, ipu_config=ipu_config)
    state = StreamState()
    for chunk in stream:
        if not getattr(chunk, "choices", None) or not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        handle_reasoning(delta, state)
        handle_tool_calls(delta, state)
        handle_content(delta, state)
    return state.accumulated_content


# ————————————————————————————————————————————————————————
#  公共 Reason-Action 循环（Item 1 + 2: 返回值 + 提取公共循环）
# ————————————————————————————————————————————————————————

MAX_ITER = 999


async def _run_common_round(
        messages: list[dict],
        iteration: int,
        ipu_config,
        reasoning_field: str = "reasoning_details",
        reasoning_inline: bool = False,
        character_name: str = "",
        is_tool_round: bool = False,
        on_history_save: callable | None = None,
):
    """公共单轮执行：流式请求 + 响应收集 + assistant 消息组装。

    reasoning_field: "reasoning_details" (MiniMax) | "reasoning_content" (DeepSeek/DashScope)
    reasoning_inline: True → reasoning 嵌入 assistant 消息 (DeepSeek)
                     False → reasoning 作为独立消息 (MiniMax/DashScope)
    character_name: 角色名，非空时每次 API 调用前写入 experience.md
    is_tool_round: 是否为工具调用的后续轮次（True 时不重复打印"回复"标题）
    on_history_save: 每次 API 调用后触发 history 保存回调（用于实时更新 history.json）
    """
    round_start(iteration + 1, len(messages))

    # 上下文拦截器：每轮 API 调用前写入完整上下文快照
    # 已移除：dump_experience 调用
    # 原因：此时 history.json 中的 assistant 还未写入，导致 experience.md 快照不完整
    # experience.md 的写入统一由 _post_round_async 在对话结束时处理

    stream = form_stream(messages, ipu_config=ipu_config)
    t0 = time.time()
    output = collect_round(stream, reasoning_field=reasoning_field, is_tool_round=is_tool_round)
    print()  # 流式输出收尾换行
    if output.content.strip():
        separate_print(end=True)
    # 用户视角：本轮消耗（紧贴虚线）
    line = format_round_usage(output.usage)
    if line:
        print(line)
    elapsed = time.time() - t0
    logger.info(f"    {format_api_ok(elapsed, output.usage, output.finish_reason)}")
    set_round_meta(elapsed, output.usage, output.finish_reason)

    # 组装 assistant 消息
    if reasoning_inline:
        # DeepSeek: reasoning_content 嵌入同一条消息
        msg: dict = {"role": "assistant", "content": output.content}
        if output.reasoning:
            msg["reasoning_content"] = output.reasoning
    else:
        # MiniMax / DashScope: reasoning 作为独立 assistant 消息
        if output.reasoning:
            messages.append({"role": "assistant", "content": output.reasoning, "_reasoning": True})
        msg = {"role": "assistant", "content": output.content}

    if output.tool_calls:
        msg["tool_calls"] = [
            {"id": f"call_{iteration}_{i}", "type": "function",
             "function": {"name": tc.name, "arguments": tc.arguments}}
            for i, tc in enumerate(output.tool_calls)
        ]

    messages.append(msg)

    # 触发 history 保存回调（实时更新 history.json）
    if on_history_save:
        on_history_save()

    return output, messages


async def reason_action_loop(
        messages: list[dict],
        ipu_config,
        reasoning_field: str = "reasoning_details",
        reasoning_inline: bool = False,
        character_name: str = "",
        on_history_save: callable | None = None,
) -> ChatResult:
    """公共 Reason-Action 循环：多轮工具调用，直到模型给出最终回复。

    返回 ChatResult — 用 should_switch 替代 IPUSwitched 异常。
    character_name 非空时，每轮 API 调用前写入 experience.md。
    每次工具执行后也会写入 experience.md 并触发 on_history_save。
    on_history_save: 每次 API 调用后触发 history 保存回调（用于实时更新 history.json）
    """
    if character_name:
        set_display_name(character_name)
    last_content = ""

    for i in range(MAX_ITER):
        output, messages = await _run_common_round(
            messages, i, ipu_config,
            reasoning_field=reasoning_field,
            reasoning_inline=reasoning_inline,
            character_name=character_name,
            is_tool_round=(i > 0),
            on_history_save=on_history_save,
        )
        last_content = output.content

        if output.tool_calls:
            _log_tool_calls_common(output.tool_calls)
            # ── 终端显示：工具调用 ──
            if not get_silent():
                separate_print(title="工具调用")
                for tc in output.tool_calls:
                    try:
                        args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                    except Exception:
                        args = {}
                    if tc.name == "send_to_character":
                        recipient = args.get("recipient", "?")
                        message = args.get("message", "")
                        print(f"  >> {tc.name} → {recipient}:")
                        print(f"     {message}")
                    elif tc.name == "shice_schedule_add":
                        desc = args.get("message", args.get("description", "")) or "?"
                        timestamps = args.get("timestamps", [])
                        count = len(timestamps) if isinstance(timestamps, list) else "?"
                        print(f"  >> {tc.name}: {desc[:60]}（{count} 个时间点）")
                    else:
                        print(f"  >> {tc.name}")
            # ── 执行 + 显示结果 ──
            for idx, tc in enumerate(output.tool_calls):
                try:
                    args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                    result = await execute_tool(tc.name, args)
                except Exception as e:
                    result = f"[Error] {type(e).__name__}: {e}"

                _log_tool_result_common(tc.name, result)
                if not get_silent():
                    if tc.name == "send_to_character":
                        # 显示对方角色的完整回复
                        separate_print(title=f"{tc.name} 回复")
                        # 安全打印，避免编码问题
                        try:
                            print(f"  {result}")
                        except UnicodeEncodeError:
                            # 移除无法打印的字符
                            safe = result.encode('ascii', errors='ignore').decode('ascii')
                            print(f"  [内容已简化] {safe[:500]}")
                    elif tc.name == "shice_schedule_add":
                        # 时策注册结果简要显示
                        print(f"  [OK] {tc.name}: {result[:200]}")
                    else:
                        print(f"\n  [OK] {tc.name}:\n{result[:300]}{'...' if len(result) > 300 else ''}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": f"call_{i}_{idx}",
                    "name": tc.name,
                    "content": result,
                })

                # 每次工具执行后：触发 history 保存回调
                if on_history_save:
                    on_history_save()

                # send_to_character 后注入独立提示：防止 LLM 在文本回复中直接跟角色对话
                if tc.name == "send_to_character":
                    messages.append({
                        "role": "user",
                        "content": (
                            "[系统] send_to_character 已完成。"
                            "对方角色的回复在上方 tool result 中。"
                            "⚠️ 对方无法看到你的普通回复文本。"
                            "如需继续对话 → 调用 send_to_character。"
                            "如需向用户汇报 → 直接输出回复。"
                        ),
                    })

            # Item 1: 检查模型切换请求（替代 IPUSwitched 异常）
            switch = pop_switch()
            if switch:
                return ChatResult(
                    messages=messages,
                    should_switch=True,
                    switch_provider=switch.provider,
                    switch_ipu=switch.ipu,
                )
        else:
            round_end(i + 1, "no tool calls" if i == 0 else "tool chain done")
            # dump_experience 已移除，统一由 _post_round_async 调用
            return ChatResult(messages=messages)

    max_rounds_reached(MAX_ITER)
    return ChatResult(messages=messages)


# ── 公共日志辅助 ──

def _log_tool_calls_common(tool_calls: list):
    calls = []
    for tc in tool_calls:
        try:
            args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
        except Exception:
            args_str = tc.arguments[:80] if isinstance(tc.arguments, str) else str(tc.arguments)[:80]
        calls.append(f"{tc.name}({args_str})")
    logger.info(f"    [TOOL] 工具调用 | {len(tool_calls)} 个: {'; '.join(calls)}")


def _log_tool_result_common(tool_name: str, result: str):
    """日志记录工具结果。send_to_character 特殊处理：展示完整错误行。"""
    if tool_name == "send_to_character":
        # 取前两行（标题行 + 内容/错误行），截断防过长
        lines = result.split("\n")
        brief = "\n".join(lines[:2])
        if len(brief) > 300:
            brief = brief[:300] + "..."
        logger.info(f"    [RESULT] 工具结果 | {tool_name} →\n{brief}")
    else:
        first_line = result.split("\n")[0]
        if len(first_line) > 120:
            first_line = first_line[:120] + "..."
        logger.info(f"    [RESULT] 工具结果 | {tool_name} → {first_line}")


# ── experience.md 快照 ──

def _choose_fence(text: str) -> str:
    """选择足够长的代码块 fence，确保不与内容中的反引号序列冲突。"""
    max_run = 0
    for m in re.finditer(r"`+", text):
        run_len = len(m.group())
        if run_len > max_run:
            max_run = run_len
    n = max_run + 1 if max_run >= 3 else 3
    return "`" * max(3, n)


def _flatten(msg: dict) -> str:
    """将一条消息转为可读文本。"""
    content = msg.get("content")
    parts: list[str] = []

    # 推理内容不展示
    if msg.get("role") == "assistant" and msg.get("reasoning_content"):
        pass

    if isinstance(content, list):
        for item in content:
            if item.get("type") == "image_url":
                url = item.get("image_url", {}).get("url", "")
                tag = f"[image: {url[:60]}...]" if len(url) > 60 else f"[image: {url}]"
                parts.append(tag)
            else:
                parts.append(item.get("text", ""))
    elif content:
        # 仅对 user 消息剥离 form_full_context 包裹
        if msg.get("role") == "user":
            from common.context import strip_context_wrapper
            clean = strip_context_wrapper(str(content))
            parts.append(clean)
        else:
            parts.append(str(content))

    if msg.get("role") == "tool":
        tc_name = msg.get("name", "")
        if tc_name:
            parts.insert(0, f"[tool_call: {tc_name}]")

    if tool_calls := msg.get("tool_calls"):
        tc_lines = ["[tool_calls]"]
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "?")
            args = fn.get("arguments", "")
            if isinstance(args, str) and len(args) > 120:
                args = args[:120] + "..."
            elif isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            tc_lines.append(f"  {name}({args})")
        parts.append("\n".join(tc_lines))

    return "\n".join(parts)


def _render_messages_as_dialogue(msgs: list[dict]) -> str:
    """将消息列表渲染为对话格式（不含 ## 标题）。

    用于从 messages 列表中提取历史消息，不依赖 message2 内容。
    """
    if not msgs:
        return ""
    lines: list[str] = []
    for m in msgs:
        role = m.get("role", "unknown")
        # 跳过 system 消息
        if role == "system":
            continue
        # 跳过系统提示消息
        content = m.get("content", "")
        if isinstance(content, str) and content.startswith("[系统]"):
            continue

        text = _flatten(m)
        fence = _choose_fence(text)
        msg_time = m.get("time", "")
        time_str = msg_time[:19] if msg_time else time.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"### [{time_str}] {role}:\n\n{fence}text\n{text}\n{fence}")
    return "\n\n".join(lines)


# ── experience.md 快照 ────────────────────────────────────────────────────────

def dump_experience(character_name: str, messages: list[dict] | None = None,
                   round_context: str | None = None,
                   round_usage: dict | None = None):
    """增量追加对话历史到 experience.md。

    始终从磁盘读取 history.json 获取最新消息列表。
    用 _dump_meta.json 的 written_len（消息数）作为计数器，不依赖条目数。
    round_context: 当前轮次的状态（上轮消耗/累计消耗），非空时写入 message1。
    round_usage: 当前轮次的 usage，累加到 _dump_meta.json 的累计字段（持久化）。
    """
    import json, re
    from common.experience_core import update_experience, load_experience, _write_experience_file
    from character import get_character_dir, get_history_path
    from character.history import History
    from yinao.ipu_client.ipu_context import _usage_to_icp

    # 始终从磁盘读取最新状态
    hp = str(get_history_path(character_name))
    hist = History(hp).load()
    all_msgs = hist.messages

    # history.json 直接存储对话消息 [user1, assistant1, user2, assistant2, ...]
    dialogue_msgs = all_msgs

    # 读取 _dump_meta.json 的 written_len（对话消息计数）
    meta_path = get_character_dir(character_name) / "_dump_meta.json"
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    current_written = meta.get("written_len", 0)

    # 累加本轮 usage 到 _dump_meta.json 的累计字段（持久化，跨重启累计）
    if round_usage:
        icp = _usage_to_icp(round_usage)
        meta["prompt_icp"] = meta.get("prompt_icp", 0) + icp["prompt_icp"]
        meta["completion_icp"] = meta.get("completion_icp", 0) + icp["completion_icp"]
        meta["total_icp"] = meta.get("total_icp", 0) + icp["total_icp"]
        meta["thinking_icp"] = meta.get("thinking_icp", 0) + icp["thinking_icp"]

    # 写入状态区块 (message1) — 与增量消息逻辑独立，先于 early return，
    # 即使本轮无新消息（history 已与 disk 同步），也要把 round_context 持久化。
    # 跳过占位标题（"# 状态" 单独一行，无任何数据），否则会把原占位符覆盖。
    if round_context and round_context.strip() != "# 状态":
        blocks = load_experience(character_name)
        if blocks[1] != round_context:
            blocks[1] = round_context
            path = get_character_dir(character_name) / "experience.md"
            _write_experience_file(path, blocks)

    # 没有新增则跳过增量部分（状态已写）——但仍要落盘累计字段
    if len(dialogue_msgs) <= current_written:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return

    # 只写未写部分
    new_msgs = dialogue_msgs[current_written:]
    if not new_msgs:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return

    update_experience(character_name, "dump", {
        "messages": new_msgs,
        "_meta": meta,
        "character_name": character_name,
        "round_context": round_context,
    })

    # 同步 _dump_meta.json（用消息数，而非条目数）
    meta["written_len"] = len(dialogue_msgs)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
