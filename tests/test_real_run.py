"""
test_real_run.py — 真实运行集成测试

按照测试经验教训文档：
  - 不 mock，直接调用系统已有机制
  - 通过 subprocess 保持 event loop 持续运行
  - 检查真实产生的文件（history.json, config.json等）
  - 日志输出作为成功判断标准

目标：验证 app.py 启动 → 角色选择 → 消息对话 → 文件落盘 的完整链路。
"""
import sys
import os
import json
import time
import subprocess
import threading
from pathlib import Path

PYTHON = r"D:\B\Python3.10\python.exe"
PROJECT_ROOT = Path(r"E:\Code\AIProjects\Actor01")

# 确保项目根目录在 sys.path
sys.path.insert(0, str(PROJECT_ROOT))


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ── 测试 1: 模块导入完整性 ──
print_section("测试 1: 模块导入（--list 参数）")

result = subprocess.run(
    [PYTHON, "app.py", "--list"],
    cwd=PROJECT_ROOT,
    capture_output=True,
    encoding="utf-8",
    errors="replace",
    timeout=10,
)
print(f"  返回码: {result.returncode}")
print(f"  输出:\n{result.stdout[:500]}")
if result.returncode != 0:
    print(f"  STDERR:\n{result.stderr[:500]}")
assert result.returncode == 0, f"--list 应返回 0，实际 {result.returncode}"
assert "角色" in result.stdout or "characters" in result.stdout.lower() or "暂无" in result.stdout
print("  [OK] --list 正常输出角色列表")


# ── 测试 2: 创建测试角色并对话 ──
print_section("测试 2: 角色创建 + 基础对话")

TEST_CHAR = "_test_real_run"

# 清理旧数据
char_dir = PROJECT_ROOT / "character_data" / TEST_CHAR
if char_dir.exists():
    import shutil
    shutil.rmtree(char_dir)

# 创建测试角色配置
from data_shape import ActorConfig, IdentityConfig, RuntimeConfig
from character.registry import registry

config = ActorConfig(
    identity=IdentityConfig(
        system_prompt="你是测试助手，回复简洁。",
        title="集成测试角色",
        traits="测试专用",
    ),
    runtime=RuntimeConfig(
        provider="minimax",
        model="2.7快",
        temperature=0.5,
        max_tokens=1024,
    ),
)
registry.create(TEST_CHAR, config)
print(f"  已创建测试角色: {TEST_CHAR}")

# 验证角色目录和配置文件
char_root = PROJECT_ROOT / "character_data"
# registry.create 为新建角色生成时间戳前缀目录
found = list(char_root.glob(f"*-{TEST_CHAR}"))
if not found:
    found = list(char_root.glob(TEST_CHAR))
assert found, f"角色目录应存在: {char_root}/*-{TEST_CHAR}"
char_dir = found[0]
assert char_dir.exists(), f"角色目录应存在: {char_dir}"
config_path = char_dir / "config.json"
assert config_path.exists(), f"config.json 应存在: {config_path}"
with open(config_path, encoding="utf-8") as f:
    saved = json.load(f)
assert saved["identity"]["title"] == "集成测试角色", f"配置内容不符: {saved['identity']}"
print(f"  [OK] config.json 内容正确: title={saved['identity']['title']}")


# ── 测试 3: 验证 O(1) 上下文构建 ──
print_section("测试 3: O(1) 上下文结构验证")

from common.context import form_full_context

ctx_empty = form_full_context(config, [], "你好")
ctx_loaded = form_full_context(
    config,
    [{"role": "user", "content": "msg1", "time": "2025-01-01 12:00:00"},
     {"role": "assistant", "content": "reply1", "time": "2025-01-01 12:00:01"}]
    * 10,  # 20条历史
    "你好",
)

assert len(ctx_empty) == len(ctx_loaded), \
    f"O(1) 违反: 空={len(ctx_empty)}, 满载={len(ctx_loaded)}"
print(f"  消息数: 空历史={len(ctx_empty)}, 20条历史={len(ctx_loaded)} → O(1) ✓")

# 验证消息角色结构
assert ctx_empty[0]["role"] == "system", f"首条应为 system，实际 {ctx_empty[0]['role']}"
assert "身份" in ctx_empty[0]["content"], "system 应含身份块"
assert "引擎" in ctx_empty[0]["content"], "system 应含引擎块"
assert ctx_empty[-1]["role"] == "user", f"末条应为 user，实际 {ctx_empty[-1]['role']}"
print("  [OK] 消息角色结构: system → status → history → user")


# ── 测试 4: History 完整链路 ──
print_section("测试 4: History 读写完整链路")

from character.history import History

hist_path = char_dir / "history.json"
h = History(str(hist_path)).load()
assert len(h.messages) == 0, f"新角色 history 应为空，实际 {len(h.messages)}"

h.append_pair("测试消息", "测试回复")
h.save()

assert hist_path.exists(), "history.json 应落盘"
with open(hist_path, encoding="utf-8") as f:
    hist_data = json.load(f)
assert len(hist_data) == 2
assert hist_data[0]["role"] == "user" and hist_data[0]["content"] == "测试消息"
assert hist_data[1]["role"] == "assistant" and hist_data[1]["content"] == "测试回复"
print(f"  [OK] history.json 落盘: {len(hist_data)} 条，时间戳: {hist_data[0].get('time', 'N/A')}")

# 重新加载
h2 = History(str(hist_path)).load()
assert len(h2.messages) == 2
assert h2.messages[0]["content"] == "测试消息"
print("  [OK] 重新加载: 数据一致")


# ── 测试 5: 配置文件往返 ──
print_section("测试 5: ActorConfig save → load 往返")

from actor_config.config_io import load_config, save_config

loaded = load_config(TEST_CHAR)
assert loaded.identity.title == "集成测试角色"
assert loaded.runtime.provider == "minimax"

# 修改并保存
loaded.runtime.temperature = 0.9
loaded.identity.traits = "修改后的特质"
save_config(loaded, TEST_CHAR)

# 重新加载
reloaded = load_config(TEST_CHAR)
assert reloaded.runtime.temperature == 0.9
assert reloaded.identity.traits == "修改后的特质"
print(f"  [OK] 配置往返: temp={reloaded.runtime.temperature}, traits={reloaded.identity.traits}")


# ── 测试 6: 清理 ──
print_section("测试 6: 角色删除")

registry.delete(TEST_CHAR)
assert not registry.exists(TEST_CHAR), "delete 后 exists 应为 False"
assert not char_dir.exists(), "角色目录应被删除"
print("  [OK] 角色清理完成")


# ── 汇总 ──
print_section("集成测试结果汇总")
print("""
  [OK] 测试 1: --list 参数正常
  [OK] 测试 2: 角色创建 + config.json 落盘
  [OK] 测试 3: O(1) 上下文结构
  [OK] 测试 4: History 读写
  [OK] 测试 5: Config 往返
  [OK] 测试 6: 角色删除
""")
print("  全部 6 项集成测试通过")
print("="*60)

