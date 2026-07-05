"""DeepSeek 智能基元供应商 (OpenAI-compatible)"""
from common.logger import logger
from .common_client_util import (
    IPUConfig,
    reason_action_loop,
    ChatResult,
)


async def reason_action_chat(messages: list[dict], ipu_config=None,
                              character_name: str = "") -> ChatResult:
    if ipu_config is None:
        ipu_config = IPUConfig(ipu="deepseek-chat")

    logger.info(
        f"供应商 deepseek | 智能基元={ipu_config.ipu} | 地址={ipu_config.base_url}"
        f" | 思考={getattr(ipu_config, 'thinking_enabled', True)}"
    )

    # 思考模式
    if getattr(ipu_config, 'thinking_enabled', True):
        ipu_config.extra_body = {"thinking": {"type": "enabled"}}
    else:
        ipu_config.extra_body = {"thinking": {"type": "disabled"}}
    ipu_config.stream = True

    from tool.builtin import tools  # 延迟导入，避免循环依赖
    tool_defs = tools.get_definitions()
    if tool_defs:
        ipu_config.tools = tool_defs
        ipu_config.tool_choice = "auto"

    return await reason_action_loop(
        messages, ipu_config,
        reasoning_field="reasoning_content",
        reasoning_inline=True,
        character_name=character_name,
    )