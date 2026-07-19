"""jardias 入口(薄壳)

这个模块作为 `pip install jardias` 之后的 console_script 入口。
整个包的实质逻辑在 app.py 与各子模块中,这里只负责:

1. 解析命令行参数(与原 `python app.py` 一致,完整兼容)
2. 委托给 app.main / app.interactive_loop

为什么存在这个文件而不是直接指向 `app:main`?
- app.py 用 `sys.path.insert` hack 让 `python app.py` 在没有 pip install 的情况下也能运行
- pip install 之后 sys.path 已经被包本身正确填充,那些 hack 不再必要
- 但保留 `python app.py` 的向后兼容(用户文档/日志/run.bat 都依赖它)
- 因此我们新做一个 *真正* 的入口,把兼容工作集中到一处
"""
from __future__ import annotations

import argparse
import asyncio
import sys


def cli() -> None:
    """console_script 入口:被 `jardias` 命令调用。"""
    parser = _build_parser()
    args = parser.parse_args()

    if args.list:
        _list_characters()
        return

    if args.character:
        asyncio.run(_run_with_character(args))
    else:
        asyncio.run(_run_interactive())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jardias",
        description="jardias 的 AI 演员框架",
    )
    parser.add_argument(
        "--provider",
        default="minimax",
        help="智能基元供应商 (minimax/dashscope/deepseek)",
    )
    parser.add_argument(
        "--ipu",
        default="2.7",
        help="智能基元简称(短名)",
    )
    parser.add_argument(
        "--character",
        default=None,
        help="启动时使用的角色名(不指定则进入交互菜单)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有角色",
    )
    return parser


def _list_characters() -> None:
    """复制自原 app.py 的 --list 行为,保持完全一致。"""
    from character.registry import registry

    chars = registry.scan()
    if not chars:
        print("暂无角色。")
        return

    print(f"共 {len(chars)} 个角色:\n")
    for name in chars:
        try:
            config = registry.get_config(name)
            prov = config.runtime.provider
            ipu = config.runtime.ipu
            title = config.identity.title or "(未设置)"
            traits = config.identity.traits or "(无描述)"
            print(f"  {name}")
            print(f"    角色: {title}  |  引擎: {prov}/{ipu}")
            print(f"    描述: {traits}")
            print()
        except Exception as e:
            print(f"  {name}: (配置错误: {e})\n")


async def _run_with_character(args: argparse.Namespace) -> None:
    """等价于原 app.py: main(provider, ipu, character)。"""
    from common.bootstrap import bootstrap
    from common.lifecycle import conversation_loop

    ctx = bootstrap(args.provider, args.ipu, character_name=args.character)
    await conversation_loop(ctx)


async def _run_interactive() -> None:
    """等价于原 app.py: interactive_loop()。"""
    from common.lifecycle import interactive_loop

    await interactive_loop()


if __name__ == "__main__":  # 兜底: `python -m jardias` 也能用
    cli()
