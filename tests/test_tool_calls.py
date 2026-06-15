"""
test_tool_calls.py — 自指涉工具真实调用测试

验证 6 个角色管理工具的实际执行结果：
  - list_characters: 无参调用，返回角色列表
  - create_character: 创建新角色 + 重复创建报错
  - update_identity: 修改身份参数
  - update_runtime: 修改运行时参数
  - summarize_conversation: 压缩历史
  - send_to_character: 跨角色通信（注册/参数校验，不实际调 LLM）

按测试经验文档：使用系统真实机制，验证文件落盘。
"""
import sys
import os
import json
import asyncio
import shutil
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from tool.builtin import tools, set_actor, _BUILTIN_HANDLERS
from character.registry import registry
from character import (
    get_history_path, get_config_path, get_character_dir, ensure_dirs,
)


TEST_CHAR = "_test_tool_char"

# ── 辅助 ──

def setup():
    set_actor(TEST_CHAR)
    if registry.exists(TEST_CHAR):
        registry.delete(TEST_CHAR)


def teardown():
    if registry.exists(TEST_CHAR):
        registry.delete(TEST_CHAR)


def print_section(title: str):
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print(f"{'─'*50}")


# ── 测试 1: list_characters ──

def test_list_characters():
    """无参调用，应返回角色列表字符串。"""
    result = _BUILTIN_HANDLERS["list_characters"]()
    assert "[OK]" not in result, "list_characters 不应返回 [OK]"  # 实际格式不同
    assert "角色" in result or "暂无" in result or "共" in result, \
        f"应有角色列表: {result[:100]}"
    print(f"  [OK] list_characters: {len(result.split(chr(10)))} 行输出")


# ── 测试 2: create_character ──

async def test_create_character():
    """创建新角色，验证文件和返回值。"""
    # 创建
    result = await _BUILTIN_HANDLERS["create_character"]({
        "name": TEST_CHAR,
        "system_prompt": "你是测试角色，回复简洁。",
        "title": "工具测试员",
        "traits": "用于工具集成测试",
        "model": "v4-pro",
        "temperature": 0.5,
    })
    assert "[OK]" in result, f"创建应成功: {result[:200]}"
    assert "工具测试员" in result
    print(f"  [OK] create_character: {result[:120].strip()}")

    # 验证文件落盘
    char_dir = get_character_dir(TEST_CHAR)
    assert char_dir.exists(), f"角色目录应存在: {char_dir}"
    config_path = get_config_path(TEST_CHAR)
    assert config_path.exists(), f"config.json 应存在: {config_path}"

    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["identity"]["title"] == "工具测试员"
    assert cfg["identity"]["system_prompt"] == "你是测试角色，回复简洁。"
    assert cfg["runtime"]["model"] == "v4-pro"
    assert cfg["runtime"]["temperature"] == 0.5
    print(f"  [OK] config.json 内容验证: title={cfg['identity']['title']}, model={cfg['runtime']['model']}")

    # 重复创建应报错
    dup = await _BUILTIN_HANDLERS["create_character"]({
        "name": TEST_CHAR,
        "system_prompt": "重复角色",
    })
    assert "[Error]" in dup, f"重复创建应报错: {dup[:100]}"
    assert "已存在" in dup
    print(f"  [OK] 重复创建 -> 正确报错: {dup.strip()[:100]}")


# ── 测试 3: update_identity ──

def test_update_identity():
    """修改身份参数，验证持久化。"""
    result = _BUILTIN_HANDLERS["update_identity"]({
        "title": "升级版测试员",
        "traits": "已通过工具测试验证",
    })
    assert "[OK]" in result, f"更新应成功: {result}"
    assert "title=" in result
    assert "traits" in result
    print(f"  [OK] update_identity: {result.strip()}")

    # 验证持久化
    config_path = get_config_path(TEST_CHAR)
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["identity"]["title"] == "升级版测试员"
    assert cfg["identity"]["traits"] == "已通过工具测试验证"
    print(f"  [OK] 持久化验证: title={cfg['identity']['title']}")

    # system_prompt 更新
    result2 = _BUILTIN_HANDLERS["update_identity"]({
        "system_prompt": "你是更新后的测试角色。",
    })
    assert "[OK]" in result2
    with open(config_path, encoding="utf-8") as f:
        cfg2 = json.load(f)
    assert cfg2["identity"]["system_prompt"] == "你是更新后的测试角色。"
    print(f"  [OK] system_prompt 更新: {cfg2['identity']['system_prompt'][:30]}...")


# ── 测试 4: update_runtime ──

def test_update_runtime():
    """修改运行时参数（不触发模型切换）。"""
    result = _BUILTIN_HANDLERS["update_runtime"]({
        "temperature": 0.8,
        "max_tokens": 2048,
        "thinking_mode": "auto",
    })
    assert "[OK]" in result, f"更新应成功: {result}"
    assert "temperature=0.8" in result
    assert "max_tokens=2048" in result
    print(f"  [OK] update_runtime (no switch): {result.strip()}")

    # 验证持久化
    config_path = get_config_path(TEST_CHAR)
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["runtime"]["temperature"] == 0.8
    assert cfg["runtime"]["max_tokens"] == 2048
    print(f"  [OK] 运行时持久化: temp={cfg['runtime']['temperature']}, max_tokens={cfg['runtime']['max_tokens']}")

    # temperature 边界值
    result2 = _BUILTIN_HANDLERS["update_runtime"]({"temperature": 3.0})
    assert "[Error]" in result2, f"超范围应报错: {result2}"
    print(f"  [OK] temperature=3.0 -> 边界校验: {result2.strip()}")

    result3 = _BUILTIN_HANDLERS["update_runtime"]({"temperature": -1})
    assert "[Error]" in result3, f"负数应报错: {result3}"
    print(f"  [OK] temperature=-1 -> 边界校验: {result3.strip()}")

    # 无变更
    result4 = _BUILTIN_HANDLERS["update_runtime"]({})
    assert "no changes" in result4.lower(), f"空参数应提示无变更: {result4}"
    print(f"  [OK] 空参数: {result4.strip()}")


# ── 测试 5: summarize_conversation ──

async def test_summarize_conversation():
    """注入历史消息后调用摘要工具。"""
    # 先造一些历史消息
    from character.history import History
    hist = History(str(get_history_path(TEST_CHAR))).load()
    for i in range(15):
        hist.append_pair(f"第{i}轮测试问题，内容是些比较长的文本内容用于测试摘要功能", f"第{i}轮测试回复，回答内容也是比较长的文本")
    hist.save()
    print(f"  [OK] 注入 {len(hist.messages)} 条测试历史")

    # 调用摘要
    result = await _BUILTIN_HANDLERS["summarize_conversation"]({
        "keep_recent_turns": 4,
        "topic": "测试摘要功能",
    })
    assert "[摘要已保存]" in result, f"摘要应保存: {result[:200]}"
    assert "测试摘要功能" in result
    print(f"  [OK] summarize_conversation: {result[:200].strip()}")

    # 验证 L1 摘要文件落盘
    from character import get_summaries_dir
    summaries_dir = get_summaries_dir(TEST_CHAR)
    l1_files = list(summaries_dir.glob("*.json")) if summaries_dir.exists() else []
    assert len(l1_files) > 0, f"L1 摘要文件应存在: {summaries_dir}"
    print(f"  [OK] L1 摘要文件落盘: {l1_files[0].name}")

    with open(l1_files[0], encoding="utf-8") as f:
        summary_data = json.load(f)
    assert "summary" in summary_data, "应包含 summary 字段"
    assert len(summary_data["summary"]) > 0
    seg_topic = summary_data["summary"][0].get("topic", "")
    assert "测试摘要功能" in seg_topic
    print(f"  [OK] L1 摘要内容: topic={seg_topic}, messages={summary_data.get('message_count', '?')}")

    # 历史太少时不应压缩
    result2 = await _BUILTIN_HANDLERS["summarize_conversation"]({
        "keep_recent_turns": 100,
    })
    assert "无需压缩" in result2 or "仅" in result2, f"历史不足应跳过: {result2[:100]}"
    print(f"  [OK] 历史不足跳过: {result2.strip()}")


# ── 测试 6: send_to_character 注册验证 ──

async def test_send_to_character_handler():
    """验证 send_to_character 的 handler 已注册且参数正确。"""
    assert "send_to_character" in _BUILTIN_HANDLERS, "handler 应注册"

    # 验证参数校验（收件人不存在）
    result = await _BUILTIN_HANDLERS["send_to_character"]({
        "recipient": "__nonexistent_character__",
        "message": "测试消息",
    })
    assert "[Error]" in result
    assert "不存在" in result
    print(f"  [OK] send_to_character 参数校验: {result.strip()}")


# ── 测试 7: 文件工具调用 ──

def test_file_tool_calls():
    """快速验证文件工具可正常执行。"""
    from tool.builtin import _handle_bash

    # bash - echo
    result = _BUILTIN_HANDLERS["bash"]({"command": "echo hello_tool_test"})
    assert "hello_tool_test" in result, f"bash 应正常: {result[:100]}"
    print(f"  [OK] bash echo: {result.strip()}")


# ── 主入口 ──

async def main():
    setup()
    try:
        print_section("测试 1: list_characters")
        test_list_characters()

        print_section("测试 2: create_character")
        await test_create_character()

        print_section("测试 3: update_identity")
        test_update_identity()

        print_section("测试 4: update_runtime")
        test_update_runtime()

        print_section("测试 5: summarize_conversation")
        await test_summarize_conversation()

        print_section("测试 6: send_to_character 校验")
        await test_send_to_character_handler()

        print_section("测试 7: 文件工具 (bash)")
        test_file_tool_calls()

        print(f"\n{'='*50}")
        print(f"  [OK] 工具调用: 全部 7 项测试通过")
        print(f"{'='*50}")
    finally:
        teardown()


if __name__ == "__main__":
    asyncio.run(main())
