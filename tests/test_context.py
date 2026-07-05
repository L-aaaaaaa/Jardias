"""
test_context.py — 上下文构建 O(1) 固定结构测试

验证 Actor01 最核心的架构特征：form_full_context 永远产出固定数量的消息。
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from data_shape import ActorConfig, RoleConfig, IPURuntime
from common.context import (
    form_full_context,
    build_system_message,
    build_config_context,
    strip_context_wrapper,
    _build_recent_history,
)


def make_test_config():
    return ActorConfig(
        identity=RoleConfig(
            system_prompt="测试用系统提示词。",
            title="测试角色",
            traits="测试特质",
        ),
        runtime=IPURuntime(
            provider="minimax",
            ipu="2.7快",
            temperature=1.0,
            max_icp=4096,
        ),
    )


def test_fixed_message_count():
    """核心断言：无论历史多长，消息数永远是 N（固定结构）。"""
    config = make_test_config()

    # 空历史
    ctx0 = form_full_context(config, [], "你好")
    msg_count_0 = len(ctx0)
    print(f"  空历史 → {msg_count_0} 条消息")

    # 10 轮历史（20 条消息）
    history = []
    for i in range(20):
        history.append({"role": "user" if i % 2 == 0 else "assistant",
                        "content": f"消息{i}", "time": "2025-01-01 12:00:00"})
    ctx_n = form_full_context(config, history, "你好")
    msg_count_n = len(ctx_n)

    # 关键：O(1) 固定结构
    assert msg_count_0 == msg_count_n, \
        f"消息数应固定，但空历史={msg_count_0}，多历史={msg_count_n}"
    print(f"  20条历史 → {msg_count_n} 条消息")
    print(f"  [OK] O(1) 固定消息结构: 始终 {msg_count_0} 条")


def test_message_structure():
    """验证消息结构：system + status + history + user。"""
    config = make_test_config()
    ctx = form_full_context(config, [], "你好啊")

    assert len(ctx) >= 3, f"至少 3 条消息，实际 {len(ctx)}"
    assert ctx[0]["role"] == "system", f"消息[0] 应为 system，实际 {ctx[0]['role']}"
    assert ctx[-1]["role"] == "user", f"最后一条应为 user，实际 {ctx[-1]['role']}"

    # system 消息应包含身份和引擎信息
    sys_content = ctx[0]["content"]
    assert "身份" in sys_content, "system 消息应含身份块"
    assert "引擎" in sys_content, "system 消息应含引擎块"

    # 最后一条 user 消息包含用户输入
    last_content = ctx[-1]["content"]
    assert "你好啊" in str(last_content), f"用户消息应含原始输入: {last_content[:100]}"

    # 中间消息包含历史块
    combined = " ".join(str(m["content"]) for m in ctx[1:-1])
    assert "历史" in combined or "状态" in combined, "应有历史或状态块"

    print("  [OK] 消息结构: system + status/history + user 格式正确")


def test_build_system_message():
    """system 消息内容完整性。"""
    config = make_test_config()
    msg = build_system_message(config, "test_char")

    assert msg["role"] == "system"
    content = msg["content"]
    assert "# 系统提示词" in content
    assert "## 身份" in content
    assert "测试用系统提示词" in content
    assert "test_char" in content
    assert "## 引擎" in content or "引擎" in content
    print("  [OK] build_system_message: 身份+引擎完整")


def test_build_config_context():
    """引擎配置上下文包含关键信息。"""
    config = make_test_config()
    ctx = build_config_context(config)

    assert "minimax" in ctx.lower()
    assert "当前配置" in ctx
    assert "运行环境" in ctx
    print("  [OK] build_config_context: 引擎+环境信息完整")


def test_strip_context_wrapper():
    """strip 函数正确提取被封装的原始消息。"""
    wrapped = (
        "## 本次用户消息\n\n"
        "### [2025-01-01 12:00:00] user:\n\n"
        "```text\n"
        "原始消息内容\n"
        "```"
    )
    stripped = strip_context_wrapper(wrapped)
    assert stripped == "原始消息内容", f"剥离后应为原始消息，实际: {stripped}"

    # 空消息
    assert strip_context_wrapper("") == ""
    # 不匹配的消息原样返回
    assert strip_context_wrapper("普通消息") == "普通消息"
    print("  [OK] strip_context_wrapper: 正确提取原始内容")


def test_build_recent_history():
    """近期历史格式化。"""
    # 空历史
    empty = _build_recent_history([])
    assert "暂无对话记录" in empty, f"空历史应有提示: {empty[:50]}"

    # 有历史
    msgs = [
        {"role": "user", "content": "提问1", "time": "2025-01-01 12:00:00"},
        {"role": "assistant", "content": "回答1", "time": "2025-01-01 12:00:01"},
    ]
    recent = _build_recent_history(msgs)
    assert "近期对话原文" in recent
    assert "提问1" in recent
    assert "回答1" in recent
    print("  [OK] _build_recent_history: 格式正确")


if __name__ == "__main__":
    test_fixed_message_count()
    test_message_structure()
    test_build_system_message()
    test_build_config_context()
    test_strip_context_wrapper()
    test_build_recent_history()
    print("\n" + "="*50)
    print("  [OK] 上下文构建: 全部 6 项测试通过")
    print("="*50)

