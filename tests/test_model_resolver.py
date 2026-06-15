"""
test_model_resolver.py — 模型解析与能力映射测试

验证 providers.json5 → MODEL_NAMES / MODEL_CAPABILITIES 的构建正确性。
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from actor_config.model_resolver import (
    MODEL_NAMES, MODEL_CAPABILITIES, providers,
    get_model_capabilities, choose_model, choose_provider, resolve_model,
    _build_all,
)
from data_shape import AIModelProvider, AIModelConfig


def test_model_names_loaded():
    """MODEL_NAMES 非空，包含至少一个 provider。"""
    assert len(MODEL_NAMES) > 0, "MODEL_NAMES 不应为空"
    for provider_name, models in MODEL_NAMES.items():
        print(f"  {provider_name}: {len(models)} 个模型")
        assert len(models) > 0, f"provider '{provider_name}' 下应至少有一个模型"
    print("  [OK] MODEL_NAMES: 所有 provider 都有模型")


def test_model_capabilities():
    """MODEL_CAPABILITIES 中的模型有对应的能力标签。"""
    for short_name, caps in MODEL_CAPABILITIES.items():
        assert isinstance(caps, set), f"caps 应为 set: {short_name}"
        valid_caps = {"vision", "thinking", "reasoning_stream", "fast", "tool_call", "long_context", "text"}
        unknown = caps - valid_caps
        if unknown:
            print(f"  [WARN] {short_name} 有未知能力标签: {unknown}")
    print(f"  [OK] MODEL_CAPABILITIES: {len(MODEL_CAPABILITIES)} 个模型有能力标签")


def test_providers_loaded():
    """providers dict 包含 AIModelProvider。"""
    assert len(providers) > 0, "providers 不应为空"
    for name, provider in providers.items():
        assert isinstance(provider, AIModelProvider), f"{name} 应为 AIModelProvider"
        assert provider.base_url, f"{name} 缺少 base_url"
    print(f"  [OK] providers: {len(providers)} 个 provider 已加载")


def test_choose_model():
    """choose_model 正确映射短名 → API ID。"""
    for provider_name in MODEL_NAMES:
        short_names = list(MODEL_NAMES[provider_name].keys())
        if short_names:
            first_short = short_names[0]
            api_id = choose_model(provider_name, first_short)
            assert isinstance(api_id, str)
            assert len(api_id) > 0
            print(f"  {provider_name}/{first_short} → {api_id}")
    print("  [OK] choose_model: 短名映射正确")


def test_choose_model_invalid():
    """不存在的模型应抛出 KeyError。"""
    try:
        first_provider = list(MODEL_NAMES.keys())[0]
        choose_model(first_provider, "__nonexistent_model__")
        assert False, "应抛出 KeyError"
    except KeyError as e:
        assert "__nonexistent_model__" in str(e)
    print("  [OK] choose_model 无效模型 → KeyError")


def test_choose_provider():
    """有效 provider 返回 AIModelProvider。"""
    for name in providers:
        p = choose_provider(name)
        assert isinstance(p, AIModelProvider)
        assert p.base_url
    print("  [OK] choose_provider: 所有 provider 可用")


def test_choose_provider_invalid():
    """不存在的 provider 抛出 KeyError。"""
    try:
        choose_provider("__nonexistent__")
        assert False, "应抛出 KeyError"
    except KeyError as e:
        assert "__nonexistent__" in str(e)
    print("  [OK] choose_provider 无效 → KeyError")


def test_resolve_model():
    """resolve_model 返回 (AIModelProvider, AIModelConfig) 元组。"""
    for provider_name in providers:
        short_names = list(MODEL_NAMES[provider_name].keys())
        if short_names:
            p, cfg = resolve_model(provider_name, short_names[0])
            assert isinstance(p, AIModelProvider)
            assert isinstance(cfg, AIModelConfig)
            assert cfg.model, f"model 不应为空: {provider_name}/{short_names[0]}"
            print(f"  {provider_name}/{short_names[0]} → {cfg.model}")
    print("  [OK] resolve_model: 供应商+模型解析正确")


def test_build_all_deterministic():
    """_build_all 是纯函数，多次调用结果一致。"""
    a = _build_all()
    b = _build_all()
    assert a[0] == b[0], "MODEL_NAMES 应一致"
    assert a[1] == b[1], "MODEL_CAPABILITIES 应一致"
    # providers 对象不可直接比较，但 base_url 应一致
    for name in a[2]:
        assert a[2][name].base_url == b[2][name].base_url, f"{name} base_url 应一致"
    print("  [OK] _build_all: 纯函数，幂等")


if __name__ == "__main__":
    test_model_names_loaded()
    test_model_capabilities()
    test_providers_loaded()
    test_choose_model()
    test_choose_model_invalid()
    test_choose_provider()
    test_choose_provider_invalid()
    test_resolve_model()
    test_build_all_deterministic()
    print("\n" + "="*50)
    print("  [OK] 模型解析: 全部 9 项测试通过")
    print("="*50)

