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

内部辅助：
    - _write_experience_file(path, blocks)：底层 4 块统一写入
    - _merge_or_append_summary(blocks, entry)：合并/追加摘要 JSON
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from character import get_character_dir
from .reader import read_all


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
    """合并或追加一条 summary_entry 到 blocks[2] 的 ## 摘要 段。

    关键：如果新 summary 的 msg_indices 与现有任一条目重叠或相邻，
    说明这是"扩展归档范围"而非"新话题"，应合并而不是追加。
    返回合并后的 entries 列表（已写入 blocks[2]）。
    """
    text2 = blocks[2] or ""
    m = re.search(r"```json\s*\n(.+?)\n```", text2, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(1))
        except Exception:
            arr = []
    else:
        arr = []

    new_from = summary_entry.get("msg_indices", [0, 0])[0]
    new_to = summary_entry.get("msg_indices", [0, 0])[1]
    merged = False
    for i, ex in enumerate(arr):
        ex_from = (ex.get("msg_indices") or [0, 0])[0]
        ex_to = (ex.get("msg_indices") or [0, 0])[1]
        # 检查重叠或相邻
        if not (new_to < ex_from or new_from > ex_to):
            # 重叠 → 合并，新条目为主
            merged_from = min(new_from, ex_from)
            merged_to = max(new_to, ex_to)
            arr[i] = dict(summary_entry)
            arr[i]["msg_indices"] = [merged_from, merged_to]
            # 保留原 id（如果有）
            if ex.get("id") and "id" not in summary_entry:
                arr[i]["id"] = ex["id"]
            # 扩展 detail 优先用新 detail，但如果新 detail 是 fallback，
            # 且原 detail 是真 LLM 生成的，则保留原 detail
            new_detail = summary_entry.get("detail", "")
            old_detail = ex.get("detail", "")
            if "归档时间" in new_detail and "归档时间" not in old_detail and len(old_detail) > len(new_detail):
                arr[i]["detail"] = old_detail
            merged = True
            break
    if not merged:
        arr.append(summary_entry)
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
        try:
            from common.logger import logger
            logger.warning(
                f"  [exp] 块2 体积 {block2_size} bytes 超过阈值 "
                f"{_BLOCK2_WARN_THRESHOLD}，建议归档早期对话"
            )
        except Exception:
            pass  # logger 不可用时静默

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
    # 内部 helper（dump_meta 操作需要从外部导入）
    '_write_experience_file',
    '_render_block2', '_render_block3_user_input',
    '_load_dump_meta', '_save_dump_meta',
]  # fmt: skip
