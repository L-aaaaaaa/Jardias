"""writer.py — experience.md 写入模块。

提供 update_experience 统一入口，处理 dump/archive/recall/用户输入等操作。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from character import get_character_dir
from .reader import load_experience


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
        if idx == 3 and not content.strip(): content = "（等待用户输入）"
        if content:
            # 剥离内嵌的块标记
            content = MARKER_RE.sub("", content)
            lines.append(content)
        lines.append("\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(lines))
        f.flush()


def _append_summary_entry_to_blocks(blocks: dict[int, str], entry: dict) -> None:
    """在 blocks[2] 的 ## 摘要 section 追加一条 JSON 条目。"""
    text2 = blocks[2] or ""

    # 提取现有摘要 JSON（如果有）
    m = re.search(r"(## 摘要\s*\n\n```json\s*\n)(.+?)(\n```)", text2, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(2))
        except Exception:
            arr = []
    else:
        arr = []

    arr.append(entry)

    new_json = json.dumps(arr, ensure_ascii=False, indent=2)
    new_summary = f"## 摘要\n\n```json\n{new_json}\n```"

    # 替换或插入摘要 section
    if m:
        text2 = text2[:m.start()] + new_summary + text2[m.end():]
    else:
        # 没有摘要 section，在 ## 近期对话原文 之前插入
        recent_match = re.search(r"(## 近期对话原文)", text2)
        if recent_match:
            text2 = text2[:recent_match.start()] + new_summary + "\n\n" + text2[recent_match.start():]
        else:
            text2 = new_summary + "\n\n" + text2

    blocks[2] = text2


def update_experience(
        character_name: str, operation: Literal["用户输入", "对话完成", "dump", "recall", "archive"],
        data: dict, ) -> None:
    """更新 experience.md 的统一入口。

    operation:
        - "用户输入": data = {user_input, timestamp?}
        - "对话完成": data = {} — 只清空 message3（对话已由 dump 写入）
        - "dump": data = {messages: list[dict]} — 增量追加 messages[3:] 到近期对话原文
        - "recall": data = {topic_id, recall_block, insert_before?} — 召回内容以 tool 块形态注入
        - "archive": data = {messages, summary_entry} — 重写近期对话原文 + 追加摘要条目
            （archive_recent_talk 工具调用）
    """
    from .formatter import _render_messages_to_recent_section, _extract_pure_text

    path = get_character_dir(character_name) / "experience.md"

    # 先读原始文件（用于提取 _dump_written_len 元数据）
    raw_content = ""
    if path.exists():
        raw_content = path.read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)

    blocks = load_experience(character_name)

    # _dump_meta.json 路径（dump/park 操作需要读写）
    meta_path = path.parent / "_dump_meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if operation == "用户输入":
        user_input = data["user_input"]
        ts = data.get("timestamp") or now_str
        blocks[3] = (
            f"## 本次用户消息\n\n"
            f"### [{ts}] user\n\n"
            f"```text\n{user_input}\n```"
        )

    elif operation == "对话完成":
        blocks[3] = ""

    elif operation == "dump":
        # 用 meta["written_len"]（消息数）作为真正的计数器，避免与 park 操作冲突。
        # park 会把 blocks[2] 重写成「摘要 + 空的近期对话原文」，如果用条目数
        # 判断，会误以为从零开始，导致把已有摘要 L1 覆盖掉。
        meta = data.get("_meta") or {}
        current_written = meta.get("written_len", 0)

        new_msgs = data.get("messages", [])
        if not new_msgs:
            return

        # 按 compression_log 过滤：已压缩的消息不应再写入 ## 近期对话原文。
        from character.summarizer import load_compression_log, _covered_ranges
        try:
            char_name = data.get("character_name") or _infer_character_name(blocks)
            comp_log = load_compression_log(char_name) if char_name else []
        except Exception:
            comp_log = []

        if comp_log:
            covered = _covered_ranges(comp_log)

            def _is_covered(idx: int) -> bool:
                for f, t in covered:
                    if f <= idx <= t:
                        return True
                return False

            new_msgs = [m for i, m in enumerate(new_msgs) if not _is_covered(current_written + i)]
            if not new_msgs:
                return

        # user 消息：提取纯文本，剥离 wrapper
        for m in new_msgs:
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                m["content"] = _extract_pure_text(m["content"])

        new_recent = _render_messages_to_recent_section(new_msgs)

        # 判断 blocks[2] 是否已有内容
        has_existing_content = bool(blocks[2]) and "## 近期对话原文" in blocks[2]

        if not has_existing_content:
            blocks[2] = (
                    "# 历史\n\n"
                    "## 摘要\n```json\n[]\n```\n\n"
                    "## 近期对话原文\n\n"
                    + new_recent
            )
        else:
            blocks[2] = blocks[2] + "\n\n" + new_recent

        blocks[3] = ""

    elif operation == "recall":
        # 7.md 设计原则：experience.md 是 messages 的单一真相源。
        # recall_topic 的真实 tool result (recall_block) 已在 messages
        # 列表的 tool msg 中,dump 阶段会原样渲染到 "## 近期对话原文" 段。
        # recall 操作在 experience.md 上 no-op,不再单独维护 "## 已召回话题" section。
        # 这里保留 noop 分支仅为兼容上游调用。
        return

    elif operation == "archive":
        # data = {messages, summary_entry, visible_msgs,
        #         tool_call_args?: str, tool_result?: str, archive_ts?: str}
        messages = data["messages"]
        summary_entry = data["summary_entry"]
        # tool_call_args / tool_result / archive_ts 在 dump 阶段由
        # _render_messages_to_recent_section 自然渲染(assistant(tool_calls) 中的
        # archive_recent_talk tool_call 不再被 skip),不在这里手动构造条目。
        # 字段保留仅为兼容上游调用;实际不参与本分支渲染逻辑。

        # 1. 渲染新的近期对话原文
        #    注意：必须用 visible_msgs（已经过 _gaps_between_covered 过滤），
        #    而不是 messages[3:]（后者包含全部对话消息，会把已归档段也渲染回去）。
        #    archive_recent_talk 工具调用本身在 dump 渲染时照常写入"近期对话原文"区,
        #    所有对话原文按 time 排序,不会出现乱序。
        dialogue_msgs = data.get("visible_msgs") or (messages[3:] if len(messages) > 3 else [])

        new_recent = _render_messages_to_recent_section(dialogue_msgs)

        # 2. 追加或合并 summary_entry 到现有摘要 JSON
        #    关键修复：如果新 summary 的 msg_indices 与现有任一条目重叠，
        #    说明这是"扩展归档范围"而非"新话题"，应合并而不是追加。
        existing = blocks[2] or ""
        m = re.search(r"```json\s*\n(.+?)\n```", existing, re.DOTALL)
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
        new_json = json.dumps(arr, ensure_ascii=False, indent=2)

        # 3. 直接重建 blocks[2]，保证双骨架顺序正确
        blocks[2] = (
                "# 历史\n\n"
                f"## 摘要\n\n```json\n{new_json}\n```\n\n"
                "## 近期对话原文\n\n"
                + new_recent
        )

        # 4. 更新 _dump_meta.json（归档后 written_len 必须 = history.json 的物理总消息数）
        #    关键：dump_experience 里 `len(dialogue_msgs)` 是从 disk history.json 读的，
        #    包含 system、user、assistant、assistant(tool_calls)、tool 等全部条目。
        #    written_len 也必须是同一个值，否则下次 dump 时 current_written vs dialogue_msgs
        #    错位 → 把已渲染的 archive 工具调用/未渲染的对话消息再次重复追加 → experience.md 重复内容。
        #    不能用 visible_msgs 长度（visible 是去掉被覆盖段后的，与 disk 长度不一样）。
        history_path = path.parent / "history.json"
        physical_total = data.get("physical_total")
        if physical_total is None and history_path.exists():
            try:
                physical_total = len(json.loads(history_path.read_text(encoding="utf-8")))
            except Exception:
                physical_total = 0
        if physical_total is None:
            physical_total = len(messages)
        meta["written_len"] = physical_total
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    # 写入文件
    _write_experience_file(path, blocks)


def _infer_character_name(blocks: dict[int, str]) -> str | None:
    """从 blocks 中推测当前角色名。优先用全局缓存。"""
    # 全局缓存：character_name -> path，dump 操作需要查 compression_log
    from .reader import _CHARACTER_NAME_CACHE
    return _CHARACTER_NAME_CACHE.get("current")


__all__ = ['update_experience', '_write_experience_file', '_append_summary_entry_to_blocks']
