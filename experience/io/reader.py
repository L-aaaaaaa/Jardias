"""reader.py — experience.md 读取模块。

从 experience.md 读取并解析出 4 个 message 块。

对外接口：
    - read_block0/1/2/3(character_name) -> str：读单个块
    - read_all(character_name) -> dict[int, str]：一次性读 4 块
    - load_experience(character_name)：read_all 的兼容别名
    - load_all_l1(character_name) -> list[L1Summary]：读所有 L1 摘要
    - load_compression_log(character_name) -> list[dict]：读压缩记录表

内部辅助：
    - _parse_user_input_from_message3：从块3 文本反解出 {timestamp, role, text}
    - _CHARACTER_NAME_CACHE：dump 操作查 compression_log 时需要的全局缓存
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from character import get_character_dir, get_summaries_dir, get_compression_log_path
from data_shape import L1Summary


_BLOCK_PATTERN = re.compile(
    r"<!--\s*message(\d+)\s*-->\s*\n(.*?)(?=\n<!--\s*message|$)",
    re.DOTALL
)

# 全局缓存：character_name -> path，dump 操作需要查 compression_log
_CHARACTER_NAME_CACHE: dict[str, str] = {}


def _read_file_blocks(character_name: str) -> dict[int, str]:
    """从 experience.md 读取 4 块原始文本（含块3 的 _dump_written_len 剥离）。

    返回:
        {0: message0_str, 1: message1_str, 2: message2_str, 3: message3_str}
        如果某块不存在，返回空字符串。
    """
    path = get_character_dir(character_name) / "experience.md"
    if not path.exists():
        return {0: "", 1: "", 2: "", 3: ""}

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    result: dict[int, str] = {0: "", 1: "", 2: "", 3: ""}

    # 按 <!--_msg_N_--> 标记分割：['', content0, content1, content2, content3, trailing]
    parts = re.split(r"<!--_msg_\d+_-->", content)
    # parts[0] 是文件开头（通常为空），parts[1-4] 对应 message0-3
    for i in range(4):
        if i + 1 < len(parts):
            result[i] = parts[i + 1].strip()

    # 从 blocks[3] 剥离内部追踪元数据（不发给 LLM）
    # 注意：不要对 blocks[3] 做完整 strip，否则前面的 \n 会丢失导致元数据行剥离不掉
    # 只做 lstrip 和去除尾部元数据注释
    raw_m3 = parts[4] if len(parts) > 4 else ""
    m3_stripped = raw_m3.lstrip("\n")
    m3_clean = re.sub(r"^<!-- _dump_written_len=\d+ -->\s*", "", m3_stripped)
    result[3] = m3_clean.rstrip()

    return result


def read_block0(character_name: str) -> str:
    """读块0（角色身份 / 系统提示）。"""
    return _read_file_blocks(character_name)[0]


def read_block1(character_name: str) -> str:
    """读块1（动态状态）。"""
    return _read_file_blocks(character_name)[1]


def read_block2(character_name: str) -> str:
    """读块2（历史：摘要 + 近期对话原文）。"""
    return _read_file_blocks(character_name)[2]


def read_block3(character_name: str) -> str:
    """读块3（本次用户消息）。"""
    return _read_file_blocks(character_name)[3]


def read_all(character_name: str) -> dict[int, str]:
    """一次性读 4 块。等价于 load_experience。"""
    return _read_file_blocks(character_name)


# ── 兼容旧 API（所有调用方不动） ──
def load_experience(character_name: str) -> dict[int, str]:
    """从 experience.md 读取并解析出 4 个 message 块（read_all 的兼容别名）。"""
    return read_all(character_name)


def _parse_user_input_from_message3(message3: str) -> dict | None:
    """从 message3 解析出用户输入和时间。

    message3 格式：
    ## 本次用户消息
    ### [2026-07-08 16:01:00] user

    ```text
    用户输入内容
    ```
    """
    if not message3:
        return None
    ts_match = re.search(r"###\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})]\s*(\w+)", message3)
    if not ts_match:
        return None
    timestamp = ts_match.group(1)
    role = ts_match.group(2)

    # 提取 ```text ``` 块内容
    text_match = re.search(r"```text\s*\n(.*?)\n```", message3, re.DOTALL)
    text = text_match.group(1).strip() if text_match else ""

    return {"timestamp": timestamp, "role": role, "text": text}


def _infer_character_name(blocks: dict[int, str]) -> str | None:
    """从 blocks 中推测当前角色名。优先用全局缓存。"""
    # 全局缓存：character_name -> path，dump 操作需要查 compression_log
    return _CHARACTER_NAME_CACHE.get("current")


# ═══════════════════════════════════════════════════════════════════
# L1 摘要读写（summaries/L1/{id}.json）
# ═══════════════════════════════════════════════════════════════════

def load_all_l1(character_name: str) -> list:
    """加载所有 L1 摘要（按 ID 排序）。"""
    l1_dir = get_summaries_dir(character_name)
    if not l1_dir.exists():
        return []
    summaries: list = []
    for f in sorted(l1_dir.glob("*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                summaries.append(l1summary_from_dict(json.load(fh)))
        except (json.JSONDecodeError, OSError):
            pass
    return summaries


# ═══════════════════════════════════════════════════════════════════
# compression_log.json 读写（summaries/compression_log.json）
# ═══════════════════════════════════════════════════════════════════

def load_compression_log(character_name: str) -> list[dict]:
    """读取压缩记录表，返回所有压缩事件列表（按时间顺序）。"""
    path = get_compression_log_path(character_name)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


# ═══════════════════════════════════════════════════════════════════
# L1Summary ↔ dict 序列化（与读写同一处，writer 也会用 from_dict）
# ═══════════════════════════════════════════════════════════════════

def l1summary_from_dict(d: dict):
    """从 dict 还原 L1Summary。"""
    inst = L1Summary(
        id=d.get("id", ""),
        start_time=d.get("start_time", ""),
        end_time=d.get("end_time", ""),
        message_count=d.get("message_count", 0),
        user_turns=d.get("user_turns", 0),
        topic=d.get("topic", ""),
        detail=d.get("detail", ""),
        key_events=d.get("key_events", []),
        summary=d.get("summary", []),
        topic_label=d.get("topic_label", ""),
        people=d.get("people", []),
        msg_indices=tuple(d.get("msg_indices", [0, 0])),
        source=d.get("source", "auto"),
        time_ranges=d.get("time_ranges", []) or [],
        range_msg_indices=d.get("range_msg_indices", []) or [],
    )
    if not inst.summary and inst.topic:
        inst.summary = [{"from": 0, "to": inst.user_turns,
                         "topic": inst.topic, "detail": inst.detail}]
    return inst


__all__ = [
    # 新接口
    'read_block0', 'read_block1', 'read_block2', 'read_block3', 'read_all',
    # L1 / compression_log IO
    'load_all_l1', 'load_compression_log',
    # L1Summary 序列化
    'l1summary_from_dict',
    # 兼容
    'load_experience',
    # 内部
    '_parse_user_input_from_message3', '_CHARACTER_NAME_CACHE',
]
