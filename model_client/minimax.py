"""MiniMax 供应商 (OpenAI-compatible)"""
from common.logger import logger
from tool.builtin import tools
from .common_client_util import (
    AIModelConfig,
    reason_action_loop,
    ChatResult,
)


async def reason_action_chat(messages: list[dict], model_config=None,
                              character_name: str = "") -> ChatResult:
    if model_config is None:
        model_config = AIModelConfig()
    logger.info(f"供应商 minimax | 模型={model_config.model} | 地址={model_config.base_url}")
    tool_defs = tools.get_definitions()
    if tool_defs:
        model_config.tools = tool_defs
        model_config.tool_choice = "auto"

    return await reason_action_loop(messages, model_config,
                                     reasoning_field="reasoning_details",
                                     character_name=character_name)
