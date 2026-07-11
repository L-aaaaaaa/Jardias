"""MiniMax 智能基元供应商 (OpenAI-compatible)"""
from __future__ import annotations

from common.logger import logger
from .common_client_util import (
    IPUConfig,
    reason_action_loop,
    ChatResult,
)


def _is_m3(ipu_config) -> bool:
    """判断当前智能基元是否为 MiniMax-M3。"""
    return bool(ipu_config and ipu_config.ipu and "MiniMax-M3" in ipu_config.ipu)


async def reason_action_chat(messages: list[dict], ipu_config=None,
                              character_name: str = "",
                              on_history_save: callable | None = None) -> ChatResult:
    if ipu_config is None:
        ipu_config = IPUConfig()
    logger.info(f"供应商 minimax | 智能基元={ipu_config.ipu} | 地址={ipu_config.base_url}")

    # ── MiniMax M3 专用参数 ──
    if _is_m3(ipu_config):
        # reasoning_split: 将思考内容分离到 reasoning_details 字段
        if not ipu_config.extra_body:
            ipu_config.extra_body = {}
        ipu_config.extra_body["reasoning_split"] = True
        # thinking 控制: M3 可关闭思考，M2.x 不可关闭
        if not getattr(ipu_config, "thinking_enabled", True):
            ipu_config.extra_body["thinking"] = {"type": "disabled"}

    from tool.builtin import tools  # 延迟导入，避免循环依赖
    tool_defs = tools.get_definitions()
    if tool_defs:
        ipu_config.tools = tool_defs
        ipu_config.tool_choice = "auto"

    return await reason_action_loop(messages, ipu_config,
                                     reasoning_field="reasoning_details",
                                     character_name=character_name,
                                     on_history_save=on_history_save)