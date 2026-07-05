"""
test_model_resolver.py — 已弃用（DEPRECATED）shim。

内容已迁移到 test_ipu_resolver.py（按 B 阶段"文件名残留 → 改名"执行）。
本文件保留为 stub 以避免破坏 import 路径，待后续清理阶段删除。
执行实际测试请运行：

    python tests/test_ipu_resolver.py
"""
# 重导出新测试模块的全部公共符号，使旧 import 路径仍可用
from tests.test_ipu_resolver import (
    test_ipu_registry_loaded,
    test_ipu_capabilities,
    test_providers_loaded,
    test_choose_ipu,
    test_choose_ipu_invalid,
    test_choose_provider,
    test_choose_provider_invalid,
    test_resolve_ipu,
    test_build_registry_deterministic,
)

# 旧测试函数名映射（向后兼容）
test_model_names_loaded = test_ipu_registry_loaded
test_model_capabilities = test_ipu_capabilities
test_choose_model = test_choose_ipu
test_choose_model_invalid = test_choose_ipu_invalid
test_resolve_model = test_resolve_ipu
test_build_all_deterministic = test_build_registry_deterministic

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
    print("  [OK via shim] 智能基元解析: 全部 9 项测试通过")
    print("=" * 50)