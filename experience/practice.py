"""practice.py — 从 experience 构建 LLM messages 模块。

提供 build_context_from_experience 和初始化相关函数。
"""
from __future__ import annotations

import re
from datetime import datetime

from .reader import load_experience, _parse_user_input_from_message3


def _flatten_content(content) -> str:
    """将 message content 规范化为字符串。"""
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content) if content else ""


def build_context_from_experience(
    config,
    character_name: str,
    user_input: str,
    image_url: str | None = None,
    switch_note: str | None = None,
    round_context: str = "",
) -> list[dict]:
    """从 experience.md 构建发送给模型的 messages。

    固定结构：[system, state, history, user]
    """
    from common.context import build_system_message
    from .icp_cost import build_round_context as _build_round_context

    blocks = load_experience(character_name)

    # message0: 系统提示词
    system_msg = build_system_message(config, character_name, switch_note)

    # message1: 状态
    if round_context:
        state_content = round_context
    else:
        state_content = blocks[1] or "（暂无状态数据）"
    state_msg = {"role": "user", "content": state_content}

    # message2: 历史
    history_content = blocks[2] if blocks[2] else "（暂无历史记录）"
    history_msg = {"role": "user", "content": f"# 历史\n\n{history_content}"}

    # message3: 本次用户消息（从 experience.md 的 message3 读取）
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t_sent_ms = int(datetime.now().timestamp() * 1000)

    # 优先使用传入的 user_input（更准确），其次从 blocks[3] 解析
    input_text = user_input
    if not input_text:
        parsed = _parse_user_input_from_message3(blocks[3])
        if parsed:
            input_text = parsed["text"]
            ts = parsed["timestamp"]
        else:
            ts = now
    else:
        ts = now

    if image_url:
        # 清理图片 URL
        clean_input = re.sub(
            r"(?:https?://[^\s]+|[A-Za-z]:[\\/][^\s]+)\.(?:png|jpg|jpeg|webp|gif|bmp)(?:\?[^\s]*)?\s*",
            "", user_input, flags=re.IGNORECASE
        ).strip() or user_input

        user_content = [
            {"type": "image_url", "image_url": {"url": image_url}},
            {
                "type": "text",
                "text": f"## 本次用户消息\n\n### [{ts}] user (t_sent={t_sent_ms}ms):\n\n```text\n{clean_input}\n```",
            },
        ]
    else:
        user_content = f"## 本次用户消息\n\n### [{ts}] user (t_sent={t_sent_ms}ms):\n\n```text\n{input_text}\n```"

    user_msg = {"role": "user", "content": user_content}

    return [system_msg, state_msg, history_msg, user_msg]


def init_experience(character_name: str, config) -> None:
    """为新角色创建默认的 experience.md。"""
    from common.context import build_system_message
    from .writer import _write_experience_file

    blocks: dict[int, str] = {0: "", 1: "", 2: "", 3: ""}

    # message0: 系统提示词
    system_msg = build_system_message(config, character_name)
    blocks[0] = _flatten_content(system_msg["content"])

    # message1: 状态（占位）
    blocks[1] = "# 状态\n\n（暂无状态数据）"

    # message2: 历史（占位）
    blocks[2] = ""

    # message3: 本次用户消息（占位）
    blocks[3] = ""

    from character import get_character_dir
    path = get_character_dir(character_name) / "experience.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_experience_file(path, blocks)


def sync_experience_system_block(config, character_name: str):
    """把当前 config.runtime 对应的 engine 信息同步写入 experience.md 的 blocks[0]。

    在 auto-switch（vision / fallback）修改 config.runtime 后调用，
    确保 experience.md 的 ## 引擎 段与 config.json 同步，
    角色重启后无需再次触发切换。

    不会改动 blocks[1-3]。
    """
    from common.context import build_system_message
    from .writer import _write_experience_file

    from character import get_character_dir
    path = get_character_dir(character_name) / "experience.md"
    if not path.exists():
        return  # 角色还未初始化 experience.md，跳过
    blocks = load_experience(character_name)
    system_msg = build_system_message(config, character_name)
    blocks[0] = _flatten_content(system_msg["content"])
    _write_experience_file(path, blocks)


__all__ = [
    'build_context_from_experience', 'init_experience', 'sync_experience_system_block'
]
