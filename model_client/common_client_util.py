import json
import os
import re
import time

from openai import OpenAI

from data_shape import AIModelProvider, AIModelConfig, ToolCall, RoundOutput, ChatResult
from common.logger import logger
from common.utils import separate_print, stream_print, set_display_name, get_silent
from common.agent_log import round_start, round_end, max_rounds_reached, format_api_ok
from .model_context import set_round_meta, pop_switch
from character import get_character_dir


# ————————————————————————————————————————————————————————
#  Client
# ————————————————————————————————————————————————————————


def form_client(provider: AIModelProvider | None = None):
    if provider is None:
        provider = AIModelProvider()
    return OpenAI(api_key=provider.api_key, base_url=provider.base_url)


def single_completion(
    client: OpenAI,
    model: str,
    messages: list[dict],
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> str:
    """非流式单次 API 调用，返回纯文本（@llm_tool 用）。"""
    response = client.chat.completions.create(
        messages=messages,
        model=model,
        temperature=temperature,
        max_completion_tokens=max_tokens,
    )
    return response.choices[0].message.content


def form_stream(full_context_list: list, client=None, model_config=None):
    if model_config is None:
        model_config = AIModelConfig()
    if client is None:
        client = OpenAI(api_key=model_config.api_key, base_url=model_config.base_url)

    return client.chat.completions.create(
        messages=[{k: v for k, v in m.items() if k != "_reasoning"} for m in full_context_list],
        model=model_config.model,
        extra_body=model_config.extra_body,
        stream=model_config.stream,
        stream_options=getattr(model_config, "stream_options", None) or None,
        temperature=model_config.temperature,
        top_p=model_config.top_p,
        max_completion_tokens=model_config.max_completion_tokens,
        tools=model_config.tools if model_config.tools else None,
        tool_choice=model_config.tool_choice if model_config.tool_choice else None,
        reasoning_effort=model_config.reasoning_effort,
    )


# ————————————————————————————————————————————————————————
#  输出工具 (委托给 utils.py)
# ————————————————————————————————————————————————————————


# ————————————————————————————————————————————————————————
#  流式响应收集
# ── 流式响应收集 ──


def collect_round(stream, reasoning_field: str = "reasoning_details") -> RoundOutput:
    """
    消费流式响应，边接收边流式输出，同时返回结构化结果。
    reasoning_field: "reasoning_details" (MiniMax) | "reasoning_content" (DeepSeek/DashScope)
    """
    reasoning_parts, content_parts, fc_names, fc_args_parts, deltas = [], [], [], [], []
    _think_parts: list[str] = []  # <think> 内容独立存储，不混入 reasoning_parts
    _printed_reasoning_len = 0  # 诊断：实际 stream_print 输出的字符数
    reasoning_header = False
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
            separate_print(title="推理过程")

    for chunk in stream:
        # 尾 chunk 可能不带 choices（仅含 usage），跳过
        if not getattr(chunk, "choices", None):
            if hasattr(chunk, "usage") and chunk.usage:
                usage = chunk.usage.model_dump() if hasattr(chunk.usage, "model_dump") else None
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
                            separate_print(title="回复")
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
                            separate_print(title="回复")
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


def stream_chat(full_context_list: list[dict[str, str]], model_config=None):
    stream = form_stream(full_context_list, model_config=model_config)
    state = StreamState()
    for chunk in stream:
        if not getattr(chunk, "choices", None):
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
    model_config,
    reasoning_field: str = "reasoning_details",
    reasoning_inline: bool = False,
    character_name: str = "",
):
    """公共单轮执行：流式请求 + 响应收集 + assistant 消息组装。

    reasoning_field: "reasoning_details" (MiniMax) | "reasoning_content" (DeepSeek/DashScope)
    reasoning_inline: True → reasoning 嵌入 assistant 消息 (DeepSeek)
                     False → reasoning 作为独立消息 (MiniMax/DashScope)
    character_name: 角色名，非空时每次 API 调用前写入 context_latest.md
    """
    round_start(iteration + 1, len(messages))

    # 上下文拦截器：每轮 API 调用前写入完整上下文快照
    if character_name:
        dump_context(character_name, messages)

    stream = form_stream(messages, model_config=model_config)
    t0 = time.time()
    output = collect_round(stream, reasoning_field=reasoning_field)
    print()  # 流式输出收尾换行
    if output.content.strip():
        separate_print(end=True)
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
    return output, messages


async def reason_action_loop(
    messages: list[dict],
    model_config,
    reasoning_field: str = "reasoning_details",
    reasoning_inline: bool = False,
    character_name: str = "",
) -> ChatResult:
    """公共 Reason-Action 循环：多轮工具调用，直到模型给出最终回复。

    返回 ChatResult — 用 should_switch 替代 ModelSwitched 异常。
    character_name 非空时，每轮 API 调用前写入 context_latest.md。
    """
    if character_name:
        set_display_name(character_name)
    last_content = ""

    for i in range(MAX_ITER):
        output, messages = await _run_common_round(
            messages, i, model_config,
            reasoning_field=reasoning_field,
            reasoning_inline=reasoning_inline,
            character_name=character_name,
        )
        last_content = output.content

        if output.tool_calls:
            _log_tool_calls_common(output.tool_calls)
            for idx, tc in enumerate(output.tool_calls):
                try:
                    args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
                    result = await execute_tool(tc.name, args)
                except Exception as e:
                    result = f"[Error] {type(e).__name__}: {e}"

                _log_tool_result_common(tc.name, result)
                if not get_silent() and tc.name != "send_to_character":
                    print(f"\n  [OK] {tc.name}:\n{result[:300]}{'...' if len(result) > 300 else ''}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": f"call_{i}_{idx}",
                    "name": tc.name,
                    "content": result,
                })

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

            # Item 1: 检查模型切换请求（替代 ModelSwitched 异常）
            switch = pop_switch()
            if switch:
                if character_name:
                    dump_context(character_name, messages)
                return ChatResult(
                    messages=messages,
                    should_switch=True,
                    switch_provider=switch.provider,
                    switch_model=switch.model,
                )
        else:
            round_end(i + 1, "no tool calls" if i == 0 else "tool chain done")
            if character_name:
                dump_context(character_name, messages)
            return ChatResult(messages=messages)

    max_rounds_reached(MAX_ITER)
    if character_name:
        dump_context(character_name, messages)
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


# ── context_latest.md 快照 ──

def _choose_fence(text: str) -> str:
    """选择足够长的代码块 fence，确保不与内容中的反引号序列冲突。"""
    max_run = 0
    for m in re.finditer(r"`+", text):
        run_len = len(m.group())
        if run_len > max_run:
            max_run = run_len
    n = max_run + 1 if max_run >= 3 else 3
    return "`" * max(3, n)


def dump_context(character_name: str, messages: list[dict]):
    """将本轮上下文写入 context_latest.md —— 4 层固定结构 + 动态归位。

    结构：
      message0 = 系统提示词（固定）
      message1 = 状态（固定）
      message2 = 历史（摘要 + 近期对话原文 + 本轮对话）
      message3 = 本次用户消息（助手未回复时）→ 助手开始回复后归入 message2
    """
    path = get_character_dir(character_name) / "context_latest.md"
    blocks: list[str] = []

    def _flatten(msg: dict) -> str:
        """将一条消息转为可读文本，含 reasoning_content + content + tool_calls + tool_call_id。"""
        content = msg.get("content")
        parts: list[str] = []

        # 推理/思考内容（assistant 消息的 reasoning_content）
        if msg.get("role") == "assistant" and msg.get("reasoning_content"):
            rc = msg["reasoning_content"]
            parts.append(f"[思考]\n{rc}")

        if isinstance(content, list):
            for item in content:
                if item.get("type") == "image_url":
                    url = item.get("image_url", {}).get("url", "")
                    tag = f"[image: {url[:60]}...]" if len(url) > 60 else f"[image: {url}]"
                    parts.append(tag)
                else:
                    parts.append(item.get("text", ""))
        elif content:
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

    # ── message0-2: 固定的三层结构 ──
    structural_msgs = messages[:3]  # system, 状态, 历史
    for i, m in enumerate(structural_msgs):
        content = _flatten(m)
        blocks.append(f"<!-- message{i} -->\n\n{content}")

    # ── message3 + 本轮对话 ──
    round_msgs = messages[3:]  # 用户输入 + 可能的助手/tool 链
    if not round_msgs:
        # 不应该到达这里
        blocks.append("<!-- message3 -->\n\n（无用户消息）")
    elif len(round_msgs) == 1:
        # 仅用户消息，助手尚未回复 → 保留 message3
        blocks.append(f"<!-- message3 -->\n\n{_flatten(round_msgs[0])}")
    else:
        # 助手已开始回复 → 整个本轮对话归入 message2 的"近期对话原文"
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        round_lines: list[str] = []
        for i, m in enumerate(round_msgs):
            role = m.get("role", "unknown")
            # MiniMax：独立推理消息的内容合并到下一条 assistant，不单独成块
            if m.get("_reasoning") and role == "assistant":
                continue
            text = _flatten(m)
            # 前一条若是独立推理消息，将其内容以 [思考] 前缀合并到本条
            if i > 0 and round_msgs[i - 1].get("_reasoning"):
                prev_reasoning = round_msgs[i - 1].get("content", "")
                text = f"[思考]\n{prev_reasoning}\n\n{text}"
            fence = _choose_fence(text)
            round_lines.append(f"### [{now}] {role}:\n\n{fence}text\n{text}\n{fence}")

        # 拆开 message2，在"近期对话原文"末尾插入本轮对话
        m2_content = _flatten(messages[2])
        if "## 近期对话原文" in m2_content:
            idx = m2_content.rfind("## 近期对话原文")
            # 保留前面的 ## 摘要等部分，只在"近期对话原文"末尾追加本轮对话
            section_body = m2_content[idx:].rstrip()
            new_history = m2_content[:idx].rstrip() + "\n\n" + section_body + "\n\n" + "\n\n".join(round_lines)
        else:
            new_history = m2_content + "\n\n## 近期对话原文\n\n" + "\n\n".join(round_lines)

        blocks[2] = f"<!-- message2 -->\n\n{new_history}"
        # message3: 等待下一轮
        blocks.append("<!-- message3 -->\n\n## 本次用户消息\n\n（等待下一轮用户输入）")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocks) + "\n")
        f.flush()
        os.fsync(f.fileno())
