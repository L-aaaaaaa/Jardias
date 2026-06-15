"""
test_config_io.py — 配置读写完整链路测试

验证 ActorConfig 的 JSON 序列化/反序列化、save→load 往返一致性。
测试要点（依据测试经验文档）：
  - 使用真实的 character_data/ 目录，不 mock
  - 验证生成的文件内容，不是只靠返回值
"""
import sys
import os
import json
import shutil
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from data_shape import ActorConfig, IdentityConfig, RuntimeConfig
from actor_config.config_io import (
    load_config, save_config, init_config,
    config_to_dict, config_from_dict,
)


TEST_CHAR = "_test_io"


def setup():
    """准备干净的测试环境。"""
    char_root = Path(PROJECT_ROOT) / "character_data"
    for pattern in [f"*-{TEST_CHAR}", TEST_CHAR]:
        for char_dir in char_root.glob(pattern):
            if char_dir.is_dir():
                shutil.rmtree(char_dir)


def teardown():
    """清理测试角色数据。"""
    char_root = Path(PROJECT_ROOT) / "character_data"
    for pattern in [f"*-{TEST_CHAR}", TEST_CHAR]:
        for char_dir in char_root.glob(pattern):
            if char_dir.is_dir():
                shutil.rmtree(char_dir)


def test_config_to_dict():
    """序列化：ActorConfig → dict 完整性。"""
    config = ActorConfig(
        identity=IdentityConfig(
            system_prompt="测试用助手。",
            title="测试员",
            traits="擅长边界条件",
        ),
        runtime=RuntimeConfig(
            provider="deepseek",
            model="v4",
            temperature=0.5,
        ),
    )
    d = config_to_dict(config)
    assert d["identity"]["system_prompt"] == "测试用助手。", f"identity 序列化不完整: {d['identity']}"
    assert d["identity"]["title"] == "测试员"
    assert d["identity"]["traits"] == "擅长边界条件"
    assert d["runtime"]["provider"] == "deepseek"
    assert d["runtime"]["model"] == "v4"
    assert d["runtime"]["temperature"] == 0.5
    print("  [OK] config_to_dict: 序列化正常")


def test_config_roundtrip():
    """往返：ActorConfig → dict → ActorConfig 无损。"""
    original = ActorConfig(
        identity=IdentityConfig(
            system_prompt="往返测试。",
            title="测试者",
            traits="细致",
            max_iterations=5,
        ),
        runtime=RuntimeConfig(
            provider="minimax",
            model="2.7",
            temperature=0.8,
            max_tokens=4096,
        ),
    )
    d = config_to_dict(original)
    restored = config_from_dict(d)
    assert restored.identity.system_prompt == original.identity.system_prompt
    assert restored.identity.title == original.identity.title
    assert restored.identity.traits == original.identity.traits
    assert restored.identity.max_iterations == original.identity.max_iterations
    assert restored.runtime.provider == original.runtime.provider
    assert restored.runtime.model == original.runtime.model
    assert restored.runtime.temperature == original.runtime.temperature
    print("  [OK] config_roundtrip: dict 往返无损")


def test_save_and_load():
    """真实 I/O：save → 文件检查 → load。"""
    config = ActorConfig(
        identity=IdentityConfig(
            system_prompt="I/O 测试助手。",
            title="文件员",
        ),
        runtime=RuntimeConfig(
            provider="dashscope",
            model="qwq",
        ),
    )
    save_config(config, TEST_CHAR)

    # 验证文件确实落盘（ensure_dirs 会为新建角色生成时间戳前缀目录）
    char_root = Path(PROJECT_ROOT) / "character_data"
    found = list(char_root.glob(f"*-{TEST_CHAR}/config.json"))
    if not found:
        found = list(char_root.glob(f"{TEST_CHAR}/config.json"))
    assert found, f"配置文件未落盘，在 {char_root} 中查找"
    config_path = found[0]
    assert config_path.exists(), f"配置文件不存在: {config_path}"

    # 验证 JSON 内容可读
    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["identity"]["title"] == "文件员"
    assert raw["runtime"]["provider"] == "dashscope"
    print(f"  [OK] save_config: 文件已落盘 → {config_path}")

    # 加载回来
    loaded = load_config(TEST_CHAR)
    assert loaded.identity.title == "文件员"
    assert loaded.identity.system_prompt == "I/O 测试助手。"
    assert loaded.runtime.provider == "dashscope"
    assert loaded.runtime.model == "qwq"
    print("  [OK] load_config: 从文件加载正确")


def test_init_config_new():
    """init_config: 角色不存在时创建默认配置。"""
    name = "_test_init_new"
    char_root = Path(PROJECT_ROOT) / "character_data"
    # 清理旧数据
    for pattern in [f"*-{name}", name]:
        for d in char_root.glob(pattern):
            if d.is_dir():
                shutil.rmtree(d)

    try:
        config = init_config(name)
        assert config.identity.system_prompt == "智能体项目测试助手。", \
            f"默认 system_prompt 不符: {config.identity.system_prompt}"
        assert config.runtime.provider == "minimax"
        assert config.runtime.model == "2.7"

        # 确认文件已创建（init_config 对新建角色使用时间戳前缀目录）
        found = list(char_root.glob(f"*-{name}/config.json"))
        if not found:
            found = list(char_root.glob(f"{name}/config.json"))
        assert found, f"init_config 应创建配置目录: {char_root}/*-{name}"
        print(f"  [OK] init_config: 新角色默认配置已创建 -> {found[0]}")
    finally:
        for pattern in [f"*-{name}", name]:
            for d in char_root.glob(pattern):
                if d.is_dir():
                    shutil.rmtree(d)


def test_init_config_existing():
    """init_config: 角色已存在时返回已有配置。"""
    name = "_test_init_exist"
    char_root = Path(PROJECT_ROOT) / "character_data"
    for pattern in [f"*-{name}", name]:
        for d in char_root.glob(pattern):
            if d.is_dir():
                shutil.rmtree(d)

    try:
        # 先创建自定义配置（先用 registry 创建目录）
        config = ActorConfig(
            identity=IdentityConfig(system_prompt="已存在的配置。", title="老角色"),
            runtime=RuntimeConfig(model="custom-model"),
        )
        save_config(config, name)

        # 再 init_config
        loaded = init_config(name)
        assert loaded.identity.title == "老角色", "不应覆盖已有配置"
        assert loaded.runtime.model == "custom-model"
        print("  [OK] init_config: 已有角色不覆盖")
    finally:
        for pattern in [f"*-{name}", name]:
            for d in char_root.glob(pattern):
                if d.is_dir():
                    shutil.rmtree(d)


if __name__ == "__main__":
    setup()
    try:
        test_config_to_dict()
        test_config_roundtrip()
        test_save_and_load()
        test_init_config_new()
        test_init_config_existing()
        print("\n" + "="*50)
        print("  [OK] 配置读写: 全部 5 项测试通过")
        print("="*50)
    finally:
        teardown()

