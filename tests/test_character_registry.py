"""
test_character_registry.py — 角色注册表 CRUD 测试

验证 CharacterRegistry 的 scan/exists/create/delete/get_config 完整链路。
"""
import sys
import os
import shutil
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from data_shape import ActorConfig, IdentityConfig, RuntimeConfig
from character.registry import registry, CharacterRegistry


TEST_CHARS = ["_test_reg_a", "_test_reg_b", "_test_reg_c"]


def setup():
    char_root = Path(PROJECT_ROOT) / "character_data"
    for name in TEST_CHARS:
        for pattern in [f"*-{name}", name]:
            for char_dir in char_root.glob(pattern):
                if char_dir.is_dir():
                    shutil.rmtree(char_dir)


def teardown():
    char_root = Path(PROJECT_ROOT) / "character_data"
    for name in TEST_CHARS:
        for pattern in [f"*-{name}", name]:
            for char_dir in char_root.glob(pattern):
                if char_dir.is_dir():
                    shutil.rmtree(char_dir)


def _char_dir(name: str) -> Path | None:
    """查找角色目录（支持时间戳前缀）。"""
    char_root = Path(PROJECT_ROOT) / "character_data"
    for pattern in [f"*-{name}", name]:
        found = list(char_root.glob(pattern))
        if found:
            return found[0]
    return None


def test_scan_empty():
    """在清理后的环境中，scan 不包含测试角色。"""
    all_chars = set(registry.scan())
    for name in TEST_CHARS:
        assert name not in all_chars, f"测试角色 '{name}' 未清理干净"
    print(f"  [OK] scan: 测试环境干净，当前角色数={len(all_chars)}")


def test_create_and_exists():
    """创建角色 → exists 返回 True。"""
    config = ActorConfig(
        identity=IdentityConfig(title="注册表测试A"),
        runtime=RuntimeConfig(provider="minimax", model="2.7"),
    )
    registry.create(TEST_CHARS[0], config)
    assert registry.exists(TEST_CHARS[0]), "exists 应返回 True"
    assert TEST_CHARS[0] in registry.scan(), "scan 应包含新创建角色"
    print(f"  [OK] create + exists + scan: '{TEST_CHARS[0]}' 已注册")


def test_create_duplicate_raises():
    """创建重复角色应抛出 ValueError。"""
    config = ActorConfig(identity=IdentityConfig(title="重复测试"))
    registry.create(TEST_CHARS[1], config)

    raised = False
    try:
        registry.create(TEST_CHARS[1], config)
    except ValueError as e:
        raised = True
        assert "已存在" in str(e)
        print(f"  [OK] create 重复角色 -> ValueError: {e}")
    assert raised, "应抛出 ValueError"


def test_get_config():
    """get_config 应返回完整角色配置。"""
    config = ActorConfig(
        identity=IdentityConfig(
            system_prompt="你好，我是测试角色。",
            title="测试配置读取",
            traits="仔细",
        ),
        runtime=RuntimeConfig(
            provider="deepseek",
            model="v4",
            temperature=0.3,
        ),
    )
    registry.create(TEST_CHARS[2], config)
    loaded = registry.get_config(TEST_CHARS[2])

    assert loaded.identity.title == "测试配置读取"
    assert loaded.identity.system_prompt == "你好，我是测试角色。"
    assert loaded.runtime.provider == "deepseek"
    assert loaded.runtime.model == "v4"
    assert loaded.runtime.temperature == 0.3
    print(f"  [OK] get_config: 配置读取完整一致")


def test_delete():
    """delete 后角色不可访问。"""
    registry.delete(TEST_CHARS[0])
    assert not registry.exists(TEST_CHARS[0]), "delete 后 exists 应为 False"
    char_dir = _char_dir(TEST_CHARS[0])
    assert char_dir is None, f"角色目录应被删除: {char_dir}"
    print(f"  [OK] delete: '{TEST_CHARS[0]}' 已清理")


def test_cannot_delete_default():
    """不能删除 default 角色。"""
    try:
        registry.delete("default")
        assert False, "应抛出 ValueError"
    except ValueError as e:
        assert "default" in str(e)
    print("  [OK] delete default → ValueError (保护)")


def test_get_context_latest_path():
    """get_context_latest_path 返回正确路径。"""
    registry.create(TEST_CHARS[0], ActorConfig(identity=IdentityConfig(title="路径测试")))
    path = registry.get_context_latest_path(TEST_CHARS[0])
    assert isinstance(path, Path)
    assert path.name == "context_latest.md"
    print(f"  [OK] get_context_latest_path: {path}")


if __name__ == "__main__":
    setup()
    try:
        test_scan_empty()
        test_create_and_exists()
        test_create_duplicate_raises()
        test_get_config()
        test_delete()
        test_cannot_delete_default()
        test_get_context_latest_path()
        print("\n" + "="*50)
        print("  [OK] 角色注册表: 全部 7 项测试通过")
        print("="*50)
    finally:
        teardown()

