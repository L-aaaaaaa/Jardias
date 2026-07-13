"""让真实角色跑起来：创建角色 → 真实对话 → 触发模型切换。

不 mock 任何东西，让真正的 LLM 通过工具调用 update_runtime 切换模型。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 中文 stdout 配置
for _stream_name in ("stdin", "stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from character.registry import registry
from character.config_io import save_config, load_config
from data_shape import ActorConfig, RoleConfig, IPURuntime
from common.bootstrap import bootstrap
from common.lifecycle import _run_turn, _post_round_async, _collect_round_meta, extract_reply
from common.context import form_full_context
from common.actor_log import turn_open
from tool.builtin import tools


def setup_role(name: str, provider: str, ipu: str):
    """创建或重置一个角色。"""
    if registry.exists(name):
        registry.delete(name)

    config = ActorConfig(
        identity=RoleConfig(
            system_prompt=(
                f"你是一个测试切换机制的助手，名字叫 {name}。"
                "你的任务是：被用户告知\"请切换到 v4-pro 模型\"后，"
                "调用 update_runtime 工具（参数 ipu=\"v4-pro\"）来切换到 deepseek/v4-pro。"
                "切换完成后，简单确认一下\"已切换到 v4-pro\"即可，不要做其他事。"
            ),
            title="切换测试员",
            traits="测试用",
        ),
        runtime=IPURuntime(provider=provider, ipu=ipu),
    )
    registry.create(name, config)
    save_config(config, name)
    print(f"[1] 角色已创建: {name} ({provider}/{ipu})")


async def drive_one_turn(ctx, user_text: str):
    """运行一轮真实对话。"""
    round_context = ""

    turn_open(ctx.turn_num, ctx.config.runtime.provider, ctx.config.runtime.ipu,
        ctx.ipu_config.ipu, runtime=ctx.config.runtime,
        tool_defs=tools.get_definitions())

    round_ok, messages = await _run_turn(ctx, user_text, None, None, round_context)
    round_context = _collect_round_meta(round_ok, ctx)

    await _post_round_async(ctx, user_text, messages, round_ok, round_context=round_context)
    return round_ok, messages


async def main():
    char_name = "switch-real-测试员"
    provider, ipu = "minimax", "2.7快"

    setup_role(char_name, provider, ipu)
    ctx = bootstrap(provider, ipu, character_name=char_name)
    print(f"[2] bootstrap 完成: provider={ctx.provider}, ipu={ctx.ipu}")

    # 第一轮：让模型尝试切换到 v4-pro
    print("\n[3] 第一轮对话：要求切换到 v4-pro...")
    user_msg = "请立刻调用 update_runtime 工具，把 ipu 改为 v4-pro，然后告诉我切换结果。"
    round_ok, messages = await drive_one_turn(ctx, user_msg)

    if round_ok:
        reply = extract_reply(messages)
        print(f"[4] 角色回复: {reply[:200] if reply else '(空)'}")

    # 检查切换是否成功
    print(f"[5] 当前 provider={ctx.provider}, ipu={ctx.ipu}")
    cfg = load_config(char_name)
    print(f"[6] config.json: provider={cfg.runtime.provider}, ipu={cfg.runtime.ipu}")

    if cfg.runtime.ipu == "v4-pro":
        print("\n✓ 模型切换成功！新引擎: " + f"{cfg.runtime.provider}/{cfg.runtime.ipu}")
    else:
        print("\n✗ 模型切换未生效")

    # 第二轮：确认在新引擎下还能正常对话
    print("\n[7] 第二轮对话：在新引擎下说话...")
    round_ok2, messages2 = await drive_one_turn(ctx, "确认一下你现在跑在哪个模型上？")
    if round_ok2:
        reply2 = extract_reply(messages2)
        print(f"[8] 角色回复: {reply2[:200] if reply2 else '(空)'}")

    print("\n[9] 完成。experience.md 应当已写入。")


if __name__ == "__main__":
    asyncio.run(main())