"""switch — 智能基元切换逻辑。"""
from character.config_io import load_config
from yinao import resolve_ipu, IPU_REGISTRY, get_ipu_capabilities
from ._providers import PROVIDER_CHAT

# 代理字典（测试可以直接 monkeypatch.setitem）
_CHAT_FNS: dict[str, callable] = dict(PROVIDER_CHAT)


def resolve_chat(provider: str):
    return _CHAT_FNS.get(provider, PROVIDER_CHAT["minimax"])


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
    """智能基元切换通知"""
    note = (
        f"[智能基元切换通知] "
        f"你的运行智能基元（LLM/其他思考引擎）已从 **{old_full}** ({old_prov}/{old_ipu}) "
        f"切换为 **{new_full}** ({new_prov}/{new_ipu})。")
    if reason:
        note += f" 切换原因: {reason}。"
    note += (
        " 智能基元是计算底座，你的身份（由上方 system prompt 定义）没有改变。"
        " 切换已完成，无需再次调用 update_runtime。"
        " 历史中来自旧智能基元的消息如果提到了不同的智能基元身份，请忽略——"
        " 以本消息和上方 system prompt 为准。")
    return note


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
