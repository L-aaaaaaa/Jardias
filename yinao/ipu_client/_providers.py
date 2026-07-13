"""
_providers.py — 供应商工厂与注册表

从 common_client_util.py 拆出（第一梯队）。
职责：
- ProviderSpec：供应商差异配置
- _apply_thinking / _inject_tools：根据 spec 调整 IPUConfig
- make_provider_chat：生成供应商 reason_action_chat 协程
- PROVIDER_CHAT：模块加载时自动注册三个供应商（dashscope/deepseek/minimax）

依赖：
- reason_act_loop（来自 common_client_util.py 暂时，未来也可拆）
- IPUConfig / ChatResult / logger
"""
from __future__ import annotations

from dataclasses import dataclass, field

from common.logger import logger
from data_shape import IPUConfig, ChatResult


@dataclass
class ProviderSpec:
    """供应商差异配置：每个供应商只需构造一个实例即可生成 reason_action_chat。"""
    name: str = ""  # 供应商名（日志用）
    ipu_default: str = "MiniMax-M2.7"  # ipu_config 为 None 时的默认 ipu
    thinking_mode: str = "disable"  # "enable" | "disable" | "toggle" | "m3"
    stream_opts: dict = field(default_factory=dict)
    reasoning_field: str = "reasoning_details"
    reasoning_inline: bool = False
    extra_body_overrides: dict = field(default_factory=dict)


def _apply_thinking(ipu_config: IPUConfig, spec: ProviderSpec):
    """根据 ProviderSpec.thinking_mode 设置 extra_body。"""
    if not ipu_config.extra_body:  ipu_config.extra_body = {}
    if spec.thinking_mode == "enable":
        ipu_config.extra_body["enable_thinking"] = True
    elif spec.thinking_mode == "toggle":
        ipu_config.extra_body["thinking"] = {
            "type": "enabled" if getattr(ipu_config, "thinking_enabled", True) else "disabled"}
    elif spec.thinking_mode == "m3":
        ipu_config.extra_body["reasoning_split"] = True
        if not getattr(ipu_config, "thinking_enabled", True):
            ipu_config.extra_body["thinking"] = {"type": "disabled"}
    # "disable" — 不设置任何 thinking 相关参数
    ipu_config.extra_body.update(spec.extra_body_overrides)


def _inject_tools(ipu_config: IPUConfig):
    """延迟导入 tool 定义并注入 ipu_config。"""
    from tool.builtin import tools  # 延迟导入，避免循环依赖
    tool_defs = tools.get_definitions()
    if tool_defs:
        ipu_config.tools = tool_defs
        ipu_config.tool_choice = "auto"


def make_provider_chat(spec: ProviderSpec):
    """生成供应商的 reason_action_chat 协程函数。"""

    async def reason_action_chat(
            messages: list[dict], ipu_config=None, character_name: str = "",
            on_history_save: callable | None = None, ) -> ChatResult:
        # 延迟到闭包内导入 reason_act_loop，避免 _providers 模块加载时循环依赖
        from .common_client_util import reason_act_loop
        if ipu_config is None: ipu_config = IPUConfig(ipu=spec.ipu_default)
        logger.info(
            f"供应商 {spec.name} | 智能基元={ipu_config.ipu} | 地址={ipu_config.base_url}")
        _apply_thinking(ipu_config, spec)
        for k, v in spec.stream_opts.items(): setattr(ipu_config, k, v)
        _inject_tools(ipu_config)
        return await reason_act_loop(
            messages, ipu_config,
            reasoning_field=spec.reasoning_field,
            reasoning_inline=spec.reasoning_inline,
            character_name=character_name,
            on_history_save=on_history_save,
        )

    return reason_action_chat


# ── 供应商注册表（模块加载时自动注册） ──────────────────────────

PROVIDER_CHAT: dict[str, callable] = {}

PROVIDER_CHAT["dashscope"] = make_provider_chat(ProviderSpec(
    name="dashscope",
    thinking_mode="enable",
    stream_opts={"include_usage": True},
    reasoning_field="reasoning_content",
))

PROVIDER_CHAT["deepseek"] = make_provider_chat(ProviderSpec(
    name="deepseek",
    ipu_default="v4-pro",
    thinking_mode="toggle",
    stream_opts={"stream": True},
    reasoning_field="reasoning_content",
    reasoning_inline=True,
))

PROVIDER_CHAT["minimax"] = make_provider_chat(ProviderSpec(
    name="minimax",
    thinking_mode="m3",
    reasoning_field="reasoning_details",
))
