"""测试模型切换机制是否正常。

流程：
1. 创建一个新角色
2. 模拟 LLM 返回 tool_call(update_runtime 切到另一个供应商)
3. 验证 weave_thought 检测到 should_switch
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

# 确保项目根在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from character.registry import registry
from character.config_io import save_config
from data_shape import ActorConfig, RoleConfig, IPURuntime
from yinao.ipu_client import resolve_chat
from yinao.ipu_client.ipu_switch import PROVIDER_SPECS


def setup_test_character(name: str = "switch-test-角色") -> tuple:
    """创建测试角色并返回 (config, ctx)。"""
    from common.bootstrap import bootstrap

    # 清理旧角色
    if registry.exists(name):
        char_dir = registry.get_dir(name)
        if char_dir.exists():
            shutil.rmtree(char_dir)

    # 用第一个可用供应商创建
    first_prov = next(iter(PROVIDER_SPECS))  # dashscope
    first_ipu = first_prov if first_prov in ("dashscope", "deepseek", "minimax") else "default"

    # 直接创建配置
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_dir = os.path.join(base_dir, "config")
    os.makedirs(config_dir, exist_ok=True)

    config = ActorConfig(
        identity=RoleConfig(
            system_prompt="你是一个测试助手，名字叫 #{character_name}。",
            title="切换测试员",
            traits="用于测试模型切换",
        ),
        runtime=IPURuntime(
            provider=first_prov,
            ipu="default",
        ),
    )
    registry.create(name, config)
    save_config(config, name, config_dir=config_dir)

    ctx = bootstrap(first_prov, "default", character_name=name)
    return config, ctx, first_prov


def test_switch_detection_in_loop():
    """测试：weave_thought 内部调用 _run_single_round 后正确处理 should_switch。

    通过 monkey-patch _run_single_round 让它直接返回一个带有 tool_call(update_runtime)
    的 RoundOutput，从而触发 update_runtime 工具 → 请求切换 → 验证 should_switch。
    """
    from yinao.ipu_client.thought_weaver import weave_thought
    from yinao.ipu_client.ipu_switch import request_switch
    from yinao.ipu_client import thought_weaver as tw
    from data_shape import RoundOutput, ToolCall

    async def fake_round(messages, iteration, ipu_config, **kwargs):
        tc = ToolCall(name="update_runtime", arguments='{"ipu": "v4-pro"}')
        output = RoundOutput(
            reasoning="",
            content="切换到 v4-pro",
            tool_calls=[tc],
            finish_reason="tool_calls",
        )
        return output, messages

    original_round = tw._run_single_round
    tw._run_single_round = fake_round

    try:
        # 同时要 mock 工具执行让 update_runtime 真的能触发 request_switch
        from yinao.ipu_client.tool_runner import ToolRunner

        async def fake_run(self, tool_calls, messages, round_idx, on_history_save=None):
            for tc in tool_calls:
                if tc.name == "update_runtime":
                    request_switch("deepseek", "v4-pro")
                messages.append({"role": "tool",
                    "tool_call_id": getattr(tc, "id", None) or f"call_{round_idx}_0",
                    "name": tc.name, "content": "[OK]"})
            return messages

        class StubExecutor:
            async def execute(self, name, args):
                return "[OK]"

        fake_tool_runner = ToolRunner(executor=StubExecutor())
        fake_tool_runner.run = lambda tool_calls, messages, round_idx, on_history_save=None: fake_run(
            fake_tool_runner, tool_calls, messages, round_idx, on_history_save)

        messages = [{"role": "user", "content": "切到 v4-pro。"}]
        from data_shape import IPUConfig
        ipu_config = IPUConfig(ipu="test", api_key="fake", base_url="http://x")

        result = asyncio.run(weave_thought(
            messages, ipu_config,
            reasoning_field="reasoning_content",
            reasoning_inline=True,
            tool_runner=fake_tool_runner,
        ))

        assert result.should_switch is True, f"期望 should_switch=True, 实得 {result.should_switch}"
        assert result.switch_provider == "deepseek", f"期望 provider=deepseek, 实得 {result.switch_provider}"
        assert result.switch_ipu == "v4-pro", f"期望 ipu=v4-pro, 实得 {result.switch_ipu}"
        print(f"  [PASS] 切换检测: should_switch=True, {result.switch_provider}/{result.switch_ipu}")
    finally:
        tw._run_single_round = original_round
        try:
            ctx_mod.switch_request = None
        except NameError:
            pass


def test_provider_specs_registered():
    """测试：所有注册的 ProviderSpec 都能通过 PROVIDER_SPECS 拿到。"""
    assert len(PROVIDER_SPECS) >= 3, f"PROVIDER_SPECS 至少应有 3 个供应商，实得 {len(PROVIDER_SPECS)}"
    for name in ("dashscope", "deepseek", "minimax"):
        assert name in PROVIDER_SPECS, f"PROVIDER_SPECS 缺少 {name}"
        spec = PROVIDER_SPECS[name]
        assert spec.name == name, f"PROVIDER_SPECS['{name}'].name 应为 {name}，实得 {spec.name}"
    print(f"  [OK] PROVIDER_SPECS 已注册: {list(PROVIDER_SPECS.keys())}")


def test_switch_chat_fn():
    """测试：switch.resolve_chat 对所有供应商都能返回正确的 chat_fn。"""
    for prov in PROVIDER_SPECS:
        fn = resolve_chat(prov)
        assert fn is not None, f"resolve_chat('{prov}') 返回 None"
        assert asyncio.iscoroutinefunction(fn), f"resolve_chat('{prov}') 不是协程函数"
        print(f"  [OK] resolve_chat('{prov}') → {fn.__name__}")


def test_request_switch_round_trip():
    """测试：request_switch → pop_switch 往返。"""
    switch_request = None  # 重置

    from yinao.ipu_client.ipu_switch import request_switch, pop_switch
    from data_shape import IPUSwitch

    # 确保全局状态干净
    import yinao.ipu_client.ipu_context as ctx_mod
    ctx_mod.switch_request = None

    request_switch("deepseek", "v4-pro")
    req = pop_switch()

    assert req is not None, "pop_switch() 返回 None"
    assert isinstance(req, IPUSwitch), f"期望 IPUSwitch，实际 {type(req)}"
    assert req.provider == "deepseek"
    assert req.ipu == "v4-pro"
    print(f"  [PASS] request_switch → pop_switch: {req.provider}/{req.ipu}")


def test_provider_spec_configs():
    """测试：三个 ProviderSpec 的配置是否符合预期。"""
    expected = {
        "dashscope": {"thinking_mode": "enable", "reasoning_field": "reasoning_content"},
        "deepseek": {"thinking_mode": "toggle", "reasoning_inline": True},
        "minimax": {"thinking_mode": "m3", "reasoning_field": "reasoning_details"},
    }

    for name, expected_attrs in expected.items():
        spec = PROVIDER_SPECS[name]
        for key, expected_val in expected_attrs.items():
            actual = getattr(spec, key)
            assert actual == expected_val, \
                f"PROVIDER_SPECS['{name}'].{key}: 期望 {expected_val}, 实际 {actual}"
        print(f"  [OK] {name}: thinking_mode={spec.thinking_mode}, "
              f"reasoning_field={spec.reasoning_field}, "
              f"reasoning_inline={spec.reasoning_inline}")


if __name__ == "__main__":
    print("=" * 60)
    print("模型切换机制测试")
    print("=" * 60)

    print("\n[1] PROVIDER_SPECS 注册表检查")
    test_provider_specs_registered()

    print("\n[2] resolve_chat 解析检查")
    test_switch_chat_fn()

    print("\n[3] request_switch → pop_switch 往返")
    test_request_switch_round_trip()

    print("\n[4] ProviderSpec 配置验证")
    test_provider_spec_configs()

    print("\n[5] 模型切换检测（mock LLM）")
    test_switch_detection_in_loop()

    print("\n" + "=" * 60)
    print("全部测试通过！模型切换机制正常。")
    print("=" * 60)
