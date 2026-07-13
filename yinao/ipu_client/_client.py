"""
_client.py — OpenAI 客户端与流式请求构造

职责：
- form_client：构造 OpenAI 客户端
- get_ipu_reply：非流式单次调用（@actor_tool 用）
- get_ipu_stream_reply：构造流式请求

无副作用，纯组装。
"""
from __future__ import annotations

from openai import OpenAI

from data_shape import IPUProvider, IPUConfig


def form_client(provider: IPUProvider | None = None):
    if provider is None: provider = IPUProvider()
    return OpenAI(api_key=provider.api_key, base_url=provider.base_url)


def get_ipu_reply(
        client: OpenAI, ipu: str, messages: list[dict], temperature: float = 0.0,
        max_icp: int = 512, ) -> str:
    """非流式单次 API 调用，返回纯文本（@actor_tool 用）。

    API 协议层仍使用 max_completion_tokens / model，IPU 抽象层用 max_icp / ipu。
    """
    response = client.chat.completions.create(
        messages=messages, model=ipu, temperature=temperature,
        max_completion_tokens=max_icp, )
    if not response.choices: return ""
    return response.choices[0].message.content or ""


def get_ipu_stream_reply(full_context_list: list, client=None,
        ipu_config=None):
    """构造流式请求。

    API 协议层使用 OpenAI 兼容字段（model / max_completion_tokens），
    IPUConfig 字段（ipu / max_icp）在调用层映射。
    """
    if ipu_config is None: ipu_config = IPUConfig()
    if client is None: client = OpenAI(api_key=ipu_config.api_key, base_url=ipu_config.base_url)

    return client.chat.completions.create(
        messages=[{k: v for k, v in m.items() if k != "_reasoning"} for m in full_context_list],
        model=ipu_config.ipu,
        extra_body=ipu_config.extra_body,
        stream=ipu_config.stream,
        stream_options={"include_usage": True},  # 流式响应必须显式 include_usage，OpenAI 默认不返回 usage
        temperature=ipu_config.temperature,
        top_p=ipu_config.top_p,
        max_completion_tokens=ipu_config.max_icp,
        tools=ipu_config.tools,
        tool_choice=ipu_config.tool_choice,
        reasoning_effort=ipu_config.reasoning_effort,
    )
