"""MiniMax 供应商 (OpenAI-compatible)"""
from common.logger import logger
from .common_client_util import (
    AIModelConfig,
    reason_action_loop,
    ChatResult,
)


def _is_m3(model_config) -> bool:
    """判断当前模型是否为 MiniMax-M3。"""
    return bool(model_config and model_config.model and "MiniMax-M3" in model_config.model)


async def reason_action_chat(messages: list[dict], model_config=None,
                              character_name: str = "") -> ChatResult:
    if model_config is None:
        model_config = AIModelConfig()
    logger.info(f"供应商 minimax | 模型={model_config.model} | 地址={model_config.base_url}")

    # ── MiniMax M3 专用参数 ──
    if _is_m3(model_config):
        # reasoning_split: 将思考内容分离到 reasoning_details 字段
        if not model_config.extra_body:
            model_config.extra_body = {}
        model_config.extra_body["reasoning_split"] = True
        # thinking 控制: M3 可关闭思考，M2.x 不可关闭
        if not getattr(model_config, "thinking_enabled", True):
            model_config.extra_body["thinking"] = {"type": "disabled"}

    from tool.builtin import tools  # 延迟导入，避免循环依赖
    tool_defs = tools.get_definitions()
    if tool_defs:
        model_config.tools = tool_defs
        model_config.tool_choice = "auto"

    return await reason_action_loop(messages, model_config,
                                     reasoning_field="reasoning_details",
                                     character_name=character_name)
