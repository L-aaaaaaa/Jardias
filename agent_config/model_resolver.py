"""
model_resolver.py — 模型解析（数据源：provider_manager → providers.json5）
"""
import os
from enum import Enum

from data_shape import AIModelProvider, AIModelConfig
from .provider_manager import provider_manager


class Provider(Enum):
    MINIMAX = "minimax"
    DASHSCOPE = "dashscope"
    DEEPSEEK = "deepseek"


SYSTEM_PROMPT = "你是个测试助手"

# ── 从配置文件派生全部模型数据 ──

def _build_all():
    """从 provider_manager 配置构建 MODEL_NAMES、MODEL_CAPABILITIES、providers 三份映射。

    纯函数：config → 三个 dict，不访问全局状态。
    """
    cfg = provider_manager.load()

    model_names: dict[str, dict[str, str]] = {}
    model_caps: dict[str, set[str]] = {}
    provider_map: dict[str, AIModelProvider] = {}

    for prov in cfg.providers:
        model_names[prov.name] = {}
        for short_name, entry in prov.models.items():
            if isinstance(entry, dict):
                model_names[prov.name][short_name] = entry["id"]
                caps = entry.get("caps", [])
            else:
                model_names[prov.name][short_name] = entry
                caps = []
            if caps:
                model_caps[short_name] = set(caps)

        provider_map[prov.name] = AIModelProvider(
            api_key=os.getenv(prov.api_key_env, ""),
            base_url=prov.base_url,
        )

    return model_names, model_caps, provider_map


MODEL_NAMES, MODEL_CAPABILITIES, providers = _build_all()


def get_model_capabilities(provider: str, short_name: str) -> set[str]:
    """获取指定模型的能力标签集合"""
    try:
        _ = MODEL_NAMES[provider][short_name]
    except KeyError:
        return set()
    return MODEL_CAPABILITIES.get(short_name, set())


def choose_model(provider_name: str, model_name: str) -> str:
    provider_models = MODEL_NAMES.get(provider_name, {})
    if model_name not in provider_models:
        available = ", ".join(provider_models.keys()) if provider_models else "(无模型)"
        raise KeyError(f"模型 '{model_name}' 在供应商 {provider_name} 下不存在。{provider_name} 可用模型: {available}")
    return provider_models[model_name]


def choose_provider(provider_name: str) -> AIModelProvider:
    if provider_name not in providers:
        available = ", ".join(providers.keys())
        raise KeyError(f"供应商 '{provider_name}' 不存在。可用供应商: {available}")
    return providers[provider_name]


def resolve_model(provider_name: str, model_name: str) -> tuple[AIModelProvider, AIModelConfig]:
    provider = choose_provider(provider_name)
    model_id = choose_model(provider_name, model_name)
    return provider, AIModelConfig(
        model=model_id,
        base_url=provider.base_url,
        api_key=provider.api_key,
    )
