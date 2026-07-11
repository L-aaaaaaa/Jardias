"""DashScope Coding Plan 智能基元供应商 (OpenAI-compatible)"""
from __future__ import annotations

from common.logger import logger
from .common_client_util import (
    IPUConfig,
    reason_action_loop,
    ChatResult,
)


async def reason_action_chat(messages: list[dict], ipu_config=None,
                              character_name: str = "",
                              on_history_save: callable | None = None) -> ChatResult:
    if ipu_config is None:
        ipu_config = IPUConfig()
    if getattr(ipu_config, "thinking_enabled", None):
        ipu_config.extra_body = {"enable_thinking": True}
    ipu_config.stream_options = {"include_usage": True}  # 获取 ICP 用量

    logger.info(f"供应商 dashscope | 智能基元={ipu_config.ipu} | 地址={ipu_config.base_url}")
    from tool.builtin import tools  # 延迟导入，避免循环依赖
    tool_defs = tools.get_definitions()
    if tool_defs:
        ipu_config.tools = tool_defs
        ipu_config.tool_choice = "auto"

    return await reason_action_loop(messages, ipu_config,
                                     reasoning_field="reasoning_content",
                                     character_name=character_name,
                                     on_history_save=on_history_save)