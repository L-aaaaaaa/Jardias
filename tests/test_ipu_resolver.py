"""
test_ipu_resolver.py — 智能基元（IPU）解析与能力映射测试

验证 providers.json5 → IPU_REGISTRY / IPU_CAPS / providers 的构建正确性。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from yinao.ipu_resolver import (
    IPU_REGISTRY, IPU_CAPS, providers,
    choose_ipu, choose_ipu_provider, resolve_ipu,
    _build_registry,
)
from data_shape import IPUProvider, IPUConfig


def test_ipu_registry_loaded():
    """IPU_REGISTRY 非空，包含至少一个 provider。"""
    assert len(IPU_REGISTRY) > 0, "IPU_REGISTRY 不应为空"
    for provider_name, ipus in IPU_REGISTRY.items():
        print(f"  {provider_name}: {len(ipus)} 个智能基元")
        assert len(ipus) > 0, f"provider '{provider_name}' 下应至少有一个智能基元"
    print("  [OK] IPU_REGISTRY: 所有 provider 都有智能基元")


def test_ipu_capabilities():
    """IPU_CAPS 中的智能基元有对应的能力标签。"""
    for short_name, caps in IPU_CAPS.items():
        assert isinstance(caps, set), f"caps 应为 set: {short_name}"
        valid_caps = {"vision", "thinking", "reasoning_stream", "fast", "tool_call", "long_context", "text"}
        unknown = caps - valid_caps
        if unknown:
            print(f"  [WARN] {short_name} 有未知能力标签: {unknown}")
    print(f"  [OK] IPU_CAPS: {len(IPU_CAPS)} 个智能基元有能力标签")


def test_providers_loaded():
    """providers dict 包含 IPUProvider。"""
    assert len(providers) > 0, "providers 不应为空"
    for name, provider in providers.items():
        assert isinstance(provider, IPUProvider), f"{name} 应为 IPUProvider"
        assert provider.base_url, f"{name} 缺少 base_url"
    print(f"  [OK] providers: {len(providers)} 个 provider 已加载")


def test_choose_ipu():
    """choose_ipu 正确映射短名 → API ID。"""
    for provider_name in IPU_REGISTRY:
        short_names = list(IPU_REGISTRY[provider_name].keys())
        if short_names:
            first_short = short_names[0]
            api_id = choose_ipu(provider_name, first_short)
            assert isinstance(api_id, str)
            assert len(api_id) > 0
            print(f"  {provider_name}/{first_short} → {api_id}")
    print("  [OK] choose_ipu: 短名映射正确")


def test_choose_ipu_invalid():
    """不存在的智能基元应抛出 KeyError。"""
    try:
        first_provider = list(IPU_REGISTRY.keys())[0]
        choose_ipu(first_provider, "__nonexistent_ipu__")
        assert False, "应抛出 KeyError"
    except KeyError as e:
        assert "__nonexistent_ipu__" in str(e)
    print("  [OK] choose_ipu 无效智能基元 → KeyError")


def test_choose_provider():
    """有效 provider 返回 IPUProvider。"""
    for name in providers:
        p = choose_ipu_provider(name)
        assert isinstance(p, IPUProvider)
        assert p.base_url
    print("  [OK] choose_ipu_provider: 所有 provider 可用")


def test_choose_provider_invalid():
    """不存在的 provider 抛出 KeyError。"""
    try:
        choose_ipu_provider("__nonexistent__")
        assert False, "应抛出 KeyError"
    except KeyError as e:
        assert "__nonexistent__" in str(e)
    print("  [OK] choose_ipu_provider 无效 → KeyError")


def test_resolve_ipu():
    """resolve_ipu 返回 (IPUProvider, IPUConfig) 元组。"""
    for provider_name in providers:
        short_names = list(IPU_REGISTRY[provider_name].keys())
        if short_names:
            p, cfg = resolve_ipu(provider_name, short_names[0])
            assert isinstance(p, IPUProvider)
            assert isinstance(cfg, IPUConfig)
            assert cfg.ipu, f"ipu 不应为空: {provider_name}/{short_names[0]}"
            print(f"  {provider_name}/{short_names[0]} → {cfg.ipu}")
    print("  [OK] resolve_ipu: 供应商+智能基元解析正确")


def test_build_registry_deterministic():
    """_build_registry 是纯函数，多次调用结果一致。"""
    a = _build_registry()
    b = _build_registry()
    assert a[0] == b[0], "IPU_REGISTRY 应一致"
    assert a[1] == b[1], "IPU_CAPS 应一致"
    for name in a[2]:
        assert a[2][name].base_url == b[2][name].base_url, f"{name} base_url 应一致"
    print("  [OK] _build_registry: 纯函数，幂等")


if __name__ == "__main__":
    test_ipu_registry_loaded()
    test_ipu_capabilities()
    test_providers_loaded()
    test_choose_ipu()
    test_choose_ipu_invalid()
    test_choose_provider()
    test_choose_provider_invalid()
    test_resolve_ipu()
    test_build_registry_deterministic()
    print("\n" + "=" * 50)
    print("  [OK] 智能基元解析: 全部 9 项测试通过")
    print("=" * 50)