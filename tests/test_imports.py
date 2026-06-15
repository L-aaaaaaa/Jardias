"""
test_imports.py — 模块导入完整性检测

验证所有模块可以独立导入，无循环依赖或缺失符号。
这是 Phase 1 "确认基础可运行" 的第一道门槛。
"""
import sys
import os
import traceback

# 确保项目根目录在 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def test_all_imports():
    """逐模块导入，报告任何失败。"""
    modules = [
        # 入口
        "app",
        # 配置层
        "actor_config",
        "actor_config.config_io",
        "actor_config.model_resolver",
        "actor_config.provider_manager",
        # 角色管理
        "character",
        "character.history",
        "character.registry",
        "character.character_menu",
        "character.summarizer",
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
        "data_shape.agent_config",
        "data_shape.character",
        "data_shape.model_client",
        "data_shape.tool",
        # 媒体
        "media",
        "media.image",
        # 模型客户端
        "model_client",
        "model_client.common_client_util",
        "model_client.circuit_breaker",
        "model_client.switch",
        "model_client.model_context",
        "model_client.deepseek",
        "model_client.dashscope",
        "model_client.minimax",
        # 工具
        "tool",
        "tool.llm_tool",
        "tool.builtin",
    ]

    passed = 0
    failed = []

    for m in modules:
        try:
            __import__(m)
            passed += 1
        except Exception as e:
            failed.append((m, str(e)))

    print(f"\n{'='*50}")
    print(f"  模块导入检测: {passed}/{len(modules)} OK")
    print(f"{'='*50}")

    if failed:
        print("\n[FAIL] 以下模块导入失败:")
        for m, e in failed:
            print(f"  [{m}] {e}")
        return False

    print("[OK] 全部模块导入正常，无循环依赖")
    return True


def test_data_shape_exports():
    """验证 data_shape 统一出口导出了所有关键类型。"""
    from data_shape import (
        ActorConfig, IdentityConfig, RuntimeConfig,
        ModelEntry, ProviderConfig, ConfigFile,
        L1Summary,
        AIModelConfig, AIModelProvider, ToolCall,
        RoundOutput, ChatResult, RoundMeta, ModelSwitch,
        ToolDef, ToolParam,
    )
    print(f"  ActorConfig       = {ActorConfig}")
    print(f"  IdentityConfig    = {IdentityConfig}")
    print(f"  RuntimeConfig     = {RuntimeConfig}")
    print(f"  L1Summary         = {L1Summary}")
    print(f"  AIModelConfig     = {AIModelConfig}")
    print(f"  ToolDef           = {ToolDef}")
    print("[OK] data_shape 统一出口所有类型正常")


if __name__ == "__main__":
    ok = test_all_imports()
    if ok:
        test_data_shape_exports()
    sys.exit(0 if ok else 1)

