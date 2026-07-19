"""adapter/archive_recall.py — 触发原因：归档 / 召回 / 自动压缩。

职责：
    - on_archive_topic(character_name, messages, time_range_start, time_range_end,
                       time_ranges, topic_hint, topic_label, people)：
        用户主动归档一段对话 → L1 摘要 + compression_log + 写 experience.md 块2
    - on_compress(character_name, messages)：
        每轮结束后自动 L1 压缩（后台任务）
    - on_archive(character_name, messages, summary_entry, visible_msgs, *, physical_total)：
        写 experience.md 块2（合并摘要 + 渲染 filtered recent）+ 更新 _dump_meta.written_len
    - on_recall(character_name, topic_label, topic_id, list_all=False)：
        召回已归档话题：按标签/ID 匹配，返回续谈注入块；或列出全部话题
    - build_topics_context(character_name, max_items=20)：
        列出所有 manual/auto 归档话题概览（供 list_all=true 用）

为什么放在适配层：
    - 触发原因都是「对历史摘要 / 话题的处理」——归档（用户主动）、召回（用户主动）、
      自动压缩（每轮后台）。
    - LLM 工具（@actor_tool 修饰的对话分段/话题提炼）也属于本层业务。
    - IO 层只负责「写文件」，不关心内容怎么算出来的。

调用方：
    - common/lifecycle.py:_post_round_async → on_compress（后台 L1 压缩）
    - tool/builtin_tools/experience.py:summarize_conversation → save_l1 / append_compression_record
    - tool/builtin_tools/experience.py:archive_recent_talk → on_archive_topic（用户主动）
    - tool/builtin_tools/experience.py:recall_topic → on_recall
    - tool/builtin_tools/experience.py:_compute_visible → _gaps_between_covered / load_compression_log
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from common.logger import logger
from data_shape import L1Summary
from experience.io import load_all_l1, save_l1, append_compression_record
from experience.io import load_compression_log as _load_compression_log_io
from experience.io.writer import write_block2_rewrite
from tool.actor_tool import _summarize_conversation, _summarize_topic


# ═══════════════════════════════════════════════════════════════════
# 阈值 / 业务常量
# ═══════════════════════════════════════════════════════════════════

L1_CHAR_THRESHOLD = 10_000  # 历史总字符数达到此值触发 L1 压缩
L1_KEEP_RECENT = 6  # 最近保留不压缩的消息条数


# 归档指令前缀：含这些前缀的 user 消息是工具调用上下文，不应作为归档目标
_ARCHIVE_TRIGGER_PREFIXES = (
    "归档", "总结", "转摘要", "压缩", "先放一放", "收尾",
    "聊完了", "话题结束", "这个话题结束", "把刚才的",
)


# ═══════════════════════════════════════════════════════════════════
# 旁路 LLM 工具（@actor_tool）：从 tool.actor_tool 集中注册后导入
# ═══════════════════════════════════════════════════════════════════

from tool.actor_tool import _summarize_conversation, _summarize_topic


# ═══════════════════════════════════════════════════════════════════
# 私有算法：消息分析 / 区间判定 / 标签匹配
# ═══════════════════════════════════════════════════════════════════

def _analyze_slice(messages: list[dict]) -> tuple[int, str, str, list[str]]:
    """分析一段消息：轮次数、起止时间、关键事件。"""
    if not messages:
        return 0, "", "", []

    user_msgs = [m for m in messages if m.get("role") == "user"]
    asst_msgs = [m for m in messages if m.get("role") == "assistant"]

    user_turns = len(user_msgs)
    start_time = messages[0].get("time", "")
    end_time = messages[-1].get("time", "")

    events: list[str] = []
    for m in asst_msgs:
        content = m.get("content", "")
        if "切换" in content and (
                "引擎" in content or "deepseek" in content.lower()
                or "千问" in content or "minimax" in content.lower()):
            if "完成" in content or "成功" in content:
                events.append("引擎切换")

    user_topics: set[str] = set()
    for m in user_msgs:
        c = m.get("content", "")
        if "了解" in c or "自己" in c:
            user_topics.add("身份探索")
        if "切换" in c:
            user_topics.add("引擎切换")
        if "智点" in c or "ICP" in c.upper() or "token" in c.lower() or "消耗" in c:
            user_topics.add("智点感知")
        if "日志" in c or "log" in c.lower():
            user_topics.add("日志调试")
        if "图片" in c or "image" in c.lower() or "img" in c.lower():
            user_topics.add("图片理解")
        if "压缩" in c or "历史" in c:
            user_topics.add("历史管理")

    events.extend(user_topics)
    return user_turns, start_time, end_time, events


def _guess_topic(events: list[str]) -> str:
    if not events:
        return "基础对话测试"
    seen = set()
    unique = [e for e in events if not (e in seen or seen.add(e))]
    return " + ".join(unique[:3])


def _describe_slice(user_turns: int, events: list[str], topic: str) -> str:
    parts = [f"共 {user_turns} 轮对话"]
    if topic != "基础对话测试":
        parts.append(f"涉及: {topic}")
    if any(e in events for e in ["引擎切换"]):
        parts.append("期间进行了智能基元切换测试")
    if any(e in events for e in ["身份探索"]):
        parts.append("反复验证身份定义与引擎感知")
    return "，".join(parts[:3]) + "。"


def _extract_send_to_character_targets(messages: list[dict]) -> list[str]:
    """从消息列表中提取所有 send_to_character 调用的目标角色名。

    用于 archive_recent_talk 的代码层兜底：people 字段应该基于工具调用的
    ground truth,而不是依赖 LLM 的自由识别。
    """
    targets = []
    seen = set()
    for m in messages:
        if m.get("role") != "assistant":
            continue
        tc = m.get("tool_calls") or []
        for fn in tc:
            if fn.get("function", {}).get("name") != "send_to_character":
                continue
            args = fn["function"].get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    continue
            to = (args or {}).get("to", "").strip()
            if to and to not in seen:
                seen.add(to)
                targets.append(to)
    return targets


def _is_msg_covered(msg_idx: int, log: list[dict], manual_only: bool = True) -> bool:
    """给定消息索引，检查它是否落在某个已被压缩的段内。

    manual_only=True（默认）时只检查 source=="archive_recent_talk" 的记录，
    避免 auto_summarize 后台任务写入的压缩区间干扰 archive_recent_talk 的判定。
    """
    for rec in log:
        if manual_only and rec.get("source") != "archive_recent_talk":
            continue
        if rec["abs_from"] <= msg_idx <= rec["abs_to"]:
            return True
    return False


def _covered_ranges(log: list[dict], manual_only: bool = False) -> list[tuple[int, int]]:
    """返回所有已压缩段的范围列表（按 abs_from 排序，合并重叠/相邻）。

    manual_only=True 时只返回 source=="archive_recent_talk" 的记录——
    用于 archive_recent_talk 工具调用时，避开 auto_summarize 的覆盖段干扰（"扰乱测试"）。
    """
    if manual_only:
        log = [r for r in log if r.get("source") == "archive_recent_talk"]
    ranges = [(r["abs_from"], r["abs_to"]) for r in log]
    ranges.sort(key=lambda x: x[0])
    if not ranges:
        return ranges
    merged = [list(ranges[0])]
    for f, t in ranges[1:]:
        if f <= merged[-1][1] + 1:
            if t > merged[-1][1]:
                merged[-1][1] = t
        else:
            merged.append([f, t])
    return [tuple(r) for r in merged]


def _gaps_between_covered(total: int, log: list[dict], manual_only: bool = False) -> list[tuple[int, int]]:
    """找出所有「未被压缩覆盖」的消息索引区间。返回 [(start, end), ...]。

    manual_only=True 时只过滤 archive_recent_talk 记录（避开 auto_summarize），
    用于 archive_recent_talk 工具的 _handle 流程。
    manual_only=False 时过滤全部压缩段（默认），用于 experience.md 对话原文区渲染。
    """
    if not log:
        return [(0, total - 1)]

    ranges = _covered_ranges(log, manual_only=manual_only)
    if not ranges:
        return [(0, total - 1)]

    gaps = []

    # 第一段 Gap：头部到第一个压缩段
    if ranges[0][0] > 0:
        gaps.append((0, ranges[0][0] - 1))

    # 中间 Gap：相邻压缩段之间的间隙
    for i in range(len(ranges) - 1):
        gap_end = ranges[i][1]
        next_start = ranges[i + 1][0]
        if next_start > gap_end + 1:
            gaps.append((gap_end + 1, next_start - 1))

    # 最后一段 Gap：最后一个压缩段之后到末尾
    if ranges[-1][1] < total - 1:
        gaps.append((ranges[-1][1] + 1, total - 1))

    return gaps


def _build_topic_label_regex(label: str) -> "re.Pattern[str]":
    """把 LLM 传的话题标签转为正则。

    避免「话题1」误匹配「话题12」。lookahead 拒绝后一个字符是数字。
    前缀不做限制——让"我们讨论话题1"也能匹配（语义上是话题1的对话）。
    """
    escaped = re.escape(label)
    return re.compile(rf"{escaped}(?!\d)")


def _is_archive_trigger(content: str) -> bool:
    """user 消息是否含归档触发前缀。"""
    if not isinstance(content, str):
        return False
    s = content.strip()
    return any(s.startswith(p) for p in _ARCHIVE_TRIGGER_PREFIXES)


# ═══════════════════════════════════════════════════════════════════
# 对话文本构建（带绝对消息索引）—— 私有，LLM 工具入参
# ═══════════════════════════════════════════════════════════════════

def _build_conversation_text(messages: list[dict], max_chars: int = 12000,
        abs_start: int = 0) -> str:
    """将消息列表转为带 [msg:N] 绝对索引标记的纯文本，供 LLM 使用。"""
    lines: list[str] = []
    total = 0
    for i, m in enumerate(messages):
        abs_idx = abs_start + i
        role = m.get("role", "unknown")
        content = m.get("content", "")
        if not content:
            continue
        line = f"[msg:{abs_idx}][{role}]: {content}"
        if total + len(line) > max_chars:
            remaining = len(messages) - i
            lines.append(
                f"…（对话截断：后续 {remaining} 条消息已省略。"
                f"请仅基于以上内容生成，最后一轮的 to_msg 设置为 {abs_start + i - 1}）…"
            )
            break
        lines.append(line)
        total += len(line)
    return "\n\n".join(lines)


def _build_topic_text(messages: list[dict], max_chars: int = 10000,
        abs_start: int = 0) -> str:
    """为话题归档构建对话文本——格式与 _build_conversation_text 相同，但限制更短。"""
    return _build_conversation_text(messages, max_chars=max_chars, abs_start=abs_start)


# ═══════════════════════════════════════════════════════════════════
# L1 构建（机械归总 + LLM 语义）
# ═══════════════════════════════════════════════════════════════════

def build_l1(character_name: str, messages: list[dict]):
    """对当前历史中「超出保留窗口」的部分构建一条 L1 摘要（机械归总）。"""
    total_chars = sum(len(m.get("content", "")) for m in messages)
    if total_chars < L1_CHAR_THRESHOLD:
        return None

    compress_slice = messages[:-L1_KEEP_RECENT] if len(messages) > L1_KEEP_RECENT else []
    if not compress_slice:
        return None

    user_turns, start_t, end_t, events = _analyze_slice(compress_slice)
    topic = _guess_topic(events)
    detail = _describe_slice(user_turns, events, topic)
    sid = f"L1-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    abs_from = 0
    abs_to = len(compress_slice) - 1

    return L1Summary(
        id=sid,
        start_time=start_t,
        end_time=end_t,
        message_count=len(compress_slice),
        user_turns=user_turns,
        summary=[{
            "from": 0,
            "to": user_turns,
            "topic": topic,
            "detail": detail,
        }],
        msg_indices=(abs_from, abs_to),
        source="auto",
    )


async def build_l1_llm(character_name: str, messages: list[dict]):
    """使用旁路小模型构建 L1 摘要（语义分段）——增量式，只摘要上次未覆盖的新轮次。"""
    total_chars = sum(len(m.get("content", "")) for m in messages)
    if total_chars < L1_CHAR_THRESHOLD:
        return None

    # 找到上次压缩的终点（绝对消息索引）
    existing = load_all_l1(character_name)
    last_covered_abs_to = -1
    if existing:
        for s in existing:
            if s.msg_indices != (0, 0):
                last_covered_abs_to = max(last_covered_abs_to, s.msg_indices[1])

    abs_start = last_covered_abs_to + 1
    if abs_start >= len(messages):
        return None

    # 增量切片：abs_start 起，保留最近 L1_KEEP_RECENT 条消息不压缩
    incremental_slice = messages[abs_start:-L1_KEEP_RECENT] \
        if len(messages) > L1_KEEP_RECENT else messages[abs_start:]
    if len(incremental_slice) < 2:
        return None

    user_turns, start_t, end_t, _events = _analyze_slice(incremental_slice)
    if user_turns == 0:
        return None
    sid = f"L1-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # 构建对话文本，传入绝对起始索引（abs_start）
    conversation_text = _build_conversation_text(incremental_slice, abs_start=abs_start)
    try:
        result = await _summarize_conversation(conversation_text=conversation_text)
        segments = result.get("segments", [])
        if not segments:
            raise ValueError("LLM returned empty segments")

        # from_msg/to_msg 现在是绝对消息索引，直接使用
        summary_entries: list[dict] = []
        for seg in segments:
            from_abs = int(seg.get("from_msg", abs_start))
            to_abs = int(seg.get("to_msg", abs_start + len(incremental_slice) - 1))
            entry = {
                "from": from_abs,
                "to": to_abs,
                "topic": str(seg.get("topic", "") or "").strip(),
                "detail": str(seg.get("detail", "") or "").strip(),
            }
            if entry["topic"] and entry["detail"]:
                summary_entries.append(entry)
        if not summary_entries:
            raise ValueError("LLM returned no valid segments")
    except Exception as e:
        logger.warning(f"  [WARN] L1 LLM summary failed ({e}), skip compression")
        return None

    abs_end = abs_start + len(incremental_slice) - 1
    return L1Summary(
        id=sid,
        start_time=start_t,
        end_time=end_t,
        message_count=len(incremental_slice),
        user_turns=user_turns,
        summary=summary_entries,
        msg_indices=(abs_start, abs_end),
        source="auto",
    )


# ═══════════════════════════════════════════════════════════════════
# 自动压缩触发（lifecycle 调）
# ═══════════════════════════════════════════════════════════════════

async def on_compress(character_name: str, messages: list[dict]):
    """检查是否需要触发 L1 压缩，需要则用 LLM 构建并保存。失败回退机械归总。

    替换 character.summarizer.check_and_compress。
    """
    summary = await build_l1_llm(character_name, messages)
    if summary is None:
        return None

    saved_path = save_l1(character_name, summary)
    _append_compression_after_save(character_name, summary, source="auto_summarize")
    if summary.summary:
        topic = summary.summary[0].get("topic", "?")
    else:
        topic = summary.topic or "?"
    logger.info(f"  [L1] compressed | {summary.user_turns} turns -> {topic} | {saved_path}")
    return summary


# 旧名兼容（character.summarizer.check_and_compress）
async def check_and_compress(character_name: str, messages: list[dict]):
    return await on_compress(character_name, messages)


# ═══════════════════════════════════════════════════════════════════
# 话题归档：on_archive_topic（用户主动触发，替换 archive_recent_talk）
# ═══════════════════════════════════════════════════════════════════

async def on_archive_topic(character_name: str, messages: list[dict],
        time_range_start: str = "",
        time_range_end: str = "",
        time_ranges: list[list[str]] | None = None,
        topic_hint: str = "",
        topic_label: str = "",
        people: list[str] | None = None):
    """按时间戳精确归档一段对话为话题摘要。

    保存到 L1 目录，source=manual；写 compression_log。

    核心原则：传参即结果。
    - 单段模式（向后兼容）：time_range_start / time_range_end 为字符串。
    - 聚合模式：time_ranges 为 [[start1, end1], ...] 数组，
      多区间一次性合并为同一条 L1、共享同一个 id；compression_log 每区间一条记录。
    - 话题标签模式：只传 topic_label，工具自动在未归档 user 中匹配。
    """
    if people is None:
        people = []

    # ── 归一化输入为区间数组 ──
    try:
        comp_log = load_compression_log(character_name)
    except Exception:
        comp_log = []

    # topic_label 自动匹配模式专用：直接返回 [a_from, a_to] 索引对，
    # 跳过 _resolve_pair 的"<=" 区间搜索（那会引入杂质消息）。
    auto_label_idx_ranges: list[tuple[int, int]] = []

    if time_ranges:
        ranges = sorted([(s.strip(), e.strip()) for s, e in time_ranges if s and e],
            key=lambda x: x[0])
        if not ranges:
            raise ValueError("time_ranges 数组不能为空或全为空字符串")
    elif time_range_start.strip() or time_range_end.strip():
        ranges = [(time_range_start.strip(), time_range_end.strip())]
    elif topic_label:
        # 话题标签自动匹配模式
        label_re = _build_topic_label_regex(topic_label)
        auto_ranges: list[tuple[str, str]] = []
        for i, m in enumerate(messages):
            if _is_msg_covered(i, comp_log):
                continue
            if m.get("role") != "user":
                continue
            content = m.get("content", "")
            if not isinstance(content, str):
                continue
            if _is_archive_trigger(content):
                continue
            if not label_re.search(content):
                continue
            # 找该 user 之后第一条 assistant 的索引
            j = i + 1
            while j < len(messages) and messages[j].get("role") != "assistant":
                j += 1
            if j >= len(messages):
                continue
            u_ts = (m.get("time") or "")[:19]
            a_ts = (messages[j].get("time") or "")[:19]
            if u_ts and a_ts and u_ts <= a_ts:
                auto_ranges.append((u_ts, a_ts))
                auto_label_idx_ranges.append((i, j))
        if not auto_ranges:
            raise ValueError(
                f"未找到含话题标签「{topic_label}」的未归档 user 消息。"
                f"请确认该标签存在于近期对话原文的 user 内容中。"
            )
        ranges = sorted(auto_ranges, key=lambda x: x[0])
    else:
        raise ValueError("必须传 time_range_start/end、time_ranges 或 topic_label 至少一个")

    def _msg_ts(m: dict) -> str:
        return (m.get("time") or "")[:19]

    def _resolve_pair(start_ts: str, end_ts: str) -> tuple[int, int]:
        if start_ts:
            cands = [i for i, m in enumerate(messages)
                     if not _is_msg_covered(i, comp_log) and _msg_ts(m) >= start_ts]
            if not cands:
                raise ValueError(
                    f"未找到时间戳 >= {start_ts} 的未归档消息。"
                    "请确认时间戳格式（YYYY-MM-DD HH:MM:SS）并直接从 experience.md 的"
                    "「近期对话原文」区复制。"
                )
            a_from = cands[0]
        else:
            uncovered = [i for i in range(len(messages))
                         if not _is_msg_covered(i, comp_log)]
            if not uncovered:
                raise ValueError("无新用户消息可归档（全部已被压缩覆盖）")
            a_from = uncovered[0]

        if end_ts:
            tail = [i for i in range(a_from, len(messages))
                    if not _is_msg_covered(i, comp_log) and _msg_ts(messages[i]) <= end_ts]
            if not tail:
                raise ValueError(
                    f"在时间范围 [{start_ts}, {end_ts}] 内未找到任何消息。"
                    "请确认 end_ts 对应的消息确实存在于「近期对话原文」中。"
                )
            a_to = tail[-1]
        else:
            tail = [i for i in range(a_from, len(messages))
                    if not _is_msg_covered(i, comp_log)]
            if not tail:
                raise ValueError(f"从 {start_ts or '最早未归档'} 起没有未归档消息可归档")
            a_to = tail[-1]

        if a_from > a_to:
            raise ValueError(f"归档范围非法：start={start_ts} > end={end_ts}")
        return a_from, a_to

    # 解析每个区间
    resolved_ranges: list[tuple[str, str, int, int]] = []
    if auto_label_idx_ranges:
        for (a_from, a_to) in auto_label_idx_ranges:
            s_time = messages[a_from].get("time", "")[:19]
            e_time = messages[a_to].get("time", "")[:19]
            resolved_ranges.append((s_time, e_time, a_from, a_to))
    else:
        for s_ts, e_ts in ranges:
            a_from, a_to = _resolve_pair(s_ts, e_ts)
            s_time = messages[a_from].get("time", "")[:19]
            e_time = messages[a_to].get("time", "")[:19]
            resolved_ranges.append((s_time, e_time, a_from, a_to))

    if not resolved_ranges:
        raise ValueError("无有效区间可归档")

    overall_first_ts = resolved_ranges[0][2]
    overall_last_ts = resolved_ranges[-1][3]
    overall_start_time = resolved_ranges[0][0]
    overall_end_time = resolved_ranges[-1][1]
    all_slice_msgs = []
    for _, _, a_from, a_to in resolved_ranges:
        all_slice_msgs.extend(messages[a_from:a_to + 1])

    user_turns, _, _, _ = _analyze_slice(all_slice_msgs)
    sid = f"T-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # 构造提示文本 — 每个区间拼一段
    hint_note = f"\n参考话题方向：{topic_hint}" if topic_hint else ""
    conv_chunks = []
    for idx, (s_time, e_time, a_from, a_to) in enumerate(resolved_ranges):
        chunk = messages[a_from:a_to + 1]
        text = _build_topic_text(chunk, max_chars=10000, abs_start=a_from)
        conv_chunks.append(
            f"### 区间 {idx + 1} / {len(resolved_ranges)} [{s_time} ~ {e_time}]\n\n{text}"
        )
    conversation_text = "\n\n".join(conv_chunks) + hint_note

    try:
        result = await _summarize_topic(conversation_text=conversation_text)
        resolved_label = topic_label or result.get("topic_label", "")
        resolved_people = people or result.get("people", [])
        summary_text = result.get("summary", "")
        key_points = result.get("key_points", [])

        if not people:
            ground_truth_chars = _extract_send_to_character_targets(all_slice_msgs)
            if ground_truth_chars:
                resolved_people = [p for p in resolved_people if p in ground_truth_chars]
                if not resolved_people:
                    resolved_people = ground_truth_chars

        segments: list[dict] = []
        for idx, (s_time, e_time, a_from, a_to) in enumerate(resolved_ranges):
            segments.append({
                "from": a_from,
                "to": a_to,
                "topic": resolved_label or _guess_topic([]),
                "detail": summary_text,
                "segment_index": idx,
                "segment_count": len(resolved_ranges),
                "range_start": s_time,
                "range_end": e_time,
            })

        summary = L1Summary(
            id=sid,
            start_time=overall_start_time,
            end_time=overall_end_time,
            message_count=len(all_slice_msgs),
            user_turns=user_turns,
            topic=resolved_label or "",
            detail=summary_text,
            summary=segments,
            topic_label=resolved_label,
            people=resolved_people,
            msg_indices=(overall_first_ts, overall_last_ts),
            source="manual",
            time_ranges=[[s, e] for s, e, _, _ in resolved_ranges],
            range_msg_indices=[[a_from, a_to] for _, _, a_from, a_to in resolved_ranges],
        )
        if key_points:
            summary.key_events = key_points
    except Exception as e:
        logger.warning(f"  [WARN] archive_recent_talk LLM failed ({e}), using fallback")
        topic = topic_label or topic_hint or "归档话题"
        fallback_segments = []
        for idx, (s_time, e_time, a_from, a_to) in enumerate(resolved_ranges):
            fallback_segments.append({
                "from": a_from,
                "to": a_to,
                "topic": topic,
                "detail": f"共 {user_turns} 轮对话，涉及 {topic_hint or topic}。",
                "segment_index": idx,
                "segment_count": len(resolved_ranges),
                "range_start": s_time,
                "range_end": e_time,
            })
        summary = L1Summary(
            id=sid,
            start_time=overall_start_time,
            end_time=overall_end_time,
            message_count=len(all_slice_msgs),
            user_turns=user_turns,
            topic=topic,
            detail=f"共 {user_turns} 轮对话（归档时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}，{len(resolved_ranges)} 个区间）",
            summary=fallback_segments,
            topic_label=topic_label or topic,
            people=people or [],
            msg_indices=(overall_first_ts, overall_last_ts),
            source="manual",
            time_ranges=[[s, e] for s, e, _, _ in resolved_ranges],
            range_msg_indices=[[a_from, a_to] for _, _, a_from, a_to in resolved_ranges],
        )

    saved_path = save_l1(character_name, summary)

    # ── compression_log：每个区间一条记录，全部指向同一个 l1_id ──
    for idx, (_, _, a_from, a_to) in enumerate(resolved_ranges):
        append_compression_record(
            character_name=character_name,
            source="archive_recent_talk",
            l1_id=summary.id,
            abs_from=a_from,
            abs_to=a_to,
            segment_index=idx,
            segment_count=len(resolved_ranges),
        )
    logger.info(f"  [archive] topic={summary.topic_label} | "
                f"{len(resolved_ranges)} ranges, {summary.user_turns} turns | {saved_path}")
    return summary


# 旧名兼容（character.summarizer.archive_recent_talk）
async def archive_recent_talk(character_name: str, messages: list[dict],
        time_range_start: str = "",
        time_range_end: str = "",
        time_ranges: list[list[str]] | None = None,
        topic_hint: str = "",
        topic_label: str = "",
        people: list[str] | None = None):
    return await on_archive_topic(
        character_name=character_name, messages=messages,
        time_range_start=time_range_start, time_range_end=time_range_end,
        time_ranges=time_ranges,
        topic_hint=topic_hint, topic_label=topic_label, people=people,
    )


# ═══════════════════════════════════════════════════════════════════
# 写 experience.md 块2（on_archive，原 update_experience("archive") 的等价）
# ═══════════════════════════════════════════════════════════════════

def on_archive(character_name: str, messages: list[dict],
        summary_entry: dict, visible_msgs: list[dict] | None = None,
        *, physical_total: int | None = None) -> dict:
    """归档：重写块2 + 更新 _dump_meta.written_len。

    等价于 update_experience("archive", {"messages", "summary_entry", "visible_msgs", "physical_total"})

    参数：
        messages: history.json 的全部消息（用于推断 physical_total）
        summary_entry: 本次归档的摘要 dict（会与现有 entries 合并或追加）
        visible_msgs: 已过滤（_gaps_between_covered）的近期对话原文
                      若为 None，自动取 messages[3:]
        physical_total: history.json 物理总消息数；None 时从磁盘读

    返回：更新后的 _dump_meta 字典
    """
    from .conversation import _render_messages_to_recent_section

    dialogue_msgs = visible_msgs
    if dialogue_msgs is None:
        dialogue_msgs = messages[3:] if len(messages) > 3 else []
    new_recent = _render_messages_to_recent_section(dialogue_msgs)

    return write_block2_rewrite(
        character_name,
        summary_entry=summary_entry,
        recent_text=new_recent,
        physical_total=physical_total,
        messages=messages,
    )


# ═══════════════════════════════════════════════════════════════════
# 话题召回：on_recall（替换 recall_topic_by_label/id + _build_recall_block）
# ═══════════════════════════════════════════════════════════════════

def build_topics_context(character_name: str, max_items: int = 20) -> str:
    """构建所有已归档话题的概览（供 list_all=true 用）。"""
    summaries = load_all_l1(character_name)
    manual_topics = [s for s in summaries if s.source == "manual"]
    auto_topics = [s for s in summaries if s.source == "auto"]

    lines = []

    if manual_topics:
        lines.append("## 主动归档的话题（archive_recent_talk）")
        for s in manual_topics[-max_items:]:
            label = s.topic_label or s.topic or "未命名话题"
            people_str = "，".join(s.people) if s.people else "无特定人物"
            detail = s.detail or (s.summary[0]["detail"] if s.summary else "")
            lines.append(
                f"- **[{s.id}]**「{label}」"
                f"｜人物: {people_str}"
                f"｜{s.start_time[:10] if s.start_time else '?'} ~ {s.end_time[:10] if s.end_time else '?'}"
                f"\n  {detail[:100]}{'...' if len(detail) > 100 else ''}"
            )

    if auto_topics:
        lines.append("\n## 自动压缩的摘要（L1）")
        for s in auto_topics[-5:]:
            label = s.topic or "未命名"
            detail = s.detail or (s.summary[0]["detail"] if s.summary else "")
            lines.append(
                f"- **[{s.id}]**「{label}」"
                f"｜{s.start_time[:10] if s.start_time else '?'} ~ {s.end_time[:10] if s.end_time else '?'}"
                f"\n  {detail[:80]}{'...' if len(detail) > 80 else ''}"
            )

    if not lines:
        return "暂无归档话题。"
    return "\n".join(lines)


def on_recall(character_name: str, topic_label: str = "",
        topic_id: str = "", list_all: bool = False):
    """召回已归档的话题摘要，支持按标签、ID 查找或 list_all 概览。

    返回：
        - list_all=True：str（话题列表）
        - 匹配成功：(L1Summary, 续谈注入块字符串)
        - 匹配失败：抛 ValueError
    """
    if list_all:
        return build_topics_context(character_name)

    summaries = load_all_l1(character_name)
    manual = sorted([s for s in summaries if s.source == "manual"],
        key=lambda s: s.id, reverse=True)
    auto = sorted([s for s in summaries if s.source != "manual"],
        key=lambda s: s.id, reverse=True)

    matched = None
    # 第一轮：精确匹配
    for s in manual:
        label = s.topic_label or s.topic or ""
        if label.lower() == topic_label.lower():
            matched = s
            break
    if not matched:
        for s in auto:
            label = s.topic_label or s.topic or ""
            if label.lower() == topic_label.lower():
                matched = s
                break
    # 第二轮：子串匹配
    if not matched:
        for s in manual:
            label = s.topic_label or s.topic or ""
            if (topic_label.lower() in label.lower()
                    or label.lower() in topic_label.lower()):
                matched = s
                break
    if not matched:
        for s in auto:
            label = s.topic_label or s.topic or ""
            if (topic_label.lower() in label.lower()
                    or label.lower() in topic_label.lower()):
                matched = s
                break

    if topic_label and not matched:
        raise ValueError(f"未找到话题「{topic_label}」的归档记录,请检查标签是否正确或用 list_all 列出全部")

    # 按 ID 路径
    if topic_id and not matched:
        for s in summaries:
            if s.id == topic_id:
                matched = s
                break
        if not matched:
            raise ValueError(f"未找到 ID 为 {topic_id} 的归档记录")

    return matched, _build_recall_block(character_name, matched)


def _build_recall_block(character_name: str, s) -> str:
    """为已匹配的摘要生成续谈注入块。

    与「近期对话原文」段同款 7.md 格式：### [ts] role + code block。
    """
    from character import get_history_path
    from character.history import History
    from .conversation import _render_single_message

    # 优先用 range_msg_indices（聚合归档存了每个区间的索引）；
    # 兼容性回退：单段归档只有 msg_indices；再回退：用 summary 最后一段的 from/to。
    ranges_to_load: list[tuple[int, int]] = []
    if s.range_msg_indices:
        ranges_to_load = [(r[0], r[1]) for r in s.range_msg_indices]
    elif s.msg_indices != (0, 0):
        ranges_to_load = [(s.msg_indices[0], s.msg_indices[1])]
    elif s.summary:
        seg = s.summary[-1]
        a_from = int(seg.get("from", -1) or -1)
        a_to = int(seg.get("to", -1) or -1)
        if a_from >= 0 and a_to >= a_from:
            ranges_to_load = [(a_from, a_to)]

    if not ranges_to_load:
        return ""

    hp = get_history_path(character_name)
    if not hp.exists():
        return ""

    hist = History(str(hp))
    rendered_sections: list[str] = []
    for a_from, a_to in ranges_to_load:
        original = hist.load_slice(a_from, a_to)
        if not original:
            continue
        section_entries: list[str] = []
        for m in original:
            section_entries.extend(_render_single_message(m))
        if section_entries:
            rendered_sections.append("\n\n".join(section_entries))

    return "\n\n".join(rendered_sections)


# 旧名兼容（character.summarizer.recall_topic_by_label / recall_topic_by_id）
def recall_topic_by_label(character_name: str, topic_label: str):
    return on_recall(character_name, topic_label=topic_label, topic_id="")


def recall_topic_by_id(character_name: str, topic_id: str):
    return on_recall(character_name, topic_label="", topic_id=topic_id)


# ═══════════════════════════════════════════════════════════════════
# 压缩记录追加（save_l1 后统一调用）
# ═══════════════════════════════════════════════════════════════════

def _append_compression_after_save(character_name: str, summary, source: str):
    """save_l1 后统一调用：追加 compression_log。

    关键设计：archive_recent_talk（manual）和 auto_summarize 都写 compression_log，
    但用 source 区分。_gaps_between_covered / _covered_ranges 只过滤 source=="archive_recent_talk"
    的记录，避免 auto L1 的覆盖段"扰乱" archive 的精确归档判定。
    """
    if summary.msg_indices == (0, 0):
        return
    append_compression_record(
        character_name=character_name, source=source,
        l1_id=summary.id, abs_from=summary.msg_indices[0],
        abs_to=summary.msg_indices[1])
    logger.info(f"  [compression_log] +1 record | {summary.id} | "
                f"msg[{summary.msg_indices[0]}:{summary.msg_indices[1]}] | source={source}")


# ═══════════════════════════════════════════════════════════════════
# 内部 helper：触发层用 load_compression_log（适配层暴露 IO 给 adapter 调用）
# ═══════════════════════════════════════════════════════════════════

def load_compression_log(character_name: str) -> list[dict]:
    """读取压缩记录表（适配层委托 IO 层）。"""
    return _load_compression_log_io(character_name)


__all__ = [
    # 触发原因适配器：归档
    "on_archive_topic", "on_archive",
    # 触发原因适配器：自动压缩
    "on_compress",
    # 触发原因适配器：召回
    "on_recall", "build_topics_context",
    # L1 构建（私有算法暴露给测试 + summarize_conversation tool 用）
    "build_l1", "build_l1_llm",
    # 私有算法（trigger 层 _compute_visible 用）
    "_gaps_between_covered", "_covered_ranges", "_is_msg_covered",
    "_extract_send_to_character_targets",
    "_build_topic_label_regex", "_is_archive_trigger", "_ARCHIVE_TRIGGER_PREFIXES",
    # 阈值常量
    "L1_CHAR_THRESHOLD", "L1_KEEP_RECENT",
    # 旧名兼容
    "archive_recent_talk", "check_and_compress",
    "recall_topic_by_label", "recall_topic_by_id",
]
