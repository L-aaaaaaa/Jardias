"""
智能基元切换逻辑。
供应商注册表与统一对话生成入口
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field

from character.config_io import load_config
from common.logger import logger
from data_shape import IPUConfig, ChatResult
from yinao import resolve_ipu, IPU_REGISTRY, get_ipu_capabilities


@dataclass
class ProviderSpec:
    """供应商差异配置：每个供应商只需构造一个实例。"""
    name: str = ""  # 供应商名（日志用）
    ipu_default: str = "MiniMax-M2.7"  # ipu_config 为 None 时的默认 ipu
    thinking_mode: str = "disable"  # "enable" | "disable" | "toggle" | "m3"
    stream_opts: dict = field(default_factory=dict)
    reasoning_field: str = "reasoning_details"
    reasoning_inline: bool = False
    extra_body_overrides: dict = field(default_factory=dict)


def _apply_thinking(ipu_config: IPUConfig, spec: ProviderSpec):
    """根据 ProviderSpec.thinking_mode 设置 extra_body。"""
    if not ipu_config.extra_body: ipu_config.extra_body = {}
    enabled = getattr(ipu_config, "thinking_enabled", True)
    mode_map = {
        "enable": {"enable_thinking": True}, "disable": {},
        "m3": {"reasoning_split": True, **({"thinking": {"type": "disabled"}} if not enabled else {})},
        "toggle": {"thinking": {"type": "enabled" if enabled else "disabled"}}, }
    ipu_config.extra_body.update(mode_map.get(spec.thinking_mode, {}))
    ipu_config.extra_body.update(spec.extra_body_overrides)


def _inject_tools(ipu_config: IPUConfig):
    """延迟导入 tool 定义并注入 ipu_config。"""
    from tool.builtin import tools  # 延迟导入，避免循环依赖
    tool_defs = tools.get_definitions()
    if tool_defs:
        ipu_config.tools = tool_defs
        ipu_config.tool_choice = "auto"


async def reason_action_chat(
        provider: str, messages: list[dict], ipu_config: IPUConfig | None = None,
        character_name: str = "", on_history_save: callable | None = None, ) -> ChatResult:
    """统一 chat 入口：从 PROVIDER_SPECS 取 spec，按供应商差异调 weave_thought。

    调用方应通过 switch.resolve_chat(provider) 拿到预绑定 provider 的 4 参协程；
    直接调用本函数时第一参数必传 provider。
    """
    # 延迟导入 weave_thought，避免 _providers 模块加载时循环依赖
    from .thought_weaver import weave_thought

    spec = PROVIDER_SPECS[provider]
    if ipu_config is None: ipu_config = IPUConfig(ipu=spec.ipu_default)
    logger.info(f"供应商 {spec.name} | 智能基元={ipu_config.ipu} | 地址={ipu_config.base_url}")
    _apply_thinking(ipu_config, spec)
    for k, v in spec.stream_opts.items(): setattr(ipu_config, k, v)
    _inject_tools(ipu_config)
    return await weave_thought(
        messages, ipu_config, reasoning_field=spec.reasoning_field, reasoning_inline=spec.reasoning_inline,
        character_name=character_name, on_history_save=on_history_save, )


# ── 供应商注册表（模块加载时自动注册） ──────────────────────────

PROVIDER_SPECS: dict[str, ProviderSpec] = {}

PROVIDER_SPECS["dashscope"] = ProviderSpec(
    name="dashscope",
    thinking_mode="enable",
    stream_opts={"include_usage": True},
    reasoning_field="reasoning_content",
)

PROVIDER_SPECS["deepseek"] = ProviderSpec(
    name="deepseek",
    ipu_default="v4-pro",
    thinking_mode="toggle",
    stream_opts={"stream": True},
    reasoning_field="reasoning_content",
    reasoning_inline=True,
)

PROVIDER_SPECS["minimax"] = ProviderSpec(
    name="minimax",
    thinking_mode="m3",
    reasoning_field="reasoning_details",
)


def _bind(provider: str):
    """把 provider 绑到 reason_action_chat 第一参数，返回 4 参协程。

    lifecycle.py / characters.py 等调用点拿到的是这个函数，
    它们的签名仍是 (messages, ipu_config, character_name, on_history_save)。
    """

    @functools.wraps(reason_action_chat)
    async def bound(messages: list[dict], ipu_config=None, character_name: str = "",
            on_history_save=None):
        return await reason_action_chat(provider, messages, ipu_config,
            character_name=character_name, on_history_save=on_history_save, )

    bound.__name__ = f"{provider}_chat"
    return bound


# 代理字典（测试可以直接 monkeypatch.setitem 替换为 _fake_chat）
_CHAT_FNS: dict[str, callable] = {p: _bind(p) for p in PROVIDER_SPECS}


def resolve_chat(provider: str):
    return _CHAT_FNS.get(provider, _CHAT_FNS["minimax"])


def sync_config_to_ipu(config, ipu_config):
    """把 ActorConfig.runtime 同步到 IPUConfig 调用参数。"""
    ipu_config.temperature = config.runtime.temperature
    ipu_config.top_p = config.runtime.top_p
    ipu_config.max_icp = config.runtime.max_icp
    ipu_config.reasoning_effort = config.runtime.reasoning_effort
    ipu_config.thinking_enabled = config.runtime.thinking_enabled


def reload_after_switch(ctx):
    """切换后重建 ctx 的 IPU 相关字段。"""
    ctx.config = load_config(ctx.character_name, config_dir=ctx.config_dir)
    ctx.provider = ctx.config.runtime.provider
    ctx.ipu = ctx.config.runtime.ipu
    ctx.chat_fn = resolve_chat(ctx.provider)
    _prov, ctx.ipu_config = resolve_ipu(ctx.provider, ctx.ipu)
    sync_config_to_ipu(ctx.config, ctx.ipu_config)


def inform_ipu_switch(old_prov: str, old_ipu: str, new_prov: str, new_ipu: str,
        old_full: str, new_full: str, reason: str = "") -> str:
    """智能基元切换通知（用于塞入当前 messages，给本轮 LLM 看）"""
    note = (
        f"[智能基元切换通知] "
        f"你的运行智能基元（LLM/其他思考引擎）已从 **{old_full}** ({old_prov}/{old_ipu}) "
        f"切换为 **{new_full}** ({new_prov}/{new_ipu})。")
    if reason:
        note += f" 切换原因: {reason}。"
    note += (
        " 智能基元是计算底座，你的身份（由下方 system prompt 定义）没有改变。"
        " 切换已完成，无需再次调用 update_runtime。"
        " 历史中来自旧智能基元的消息如果提到了不同的智能基元身份，请忽略——"
        " 以本消息和下方 system prompt 为准。")
    return note


def format_engine_switch_log(old_prov: str, old_ipu: str, new_prov: str, new_ipu: str,
        old_full: str, new_full: str, reason: str = "") -> str:
    """智能基元切换的持久化日志格式（写入 history.json / experience.md 近期对话原文）。

    区别于 inform_ipu_switch 的"提醒式长段落"——这一版是事件流水，
    短小精炼，专注于"何时发生了引擎变更"。
    以前缀 [智能基元切换] 开头，被 _render_single_message 识别渲染。
    """
    return (f"[智能基元切换] 引擎从 {old_full} ({old_prov}/{old_ipu}) "
            f"切换为 {new_full} ({new_prov}/{new_ipu})。"
            + (f" 原因: {reason}。" if reason else ""))


def next_provider(current: str, tried: set) -> str | None:
    """返回下一个未尝试的供应商名，没有则返回 None。"""
    for p in IPU_REGISTRY:
        if p not in tried: return p
    return None


def pick_fallback_ipu(provider: str, vision_first: bool = False) -> str:
    """返回供应商第一个智能基元简称。vision_first=True 时优先选有视觉的。"""
    ipus = IPU_REGISTRY.get(provider, {})
    if not ipus:
        raise ValueError(f"Provider {provider} 下无智能基元")
    if vision_first:
        for short_name in ipus:
            if "vision" in get_ipu_capabilities(provider, short_name): return short_name
    return next(iter(ipus))


def next_vision_provider(current: str, tried: set) -> str | None:
    """返回下一个未尝试且有视觉的智能基元供应商。"""
    for p in IPU_REGISTRY:
        if p in tried: continue
        for short_name in IPU_REGISTRY[p]:
            if "vision" in get_ipu_capabilities(p, short_name): return p
    return None


# ── 智能基元切换的进程内共享状态 ─────────────────────────────
# 由 request_switch / pop_switch 在 thought_weaver 与 app.py 之间接力；
# 由 set_active_ipu / get_active_ipu 暴露"fallback 后实际跑的那个"。

from data_shape import IPUSwitch


class IPUSwitched(Exception):
    """切换智能基元时抛出，携带 (provider, ipu) 供外层捕获并重建 client。"""

    def __init__(self, provider: str, ipu: str):
        self.provider = provider
        self.ipu = ipu
        super().__init__(f"switch to {provider}/{ipu}")


switch_request: IPUSwitch | None = None

# 实际运行中的 provider/ipu（fallback 后可能与 config 文件不同）
_actual_provider: str = ""
_actual_ipu: str = ""


def set_active_ipu(provider: str, ipu: str):
    """记录当前实际运行的智能基元（fallback/bootstrap 调用）。"""
    global _actual_provider, _actual_ipu
    _actual_provider = provider
    _actual_ipu = ipu


def get_active_ipu() -> str:
    """获取当前实际运行的智能基元简称。"""
    return _actual_ipu


def request_switch(provider: str, ipu: str):
    """请求切换智能基元（写入共享状态，由 pop_switch 取出）。"""
    global switch_request
    if provider not in IPU_REGISTRY:
        raise ValueError(f"未知供应商: {provider}，可用: {list(IPU_REGISTRY.keys())}")
    available = list(IPU_REGISTRY[provider].keys())
    if ipu not in IPU_REGISTRY[provider]:
        raise ValueError(f"未知智能基元: {ipu}，{provider} 可用: {available}")
    switch_request = IPUSwitch(provider=provider, ipu=ipu)


def pop_switch() -> IPUSwitch | None:
    """读取并清除切换请求。"""
    global switch_request
    req = switch_request
    switch_request = None
    return req
