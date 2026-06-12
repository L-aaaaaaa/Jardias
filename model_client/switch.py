"""switch — 模型切换逻辑。"""
from agent_config import load_config, resolve_model, MODEL_NAMES, get_model_capabilities
from model_client import minimax, deepseek, dashscope

_CHAT_FNS = {
    "deepseek": deepseek.reason_action_chat,
    "dashscope": dashscope.reason_action_chat,
}


def resolve_chat(provider: str):
    return _CHAT_FNS.get(provider, minimax.reason_action_chat)


def sync_config_to_model(config, model_config):
    model_config.temperature = config.runtime.temperature
    model_config.top_p = config.runtime.top_p
    model_config.max_completion_tokens = config.runtime.max_tokens
    model_config.reasoning_effort = config.runtime.reasoning_effort
    model_config.thinking_enabled = config.runtime.thinking_enabled


def reload_after_switch(ctx):
    ctx.config = load_config(ctx.character_name, config_dir=ctx.config_dir)
    ctx.provider = ctx.config.runtime.provider
    ctx.model = ctx.config.runtime.model
    ctx.chat_fn = resolve_chat(ctx.provider)
    _prov, ctx.model_config = resolve_model(ctx.provider, ctx.model)
    sync_config_to_model(ctx.config, ctx.model_config)


def make_switch_note(old_prov: str, old_model: str, new_prov: str, new_model: str,
        old_full: str, new_full: str, reason: str = "") -> str:
    """生成引擎切换通知 — 告知 LLM 引擎已换，身份不变。"""
    note = (
        f"[引擎切换通知] "
        f"你的运行引擎已从 **{old_full}** ({old_prov}/{old_model}) "
        f"切换为 **{new_full}** ({new_prov}/{new_model})。"
    )
    if reason:
        note += f" 切换原因: {reason}。"
    note += (
        " 引擎是计算底座，你的身份（由上方 system prompt 定义）没有改变。"
        " 切换已完成，无需再次调用 update_runtime。"
        " 历史中来自旧引擎的消息如果提到了不同的引擎身份，请忽略——"
        " 以本消息和上方 system prompt 为准。"
    )
    return note


def _next_provider(current: str, tried: set) -> str | None:
    """返回下一个未尝试的 provider 名称，没有则返回 None。"""
    for p in MODEL_NAMES:
        if p not in tried:
            return p
    return None


def _pick_fallback_model(provider: str, vision_first: bool = False) -> str:
    """返回 provider 下第一个模型简称。vision_first=True 时优先选有 vision 能力的。"""
    models = MODEL_NAMES.get(provider, {})
    if not models:
        raise ValueError(f"Provider {provider} 下无模型")
    if vision_first:
        for short_name in models:
            if "vision" in get_model_capabilities(provider, short_name):
                return short_name
    return next(iter(models))


def _next_vision_provider(current: str, tried: set) -> str | None:
    """返回下一个未尝试且有 vision 模型的 provider。"""
    for p in MODEL_NAMES:
        if p in tried:
            continue
        for short_name in MODEL_NAMES[p]:
            if "vision" in get_model_capabilities(p, short_name):
                return p
    return None
