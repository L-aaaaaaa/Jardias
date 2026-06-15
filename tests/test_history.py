"""
test_history.py — 对话历史读写测试

验证 History 类的 load/save/append_pair 完整链路。
"""
import sys
import os
import json
import shutil
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from character.history import History

TEST_DIR = Path(PROJECT_ROOT) / "character_data" / "_test_history"
TEST_FILE = TEST_DIR / "history.json"


def setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    if TEST_FILE.exists():
        TEST_FILE.unlink()


def teardown():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)


def test_empty_load():
    """不存在的 history 文件 → 空 messages。"""
    h = History(str(TEST_FILE)).load()
    assert h.messages == [], "新 history 应为空列表"
    print("  [OK] empty load: 空文件 → 空列表")


def test_append_and_save():
    """append_pair → save → 文件内容验证。"""
    h = History(str(TEST_FILE)).load()
    assert len(h.messages) == 0

    h.append_pair("你好", "你好！有什么可以帮你的？")
    h.save()

    # 文件存在且有 2 条消息
    assert TEST_FILE.exists(), "save 后文件应存在"
    with open(TEST_FILE, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 2, f"应有 2 条消息，实际 {len(data)}"
    assert data[0]["role"] == "user"
    assert data[0]["content"] == "你好"
    assert data[1]["role"] == "assistant"
    assert data[1]["content"] == "你好！有什么可以帮你的？"
    # 验证有时间戳
    assert "time" in data[0]
    assert "time" in data[1]
    print("  [OK] append_pair + save: 2 条消息写入，带时间戳")


def test_load_existing():
    """加载已有 history 文件。"""
    h = History(str(TEST_FILE)).load()
    assert len(h.messages) == 2, f"应加载 2 条，实际 {len(h.messages)}"
    assert h.messages[0]["role"] == "user"
    assert h.messages[0]["content"] == "你好"
    print("  [OK] load: 加载已有文件内容正确")


def test_multiple_rounds():
    """多轮对话追加。"""
    h = History(str(TEST_FILE)).load()
    before = len(h.messages)

    h.append_pair("问题1", "回答1")
    h.append_pair("问题2", "回答2")
    h.append_pair("问题3", "回答3")
    h.save()

    assert len(h.messages) == before + 6, f"应增加 6 条，实际 {len(h.messages)}"
    # 重新加载验证持久化
    h2 = History(str(TEST_FILE)).load()
    assert len(h2.messages) == before + 6
    roles = [m["role"] for m in h2.messages]
    assert "user" in roles
    assert "assistant" in roles
    print(f"  [OK] multiple rounds: {len(h2.messages)} 条消息持久化正确")


def test_corrupted_json():
    """损坏的 JSON 文件 → 返回空 messages（不崩溃）。"""
    bad_path = TEST_DIR / "bad_history.json"
    bad_path.write_text("这不是合法的 JSON {{{", encoding="utf-8")
    h = History(str(bad_path)).load()
    assert h.messages == [], "损坏文件应返回空列表"
    print("  [OK] corrupted JSON: 优雅降级 → 空列表")


def test_chained_api():
    """append_pair 后 save 正常持久化。"""
    h = History(str(TEST_FILE)).load()
    assert isinstance(h, History)
    h.append_pair("链式", "测试")
    h.save()
    h3 = History(str(TEST_FILE)).load()
    assert any("链式" in m["content"] for m in h3.messages)
    print("  [OK] append + save: 数据持久化正常")


if __name__ == "__main__":
    setup()
    try:
        test_empty_load()
        test_append_and_save()
        test_load_existing()
        test_multiple_rounds()
        test_corrupted_json()
        test_chained_api()
        print("\n" + "="*50)
        print("  [OK] 对话历史: 全部 6 项测试通过")
        print("="*50)
    finally:
        teardown()

