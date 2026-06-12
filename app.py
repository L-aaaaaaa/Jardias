"""Agent01 — AI Agent 框架入口。"""
import asyncio
import sys
import os
import signal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 优雅处理 Ctrl+C，避免 ugly traceback 输出到 codecs.py
if hasattr(signal, "SIGINT"):
    signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))

from common import bootstrap, conversation_loop
from common.lifecycle import interactive_loop


async def main(provider: str = "minimax", model: str = "2.7快", character: str = "default"):
    ctx = bootstrap(provider, model, character_name=character)
    await conversation_loop(ctx)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agent01 — AI Agent 框架")
    parser.add_argument("--provider", default="minimax", help="LLM 供应商 (minimax/dashscope/deepseek)")
    parser.add_argument("--model", default="2.7快", help="模型短名")
    parser.add_argument("--character", default=None, help="启动时使用的角色（不指定则进入交互菜单）")
    parser.add_argument("--list", action="store_true", help="列出所有角色")
    args = parser.parse_args()

    if args.list:
        from character.registry import registry
        chars = registry.scan()
        if not chars:
            print("暂无角色。")
        else:
            print(f"共 {len(chars)} 个角色:\n")
            for name in chars:
                try:
                    config = registry.get_config(name)
                    prov = config.runtime.provider
                    model = config.runtime.model
                    title = config.identity.title or "(未设置)"
                    traits = config.identity.traits or "(无描述)"
                    print(f"  {name}")
                    print(f"    角色: {title}  |  引擎: {prov}/{model}")
                    print(f"    描述: {traits}")
                    print()
                except Exception as e:
                    print(f"  {name}: (配置错误: {e})\n")
        sys.exit(0)

    try:
        if args.character:
            asyncio.run(main(args.provider, args.model, args.character))
        else:
            asyncio.run(interactive_loop())
    except KeyboardInterrupt:
        pass  # signal handler 已处理，此处静默退出
