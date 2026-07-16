"""writer.py — experience.md 写入模块。

提供按块的写入接口（write_block0/1/2/3 + write_block2_append/rewrite）。

对外接口（按块写入）：
    - write_block0(character_name, content)：写角色身份块
    - write_block1(character_name, content)：写动态状态块
    - write_block2_append(character_name, recent_text, **_compat_kwargs)：增量追加对话原文
    - write_block2_rewrite(character_name, summary_entries, recent_text, **_compat_kwargs)：
        重写块2（archive 用），summary_entries 是 dict 列表，自动序列化为 JSON
    - write_block3(character_name, user_input, timestamp)：写本次用户消息
    - clear_block3(character_name)：清空块3
    - save_l1(character_name, summary)：保存 L1 摘要 JSON
    - save_compression_log(character_name, records)：写压缩记录表（覆盖）
    - append_compression_record(character_name, source, l1_id, abs_from, abs_to, ...)：
        追加一条压缩记录，返回压缩事件 ID

内部辅助：
    - _write_experience_file(path, blocks)：底层 4 块统一写入
    - _merge_or_append_summary(blocks, entry)：合并/追加摘要 JSON
    - l1summary_to_dict(s)：L1Summary → dict（仅写入扩展字段，非空才写）
    - l1summary_to_context_string(s)：L1Summary → markdown code block（IO 视图）
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from character import get_character_dir, get_summaries_dir, get_compression_log_path
from common.logger import logger
from .reader import read_all, load_compression_log


# ═══════════════════════════════════════════════════════════════════
# 底层：把 4 块字典写入文件
# ═══════════════════════════════════════════════════════════════════

def _write_experience_file(path: Path, blocks: dict[int, str]) -> None:
    """将 4 个 block 写入 experience.md。

    重要：即使内容为空，也要保留块标记，否则 load_experience 解析时会丢失该块。
    标记格式为 <!--_msg_N_-->（含下划线和后缀），确保不易与内容中的任何文本混淆。
    写入前剥离内容中可能含有的内嵌块标记，防止解析错位。
    """
    # 格式：<!--_msg_0_-->（下划线前缀和后缀）
    MARKER_RE = re.compile(r"<!--_msg_\d+_-->")
    lines = []
    for idx in range(4):
        lines.append(f"<!--_msg_{idx}_-->\n")
        content = blocks.get(idx, "")
        # message3 是用户消息区,空内容时填入占位提示
        # "(等待用户输入)",否则 LLM 可能误以为该区是其他用途或缺失上下文。
        if idx == 3 and not content.strip():
            content = "（等待用户输入）"
        if content:
            # 剥离内嵌的块标记
            content = MARKER_RE.sub("", content)
            lines.append(content)
        lines.append("\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(lines))
        f.flush()


# ═══════════════════════════════════════════════════════════════════
# 块2 字符串模板（writer 层唯一保留字符串硬编码的位置）
# ═══════════════════════════════════════════════════════════════════

def _render_block2(summary_entries: list[dict], recent_text: str) -> str:
    """渲染块2 的完整内容（双骨架：## 摘要 + ## 近期对话原文）。

    summary_entries: 摘要 dict 列表（可空、可含合并后的完整数组）
    recent_text: 近期对话原文条目字符串
    """
    new_json = json.dumps(summary_entries, ensure_ascii=False, indent=2)
    return (
            "# 历史\n\n"
            f"## 摘要\n\n```json\n{new_json}\n```\n\n"
            "## 近期对话原文\n\n"
            + recent_text
    )


def _render_block3_user_input(user_input: str, timestamp: str) -> str:
    """渲染块3（本次用户消息）。"""
    return (
        f"## 本次用户消息\n\n"
        f"### [{timestamp}] user\n\n"
        f"```text\n{user_input}\n```"
    )


def _merge_or_append_summary(blocks: dict[int, str], summary_entry: dict) -> list[dict]:
    """追加一条 summary_entry 到 blocks[2] 的 ## 摘要 段，并把更新后的 entries
    就地写回 blocks[2]（仅替换 ```json ... ``` 块，保留其它内容）。

    关键设计：每条 summary 都有独立 ID，跨次归档互不合并。

    历史 bug（已修）：早期版本会按 msg_indices 重叠判定"扩展归档"并合并，
    但实际跨次手动归档的 ID 是独立的（如 T-083725、T-083730），重叠只意味着
    "两个不同话题覆盖同一段历史"——例如「你好」[0,7] + 「笑话」[4,5]，
    不应被合并成 1 条。合并会把前一条直接覆盖掉，造成信息丢失。
    """
    text2 = blocks[2] or ""
    m = re.search(r"```json\s*\n(.+?)\n```", text2, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(1))
        except Exception:
            arr = []
        before, after = text2[:m.start()], text2[m.end():]
    else:
        arr = []
        before, after = text2, ""

    # 兜底：旧版 entries 不是 dict（早期可能是字符串）——全部清理为 dict
    arr = [e for e in arr if isinstance(e, dict)]
    arr.append(summary_entry)

    # 按 msg_indices 起点升序排列（视觉上更稳定）
    arr.sort(key=lambda e: (e.get("msg_indices") or [0, 0])[0])

    new_json = "```json\n" + json.dumps(arr, ensure_ascii=False, indent=2) + "\n```"
    blocks[2] = before + new_json + after
    return arr


def _dump_meta_path(character_name: str) -> Path:
    return get_character_dir(character_name) / "_dump_meta.json"


def _load_dump_meta(character_name: str) -> dict:
    p = _dump_meta_path(character_name)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_dump_meta(character_name: str, meta: dict) -> None:
    _dump_meta_path(character_name).write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def _resolve_path(character_name: str) -> Path:
    path = get_character_dir(character_name) / "experience.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# ═══════════════════════════════════════════════════════════════════
# L1 摘要写入（summaries/L1/{id}.json）
# ═══════════════════════════════════════════════════════════════════

def l1summary_to_dict(s) -> dict:
    """L1Summary → dict。扩展字段仅在非默认值时写入（保持向后兼容）。"""
    _l1_ensure_summary(s)
    d = {
        "id": s.id,
        "start_time": s.start_time,
        "end_time": s.end_time,
        "message_count": s.message_count,
        "user_turns": s.user_turns,
        "summary": s.summary,
    }
    if s.topic:
        d["topic"] = s.topic
    if s.detail:
        d["detail"] = s.detail
    if s.key_events:
        d["key_events"] = list(s.key_events)
    if s.topic_label:
        d["topic_label"] = s.topic_label
    if s.people:
        d["people"] = s.people
    if s.msg_indices != (0, 0):
        d["msg_indices"] = list(s.msg_indices)
    if s.source and s.source != "auto":
        d["source"] = s.source
    if s.time_ranges:
        d["time_ranges"] = s.time_ranges
    if s.range_msg_indices:
        d["range_msg_indices"] = s.range_msg_indices
    return d


def _l1_ensure_summary(s):
    """兜底：旧版 L1Summary 只有 topic/detail 时，构造一个 summary 段。"""
    if not s.summary and s.topic:
        s.summary = [{"from": 0, "to": s.user_turns,
                      "topic": s.topic, "detail": s.detail}]


def l1summary_to_context_string(s) -> str:
    """L1Summary → markdown code block JSON（IO 视图）。"""
    _l1_ensure_summary(s)
    payload = {
        "id": s.id, "start_time": s.start_time, "end_time": s.end_time,
        "message_count": s.message_count, "user_turns": s.user_turns,
        "summary": s.summary,
    }
    return "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"


def save_l1(character_name: str, summary) -> Path:
    """保存 L1 摘要到文件，返回写入路径。"""
    l1_dir = get_summaries_dir(character_name)
    l1_dir.mkdir(parents=True, exist_ok=True)
    path = l1_dir / f"{summary.id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(l1summary_to_dict(summary), f, ensure_ascii=False, indent=2)
    return path


# ═══════════════════════════════════════════════════════════════════
# compression_log.json 写入（summaries/compression_log.json）
# ═══════════════════════════════════════════════════════════════════

def save_compression_log(character_name: str, records: list[dict]) -> None:
    """直接写回压缩记录表（覆盖）。"""
    path = get_compression_log_path(character_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def append_compression_record(character_name: str,
        source: str, l1_id: str,
        abs_from: int, abs_to: int,
        segment_index: int = 0, segment_count: int = 1) -> str:
    """追加一条压缩记录，返回压缩事件 ID。

    segment_index / segment_count 是聚合归档的扩展字段：
    单段归档时省略（默认 0/1），多区间归档时每个区间写一条记录。
    """
    records = load_compression_log(character_name)
    cid = f"C-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    record = {
        "id": cid,
        "source": source,
        "l1_id": l1_id,
        "abs_from": abs_from,
        "abs_to": abs_to,
        "compressed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if segment_count > 1:
        record["segment_index"] = segment_index
        record["segment_count"] = segment_count
    records.append(record)
    save_compression_log(character_name, records)
    return cid


# ═══════════════════════════════════════════════════════════════════
# 按块写入 API
# ═══════════════════════════════════════════════════════════════════

def write_block0(character_name: str, content: str) -> None:
    """写块0（角色身份 / 系统提示）。其它 3 块保留。"""
    path = _resolve_path(character_name)
    blocks = read_all(character_name)
    blocks[0] = content
    _write_experience_file(path, blocks)


def write_block1(character_name: str, content: str) -> None:
    """写块1（动态状态）。其它 3 块保留。"""
    path = _resolve_path(character_name)
    blocks = read_all(character_name)
    blocks[1] = content
    _write_experience_file(path, blocks)


def write_block3(character_name: str, user_input: str,
        timestamp: str | None = None) -> None:
    """写块3（本次用户消息）。其它 3 块保留。

    timestamp 不传时使用当前时间。
    """
    path = _resolve_path(character_name)
    blocks = read_all(character_name)
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    blocks[3] = _render_block3_user_input(user_input, ts)
    _write_experience_file(path, blocks)


def clear_block3(character_name: str) -> None:
    """清空块3。其它 3 块保留。"""
    path = _resolve_path(character_name)
    blocks = read_all(character_name)
    blocks[3] = ""
    _write_experience_file(path, blocks)


def write_block2_append(character_name: str, recent_text: str,
        meta: dict | None = None) -> dict:
    """增量追加到块2 的 ## 近期对话原文 段（dump 用）。

    参数：
        recent_text: 已渲染的对话条目字符串
        meta: 调用方传入的 _dump_meta（用于 written_len 跟踪；保留向后兼容）
              若传 None，则从磁盘读取现有 meta。

    返回：更新后的 _dump_meta 字典（供 caller 持久化）。

    行为：
        - 如果块2 还没有双骨架（## 摘要 + ## 近期对话原文），先创建空骨架再追加
        - 不会清空块3（块3 由 dump 调用方负责清空，与 archive 保持一致）
    """
    if meta is None:
        meta = _load_dump_meta(character_name)

    path = _resolve_path(character_name)
    blocks = read_all(character_name)

    has_existing = bool(blocks[2]) and "## 近期对话原文" in blocks[2]
    if not has_existing:
        blocks[2] = _render_block2([], recent_text)
    else:
        blocks[2] = blocks[2] + "\n\n" + recent_text

    _write_experience_file(path, blocks)

    # 轻量监控：块2 超过 50KB 阈值时打 WARNING，提示该归档了（阶段 5 短期方案）
    block2_size = len(blocks[2].encode("utf-8"))
    _BLOCK2_WARN_THRESHOLD = 50 * 1024  # 50KB
    if block2_size > _BLOCK2_WARN_THRESHOLD:
        logger.warning(
            f"  [exp] 块2 体积 {block2_size} bytes 超过阈值 "
            f"{_BLOCK2_WARN_THRESHOLD}，建议归档早期对话"
        )

    return meta


def write_block2_rewrite(character_name: str, summary_entry: dict,
        recent_text: str, *, physical_total: int | None = None,
        messages: list[dict] | None = None) -> dict:
    """重写块2（archive 用）。

    流程：
        1. 合并/追加 summary_entry 到现有 ## 摘要
        2. 渲染双骨架（## 摘要 + ## 近期对话原文）
        3. 写文件
        4. 更新 _dump_meta.written_len = physical_total（物理总消息数）

    参数：
        summary_entry: 本次归档的摘要 dict（会与现有 entries 合并或追加）
        recent_text: 已过滤的近期对话原文
        physical_total: history.json 的当前总消息数；为 None 时从磁盘读
        messages: 当 physical_total=None 时备用，从 messages 长度推断

    返回：更新后的 _dump_meta 字典
    """
    path = _resolve_path(character_name)
    blocks = read_all(character_name)

    merged = _merge_or_append_summary(blocks, summary_entry)
    blocks[2] = _render_block2(merged, recent_text)
    _write_experience_file(path, blocks)

    # 更新 _dump_meta.written_len
    meta = _load_dump_meta(character_name)
    if physical_total is None:
        if messages is not None:
            physical_total = len(messages)
        else:
            history_path = path.parent / "history.json"
            if history_path.exists():
                try:
                    physical_total = len(json.loads(history_path.read_text(encoding="utf-8")))
                except Exception:
                    physical_total = 0
            else:
                physical_total = 0
    meta["written_len"] = physical_total
    _save_dump_meta(character_name, meta)
    return meta


# 兼容层 update_experience 已删除——已迁移到 adapter/：
#   - update_experience("用户输入")  → adapter.conversation.on_user_input
#   - update_experience("对话完成")  → 触发方负责 on_round_complete（内部 clear_block3）
#   - update_experience("dump")      → adapter.conversation.on_round_complete
#   - update_experience("archive")   → adapter.archive_recall.on_archive
#   - update_experience("recall")    → adapter.archive_recall.on_recall（no-op）


__all__ = [
    # 按块写入 API
    'write_block0', 'write_block1',
    'write_block2_append', 'write_block2_rewrite',
    'write_block3', 'clear_block3',
    # L1 / compression_log IO
    'save_l1', 'save_compression_log', 'append_compression_record',
    # L1Summary 序列化（writer 视角）
    'l1summary_to_dict', 'l1summary_to_context_string',
    # 内部 helper（dump_meta 操作需要从外部导入）
    '_write_experience_file',
    '_render_block2', '_render_block3_user_input',
    '_load_dump_meta', '_save_dump_meta',
]  # fmt: skip
