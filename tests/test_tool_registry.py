"""
test_tool_registry.py — 工具注册系统测试

验证 ToolRegistry 的注册、定义生成、执行调度。
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from data_shape import ToolDef, ToolParam
from tool.builtin import tools, ToolRegistry, _BUILTIN_HANDLERS


def test_tools_object_exists():
    """tools 全局实例存在且为 ToolRegistry 类型。"""
    assert isinstance(tools, ToolRegistry), f"tools 应为 ToolRegistry，实际 {type(tools)}"
    print("  [OK] tools 全局实例: ToolRegistry")


def test_get_definitions_format():
    """get_definitions 返回符合 OpenAI tool 规范的格式。"""
    defs = tools.get_definitions()
    assert isinstance(defs, list), f"应为 list，实际 {type(defs)}"
    assert len(defs) > 0, "应有已注册工具"

    for d in defs:
        assert d["type"] == "function", f"tool type 应为 function: {d}"
        fn = d["function"]
        assert "name" in fn, f"缺少 name: {d}"
        assert "description" in fn, f"缺少 description: {d}"
        assert "parameters" in fn, f"缺少 parameters: {d}"

    names = [d["function"]["name"] for d in defs]
    print(f"  [OK] get_definitions: {len(defs)} 个工具，格式正确")
    print(f"     工具列表: {', '.join(names)}")


def test_list_names():
    """list_names 返回所有工具名。"""
    names = tools.list_names()
    assert len(names) > 0
    assert all(isinstance(n, str) for n in names)
    print(f"  [OK] list_names: {len(names)} 个名称")


def test_get_existing():
    """get 可以获取已注册工具。"""
    names = tools.list_names()
    for name in names[:3]:
        td = tools.get(name)
        assert td is not None, f"工具 '{name}' 应可获取"
        assert isinstance(td, ToolDef)
        assert td.name == name
    print("  [OK] get: 已注册工具可获取")


def test_handler_registration():
    """_BUILTIN_HANDLERS 已注册处理器。"""
    assert len(_BUILTIN_HANDLERS) > 0, "应有已注册的 handler"
    # 确认自指涉工具都有 handler
    self_ref_tools = [
        "update_runtime", "update_identity", "summarize_conversation",
        "create_character", "send_to_character", "list_characters",
    ]
    missing = [t for t in self_ref_tools if t not in _BUILTIN_HANDLERS]
    assert not missing, f"自指涉工具缺少 handler: {missing}"
    print(f"  [OK] _BUILTIN_HANDLERS: {len(_BUILTIN_HANDLERS)} 个 handler 已注册")


def test_file_tools_registered():
    """文件类工具全部注册。"""
    file_tools = ["read_file", "write_file", "list_dir", "glob", "grep", "file_info"]
    names = set(tools.list_names())
    missing = [t for t in file_tools if t not in names]
    assert not missing, f"缺少文件工具: {missing}"
    print("  [OK] 6 个文件类工具全部注册")


def test_general_tools_registered():
    """通用工具注册。"""
    general = ["bash", "web_fetch", "web_search"]
    names = set(tools.list_names())
    missing = [t for t in general if t not in names]
    assert not missing, f"缺少通用工具: {missing}"
    print("  [OK] 3 个通用工具全部注册")


if __name__ == "__main__":
    test_tools_object_exists()
    test_get_definitions_format()
    test_list_names()
    test_get_existing()
    test_handler_registration()
    test_file_tools_registered()
    test_general_tools_registered()
    print("\n" + "="*50)
    print("  [OK] 工具注册: 全部 7 项测试通过")
    print("="*50)

