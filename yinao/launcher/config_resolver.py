"""
config_resolver.py — 智能基元（IPU）解析（数据源：ipu_config_manager → ipu_config.json）

功能：
- 从 ipu_config.json 派生智能基元注册表与能力映射
- 提供 choose_ipu / choose_ipu_provider / resolve_ipu 三个查询入口
"""
import os
from enum import Enum

from data_shape import IPUProvider, IPUConfig
from .ipu_config_manager import ipu_config_manager


class IPUVendor(Enum):
    """智能基元供应商枚举（仅用于 IDE/调试提示，运行时仍走字符串）。"""
    MINIMAX = "minimax"
    DASHSCOPE = "dashscope"
    DEEPSEEK = "deepseek"


DEFAULT_ROLE_PROMPT = "你是个测试助手"


# ── 从配置文件派生全部智能基元数据 ──

def _build_registry():
    """从 ipu_config_manager 配置构建 IPU_REGISTRY、IPU_CAPS、providers 三份映射。

    纯函数：config → 三个 dict，不访问全局状态。
    """
    cfg = ipu_config_manager.load()

    ipu_registry: dict[str, dict[str, str]] = {}
    ipu_caps: dict[str, set[str]] = {}
    provider_map: dict[str, IPUProvider] = {}

    for prov in cfg.providers:
        ipu_registry[prov.name] = {}
        for short_name, entry in prov.ipus.items():
            if isinstance(entry, dict):
                ipu_registry[prov.name][short_name] = entry["id"]
                caps = entry.get("caps", [])
            else:
                ipu_registry[prov.name][short_name] = entry
                caps = []
            if caps:
                ipu_caps[short_name] = set(caps)

        provider_map[prov.name] = IPUProvider(
            api_key=os.getenv(prov.api_key_env, ""), base_url=prov.base_url, )

    return ipu_registry, ipu_caps, provider_map


IPU_REGISTRY, IPU_CAPS, providers = _build_registry()


def get_ipu_capabilities(provider: str, short_name: str) -> set[str]:
    """获取指定智能基元的能力标签集合"""
    try:
        _ = IPU_REGISTRY[provider][short_name]
    except KeyError:
        return set()
    return IPU_CAPS.get(short_name, set())


def choose_ipu(provider_name: str, ipu_name: str) -> str:
    """短名 → API 真实 ID。"""
    provider_ipus = IPU_REGISTRY.get(provider_name, {})
    if ipu_name not in provider_ipus:
        available = ", ".join(provider_ipus.keys()) if provider_ipus else "(无智能基元)"
        raise KeyError(
            f"智能基元 '{ipu_name}' 在供应商 {provider_name} 下不存在。{provider_name} 可用智能基元: {available}")
    return provider_ipus[ipu_name]


def choose_ipu_provider(provider_name: str) -> IPUProvider:
    if provider_name not in providers:
        available = ", ".join(providers.keys())
        raise KeyError(f"供应商 '{provider_name}' 不存在。可用供应商: {available}")
    return providers[provider_name]


def resolve_ipu(provider_name: str, ipu_name: str) -> tuple[IPUProvider, IPUConfig]:
    """解析为可调用的 (供应商, IPU客户端配置) 对。"""
    provider = choose_ipu_provider(provider_name)
    ipu_id = choose_ipu(provider_name, ipu_name)
    return provider, IPUConfig(
        ipu=ipu_id, base_url=provider.base_url, api_key=provider.api_key, )


# ── 注册表便利查询 ──────────────────────────────────────

def resolve_ipu_provider(ipu_name: str) -> str | None:
    """根据智能基元短名反向查 provider。无短名歧义时 provider=ipu_name 自身。"""
    for provider, ipus in IPU_REGISTRY.items():
        if ipu_name in ipus: return provider
    if ipu_name in IPU_REGISTRY: return ipu_name
    return None


def list_ipu_providers() -> list[str]:
    """所有已注册供应商名。"""
    return list(IPU_REGISTRY.keys())


def list_ipus(provider: str) -> list[str]:
    """某供应商下的全部智能基元短名。"""
    return list(IPU_REGISTRY.get(provider, {}).keys())
