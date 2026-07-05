"""
test_imports.py — 模块导入完整性检测

验证所有模块可以独立导入，无循环依赖或缺失符号。
模块路径已按命名重构后的真实结构更新。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def test_all_imports():
    """逐模块导入，报告任何失败。"""
    modules = [
        # 入口
        "app",
        # 配置层（义脑）
        "yinao",
        "yinao.ipu_resolver",
        "yinao.provider_manager",
        "yinao.ipu_client",
        "yinao.ipu_client.switch",
        "yinao.ipu_client.ipu_context",
        "yinao.ipu_client.common_client_util",
        "yinao.ipu_client.circuit_breaker",
        "yinao.ipu_client.minimax",
        "yinao.ipu_client.dashscope",
        "yinao.ipu_client.deepseek",
        # 角色管理
        "character",
        "character.history",
        "character.registry",
        "character.character_menu",
        "character.summarizer",
        "character.config_io",
        # 公共模块
        "common",
        "common.context",
        "common.lifecycle",
        "common.logger",
        "common.actor_log",
        "common.utils",
        "common.bootstrap",
        "common.cli_style",
        # 数据形状
        "data_shape",
        "data_shape.yinao_config",
        "data_shape.ipu",
        "data_shape.ipu_client",
        "data_shape.character",
        "data_shape.tool",
        # 媒体
        "media",
        "media.image",
        # 工具
        "tool",
        "tool.llm_tool",
        "tool.builtin",
        # 时策
        "schedule",
        "schedule.types",
        "schedule.repository",
        "schedule.shice",
        "schedule.strategies",
        "schedule.concurrency",
    ]

    passed = 0
    failed = []

    for m in modules:
        try:
            __import__(m)
            passed += 1
        except Exception as e:
            failed.append((m, str(e)))

    print(f"\n{'=' * 50}")
    print(f"  模块导入检测: {passed}/{len(modules)} OK")
    print(f"{'=' * 50}")

    if failed:
        print("\n[FAIL] 以下模块导入失败:")
        for m, e in failed:
            print(f"  [{m}] {e}")
        return False

    print("[OK] 全部模块导入正常，无循环依赖")
    return True


def test_data_shape_exports():
    """验证 data_shape 统一出口导出了所有关键类型（按 IPU/ICP 新术语）。"""
    from data_shape import (
        ActorConfig, RoleConfig, IPURuntime,
        IPUEntry, IPUProviderConfig, IPUConfigFile,
        IPUConfig, IPUProvider, IPUSwitch,
        L1Summary, ToolDef,
    )
    print(f"  ActorConfig       = {ActorConfig}")
    print(f"  RoleConfig        = {RoleConfig}")
    print(f"  IPURuntime        = {IPURuntime}")
    print(f"  IPUEntry          = {IPUEntry}")
    print(f"  IPUProviderConfig = {IPUProviderConfig}")
    print(f"  IPUConfigFile     = {IPUConfigFile}")
    print(f"  IPUConfig         = {IPUConfig}")
    print(f"  IPUProvider       = {IPUProvider}")
    print(f"  IPUSwitch         = {IPUSwitch}")
    print(f"  L1Summary         = {L1Summary}")
    print(f"  ToolDef           = {ToolDef}")
    print("[OK] data_shape 统一出口所有类型正常")


if __name__ == "__main__":
    ok = test_all_imports()
    if ok:
        test_data_shape_exports()
    sys.exit(0 if ok else 1)