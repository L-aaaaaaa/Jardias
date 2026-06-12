"""DeepSeek 供应商 (OpenAI-compatible)"""
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
        model_config = AIModelConfig(model="deepseek-chat")

    logger.info(
        f"供应商 deepseek | 模型={model_config.model} | 地址={model_config.base_url}"
        f" | 思考={getattr(model_config, 'thinking_enabled', True)}"
    )

    # 思考模式
    if getattr(model_config, 'thinking_enabled', True):
        model_config.extra_body = {"thinking": {"type": "enabled"}}
    else:
        model_config.extra_body = {"thinking": {"type": "disabled"}}
    model_config.stream = True

    tool_defs = tools.get_definitions()
    if tool_defs:
        model_config.tools = tool_defs
        model_config.tool_choice = "auto"

    return await reason_action_loop(
        messages, model_config,
        reasoning_field="reasoning_content",
        reasoning_inline=True,
        character_name=character_name,
    )
