# """
# _stream_chat.py — 纯流式（无 Reason-Act 循环）
#
# 从 common_client_util.py 拆出（第一梯队）。
# 职责：
# - StreamState：流式累计状态
# - handle_reasoning / handle_tool_calls / handle_content：分类消费 delta
# - stream_chat：单轮流式输出，仅返回累计 content
#
# 和 collect_round 的区别：
# - collect_round 解析为结构化 RoundOutput（供 Reason-Act 用）
# - 本模块只打印 + 累计 content（供"无 Reason-Act"模式用）
# """
# from __future__ import annotations
#
# from common.utils import separate_print, stream_print
# from ._client import get_ipu_stream_reply
#
#
# class StreamState:
#     def __init__(self):
#         self.accumulated_thought = ""
#         self.accumulated_content = ""
#         self.is_thinking = False
#         self.content_started = False
#
#
# def handle_reasoning(delta, state: StreamState):
#     rc = getattr(delta, "reasoning_content", None)
#     if rc:
#         if not state.is_thinking:
#             state.is_thinking = True
#             separate_print(title="推理过程")
#         state.accumulated_thought += rc
#         stream_print(rc)
#     if hasattr(delta, "reasoning_details") and delta.reasoning_details:
#         if not state.is_thinking:
#             state.is_thinking = True
#             separate_print(title="推理过程")
#         for detail in delta.reasoning_details:
#             text = detail.get("text", detail.get("content", "")) if isinstance(detail, dict) else str(detail)
#             state.accumulated_thought += text
#             stream_print(text)
#
#
# def handle_tool_calls(delta, state: StreamState):
#     tc_list = getattr(delta, "tool_calls", None)
#     if not tc_list:
#         return
#     for tc_d in tc_list:
#         if not state.content_started:
#             state.content_started = True
#             separate_print(title="工具调用")
#         fname = getattr(tc_d.function, "name", "") or ""
#         fargs = getattr(tc_d.function, "arguments", "") or ""
#         print(f"  >> 工具调用: {fname}")
#         print(f"     参数: {fargs}")
#
#
# def handle_content(delta, state: StreamState):
#     if not delta.content:
#         return
#     if not state.content_started:
#         state.content_started = True
#         if not state.is_thinking:
#             separate_print(title="无推理")
#         separate_print(title="回复")
#     state.accumulated_content += delta.content
#     stream_print(delta.content)
#
#
# def stream_chat(full_context_list: list[dict[str, str]], ipu_config=None):
#     stream = get_ipu_stream_reply(full_context_list, ipu_config=ipu_config)
#     state = StreamState()
#     for chunk in stream:
#         if not getattr(chunk, "choices", None) or not chunk.choices:
#             continue
#         delta = chunk.choices[0].delta
#         handle_reasoning(delta, state)
#         handle_tool_calls(delta, state)
#         handle_content(delta, state)
#     return state.accumulated_content
