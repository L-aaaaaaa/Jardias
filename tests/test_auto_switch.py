"""让真实角色跑起来：创建角色 → 真实对话 → 让模型自主触发模型切换。

模式与 test_real_switch.py 相同，但本脚本：
1. 创建全新的独立角色（不污染 switch-real-测试员）
2. system_prompt 让模型自主决定何时切换（而非被用户显式指示）
3. 第一轮：先自我介绍 + 自报当前引擎
4. 第二轮：让模型自主决定切换到另一个引擎
5. 第三轮：在新引擎下确认

运行：D:\B\Python3.10\python.exe tests/test_auto_switch.py
"""
from __future__ import annotations

import asyncio
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
from common.actor_log import turn_open
from tool.builtin import tools


CHAR_NAME = "202607132000-自动切换实验员"
START_PROVIDER = "minimax"
START_IPU = "2.7快"
TARGET_IPU = "v4-pro"  # 让模型自己切到这个


def setup_role(name: str, provider: str, ipu: str):
    """创建或重置一个角色。

    system_prompt 让模型"自主"决定何时切换引擎 — 强调不是被用户指挥。
    """
    if registry.exists(name):
        registry.delete(name)

    config = ActorConfig(
        identity=RoleConfig(
            system_prompt=(
                f"你是 {name}，一个**会自主切换引擎**的 AI 助手。"
                f"你的任务不是被用户指挥切换，而是**自己**在合适的时机调用 update_runtime 切换引擎。"
                "工作流程：\n"
                "1. 用户向你问好时，先简短自我介绍，并报告你当前跑的引擎是什么。\n"
                "2. 当用户让你'试试切换'或'换个引擎看看'时，**自主决定**要切到哪个引擎，"
                "然后调用 update_runtime 工具（参数 ipu=\"v4-pro\"）切换。\n"
                "3. 切换完成后确认一下新引擎即可，不要再做事。\n"
                "4. 用户问你'现在跑在哪个引擎上'，必须据实回答（基于最近的 update_runtime 结果）。"
            ),
            title="自动切换实验员",
            traits="模型自主切换",
        ),
        runtime=IPURuntime(
            provider=provider, ipu=ipu,
            temperature=1.0, top_p=0.95, max_icp=8192,
            thinking_mode="auto", reasoning_effort="high", thinking_enabled=True,
        ),
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
    setup_role(CHAR_NAME, START_PROVIDER, START_IPU)
    ctx = bootstrap(START_PROVIDER, START_IPU, character_name=CHAR_NAME)
    print(f"[2] bootstrap 完成: provider={ctx.provider}, ipu={ctx.ipu}")

    # 第一轮：先让模型自我介绍并报告当前引擎
    print("\n[3] 第一轮：自我介绍 + 报告当前引擎...")
    user_msg1 = "你好，先做个自我介绍吧，并告诉我你现在跑在哪个引擎上。"
    round_ok, messages = await drive_one_turn(ctx, user_msg1)
    if round_ok:
        reply = extract_reply(messages)
        print(f"[4] 角色回复: {reply[:300] if reply else '(空)'}")
    print(f"[5] 当前状态: provider={ctx.provider}, ipu={ctx.ipu}")

    # 第二轮：让模型自主切换
    print(f"\n[6] 第二轮：让模型自主切换到 {TARGET_IPU}...")
    user_msg2 = f"我觉得现在这个引擎不够好，请你自主决定切换到更合适的引擎（提示：试试 {TARGET_IPU}），然后告诉我结果。"
    round_ok2, messages2 = await drive_one_turn(ctx, user_msg2)
    if round_ok2:
        reply2 = extract_reply(messages2)
        print(f"[7] 角色回复: {reply2[:300] if reply2 else '(空)'}")
    print(f"[8] 当前状态: provider={ctx.provider}, ipu={ctx.ipu}")

    # 检查切换
    cfg = load_config(CHAR_NAME)
    print(f"[9] config.json: provider={cfg.runtime.provider}, ipu={cfg.runtime.ipu}")
    if cfg.runtime.ipu == TARGET_IPU:
        print(f"\n[OK] 模型自主切换成功！新引擎: {cfg.runtime.provider}/{cfg.runtime.ipu}")
    else:
        print(f"\n[WARN] 模型未切换到 {TARGET_IPU}，当前 ipu={cfg.runtime.ipu}")

    # 第三轮：在新引擎下确认
    print("\n[A] 第三轮：在新引擎下确认...")
    round_ok3, messages3 = await drive_one_turn(ctx, "确认一下你现在跑在哪个引擎上？")
    if round_ok3:
        reply3 = extract_reply(messages3)
        print(f"[B] 角色回复: {reply3[:300] if reply3 else '(空)'}")

    print(f"\n[C] 完成。experience.md 和 history.json 已写入角色目录: character_data/{CHAR_NAME}/")


if __name__ == "__main__":
    asyncio.run(main())