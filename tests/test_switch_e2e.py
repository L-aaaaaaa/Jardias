"""真实端到端测试：创建角色 → 切换模型 → 验证成功。

通过 monkey-patching PROVIDER_CHAT 让所有 LLM 调用都是 mock，
避免真实 API 调用消耗 token，专注于验证切换链路。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from character.registry import registry
from character.config_io import save_config
from data_shape import ActorConfig, RoleConfig, IPURuntime
from common.bootstrap import bootstrap
from yinao.ipu_client import resolve_chat
from yinao.ipu_client import common_client_util as ccu
from yinao.ipu_client.common_client_util import reason_act_loop
from yinao.ipu_client.ipu_context import request_switch
from data_shape import RoundOutput, ToolCall


async def fake_round_first(messages, iteration, ipu_config, **kwargs):
    """模拟 LLM 第一轮返回：assistant 调用 update_runtime。"""
    tc = ToolCall(name="update_runtime", arguments='{"ipu": "v4-pro"}')
    output = RoundOutput(
        reasoning="",
        content="让我切换到 v4-pro",
        tool_calls=[tc],
        deltas=[],
        finish_reason="tool_calls",
    )
    return output, messages


async def fake_round_normal(messages, iteration, ipu_config, **kwargs):
    """模拟 LLM 后续轮：正常文字回复。"""
    output = RoundOutput(
        reasoning="",
        content="这是正常的回复。",
        tool_calls=[],
        deltas=[],
        finish_reason="stop",
    )
    return output, messages


async def fake_update_runtime(name: str, args: dict, char_name: str = "") -> str:
    """真实 update_runtime 工具行为：写 config.json + 调用 request_switch。"""
    if name == "update_runtime":
        new_ipu = args.get("ipu", "")
        from yinao.ipu_client.ipu_context import resolve_ipu_provider
        provider = resolve_ipu_provider(new_ipu) if new_ipu else None
        if provider and new_ipu and char_name:
            # 写 config.json 到磁盘（模拟真实 update_runtime 行为）
            from character.config_io import load_config, save_config
            cfg = load_config(char_name)
            cfg.runtime.provider = provider
            cfg.runtime.ipu = new_ipu
            save_config(cfg, char_name)
            # 写入全局切换状态
            request_switch(provider, new_ipu)
            return f"[OK] 已切换到 {provider}/{new_ipu}"
        return f"[Error] 未知 IPU: {new_ipu}"
    return f"[OK] {name}"


async def main():
    char_name = "switch-e2e-测试员"
    config_dir = Path(__file__).resolve().parent.parent / "config"

    # 清理旧角色
    if registry.exists(char_name):
        registry.delete(char_name)

    # 创建角色
    config = ActorConfig(
        identity=RoleConfig(
            system_prompt="你是一个测试切换机制的助手，名字叫 #{character_name}。",
            title="切换E2E测试员",
            traits="测试用",
        ),
        runtime=IPURuntime(provider="dashscope", ipu="千问3.6+"),
    )
    registry.create(char_name, config)
    save_config(config, char_name, config_dir=str(config_dir))
    print(f"[1] 角色已创建: {char_name} (dashscope/千问3.6+)")

    # 注入 monkey-patch
    original_round = ccu._run_common_round
    original_execute = ccu.execute_tool
    ccu._run_common_round = fake_round_first

    async def _fake_update_runtime(name, args):
        return await fake_update_runtime(name, args, char_name)

    ccu.execute_tool = _fake_update_runtime

    # 重置全局切换状态
    import yinao.ipu_client.ipu_context as ctx_mod
    ctx_mod.switch_request = None

    # 构建上下文
    ctx = bootstrap("dashscope", "千问3.6+", character_name=char_name)
    print(f"[2] bootstrap 完成: provider={ctx.provider}, ipu={ctx.ipu}")

    # 验证 resolve_chat
    chat_fn = resolve_chat(ctx.provider)
    print(f"[3] resolve_chat('{ctx.provider}') → {chat_fn.__name__}")
    assert chat_fn is not None, "resolve_chat 返回 None"

    # 运行 reason_act_loop，模拟用户让模型切换
    from data_shape import IPUConfig
    ipu_config = IPUConfig(ipu="qwen3.6-plus", api_key="fake", base_url="http://x")
    ipu_config.tools = []
    ipu_config.tool_choice = "auto"

    messages = [{"role": "user", "content": "请切换到 v4-pro"}]
    print(f"[4] 开始 reason_act_loop (mock 模式)...")

    result = await reason_act_loop(
        messages, ipu_config,
        reasoning_field="reasoning_content",
        reasoning_inline=False,
        character_name=char_name,
    )

    print(f"[5] reason_act_loop 返回: should_switch={result.should_switch}, "
          f"switch={result.switch_provider}/{result.switch_ipu}")

    # 还原 monkey-patch
    ccu._run_common_round = original_round
    ccu.execute_tool = original_execute

    # ── 验证结果 ──
    assert result.should_switch is True, "期望 should_switch=True"
    assert result.switch_provider == "deepseek", f"期望 provider=deepseek, 实得 {result.switch_provider}"
    assert result.switch_ipu == "v4-pro", f"期望 ipu=v4-pro, 实得 {result.switch_ipu}"

    # 验证 reload_after_switch 能正常重建 ctx
    from yinao.ipu_client import reload_after_switch
    reload_after_switch(ctx)
    print(f"[6] reload_after_switch 成功: 新 provider={ctx.provider}, ipu={ctx.ipu}")
    assert ctx.provider == "deepseek", f"reload 后 provider 应为 deepseek, 实得 {ctx.provider}"
    assert ctx.ipu == "v4-pro", f"reload 后 ipu 应为 v4-pro, 实得 {ctx.ipu}"

    print("\n" + "=" * 60)
    print("E2E 测试通过！")
    print("角色创建 → bootstrap → resolve_chat → reason_act_loop")
    print("→ update_runtime 触发切换 → reload_after_switch 重建上下文")
    print("完整切换链路工作正常。")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
