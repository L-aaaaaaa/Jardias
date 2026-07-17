"""builtin_tools/context — 历史摘要 / 归档 / 召回工具。

依赖 ``tool.builtin`` 调度层的 ``_current_actor / _format_error`` 以及
``experience.adapter.archive_recall`` / ``experience.io`` 的归档/召回/IO，
皆在函数体内延迟 import。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from character import get_history_path
from character.history import History
from common.logger import logger
from data_shape import L1Summary
from experience.adapter.archive_recall import (
    _analyze_slice, _describe_slice, _guess_topic,
    on_archive, on_archive_topic, on_recall, build_topics_context,
    _gaps_between_covered,
)
from experience.io import (
    save_l1, append_compression_record, l1summary_to_context_string,
    load_compression_log,
)


def summarize_conversation(arguments: dict) -> str:
    """角色主动压缩早期对话历史。"""
    from tool.builtin import current_actor

    _actor = current_actor()  # 每次调用都拿最新值
    keep_recent_turns = int(arguments.get("keep_recent_turns", 6))
    topic_hint = arguments.get("topic", "")
    history_path = get_history_path(_actor)
    if not history_path.exists(): return "[Error] 无历史记录"
    with open(history_path, "r", encoding="utf-8") as f:
        messages: list[dict] = json.load(f)
    if not messages: return "[OK] 历史为空"
    user_indices = [i for i, m in enumerate(messages) if m["role"] == "user"]
    total_turns = len(user_indices)
    if total_turns <= keep_recent_turns:
        return f"[OK] 仅 {total_turns} 轮，无需压缩（阈值 {keep_recent_turns}）"
    cutoff_user_idx = total_turns - keep_recent_turns
    cutoff_msg_idx = user_indices[cutoff_user_idx]
    cutoff_time = messages[cutoff_msg_idx].get("time", "")
    compress_slice = messages[:cutoff_msg_idx]
    user_turns, starttime, endtime, events = _analyze_slice(compress_slice)
    topic = topic_hint or _guess_topic(events)
    detail = _describe_slice(user_turns, events, topic)
    now = datetime.now()
    sid = f"L1-{now.strftime('%Y%m%d-%H%M%S')}"
    abs_from = 0
    abs_to = cutoff_msg_idx - 1
    summary = L1Summary(
        id=sid, start_time=starttime, end_time=endtime, message_count=len(compress_slice),
        user_turns=user_turns, topic=topic, detail=detail, key_events=events,
        msg_indices=(abs_from, abs_to), source="manual", )
    saved_path = save_l1(_actor, summary)
    lines = [
        f"[摘要已保存] {l1summary_to_context_string(summary)}",
        f"  详情: {detail}",
        f"  截断位置: {cutoff_time} — 保留最近 {keep_recent_turns} 轮原文",
        f"  文件: {saved_path}", ]
    logger.info(f"  📦 角色主动摘要 | {user_turns} 轮 → {topic} | {saved_path}")
    append_compression_record(character_name=_actor, source="summarize_conversation",
        l1_id=summary.id, abs_from=abs_from, abs_to=abs_to)
    return "\n".join(lines)


async def archive_recent_talk(arguments: dict) -> str:
    """按时间戳精确归档一段对话为话题摘要。
    用户指令如「转为摘要」「归档这个话题」时调用。
    """
    from tool.builtin import current_actor, _format_error

    _actor = current_actor()  # 每次调用都拿最新值
    args = _parse_archive_args(arguments)  # 纯函数
    messages = _load_messages(_actor)  # 只加载原始数据
    if messages is None: return "[Error] 无历史记录"
    if err := _validate_purity(messages, args): return err  # 早返回
    archive_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        result = await _execute_archive(_actor, args, messages)
    except ValueError as e:
        return _format_archive_value_error(e)
    except Exception as e:
        return _format_error(e)
    visible_msgs = _compute_visible(messages, _actor)
    entry = _build_summary_entry(result)
    response = _build_tool_result(result)
    _persist_experience(  # 副作用收口
        _actor, entry, visible_msgs, messages, arguments, response, archive_ts)
    return response


# ═══════════════════════════════════════════════════════════════════
#  archive_recent_talk 的纯函数拆分（pipeline stages）
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ArchiveArgs:
    """解析 + 归一化后的归档参数。"""
    time_range_start: str
    time_range_end: str
    time_ranges: list[list[str]]
    topic_hint: str
    topic_label: str
    people: list[str]


def _parse_archive_args(arguments: dict) -> ArchiveArgs:
    """从 tool 调用的 arguments 字典解析、归一化为 ArchiveArgs。

    接受单段模式 (time_range_start/end) 或聚合模式 (time_ranges)；
    LLM 可能传 JSON 字符串、Python list 或"换行/分号 + 逗号"分割的字符串。
    """
    time_range_start = (arguments.get("time_range_start") or "").strip()
    time_range_end = (arguments.get("time_range_end") or "").strip()

    time_ranges = _parse_time_ranges(arguments.get("time_ranges"))

    people_str = arguments.get("people", "")
    people = [p.strip() for p in people_str.split(",") if p.strip()] if people_str else []

    return ArchiveArgs(
        time_range_start=time_range_start,
        time_range_end=time_range_end,
        time_ranges=time_ranges,
        topic_hint=arguments.get("topic_hint", ""),
        topic_label=arguments.get("topic_label", ""),
        people=people, )


def _parse_time_ranges(raw) -> list[list[str]]:
    """把 LLM 传回的 time_ranges 归一为 [[start, end], ...]。
    支持 JSON 字符串、Python list、"换行/分号 + 逗号" 三种格式。
    """
    if not raw: return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [[str(x[0]), str(x[1])] for x in parsed if isinstance(x, (list, tuple)) and len(x) >= 2]
        except Exception:
            ranges: list[list[str]] = []
            for line in raw.split("\n"):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 2 and parts[0] and parts[1]:
                    ranges.append([parts[0], parts[1]])
            return ranges
    if isinstance(raw, list):
        return [[str(x[0]), str(x[1])]
                for x in raw if isinstance(x, (list, tuple)) and len(x) >= 2]
    return []


def _load_messages(character_name: str) -> list[dict] | None:
    """加载 history.json 全部消息；文件不存在返回 None。"""
    history_path = get_history_path(character_name)
    if not history_path.exists(): return None
    with open(history_path, "r", encoding="utf-8") as f: return json.load(f)


# ── 话题纯度校验（防止 LLM 用连续区间把多个话题混在一起归档）──
_TOPIC_RE = re.compile(r"话题\s*([A-Za-z0-9一二三四五六七八九十百千零]+)")


def _extract_topic_markers(text: str) -> set[str]:
    return set(_TOPIC_RE.findall(text)) if isinstance(text, str) else set()


def _validate_range_purity(messages: list[dict], start_ts: str, end_ts: str) -> str | None:
    """校验 (start_ts, end_ts) 区间内 user 消息的话题标记是否单一。
    返回 None = 通过；返回 str = 冲突时的错误描述（含改用聚合模式的提示）。    """
    if not (start_ts and end_ts): return None
    slice_msgs = [m for m in messages if m
    .get("role") == "user" and start_ts <= (m.get("time") or "")[:19] <= end_ts]
    if not slice_msgs: return None
    all_markers: set[str] = set()
    for m in slice_msgs: all_markers.update(_extract_topic_markers(m.get("content", "")))
    if len(all_markers) <= 1: return None
    sorted_markers = sorted(all_markers)
    return (
        f"区间 [{start_ts}, {end_ts}] 内包含多个不同话题标记 {sorted_markers}。"
        f"**你必须改用聚合模式**：为每个话题标记单独构造一个区间，"
        f"例如 time_ranges=[[\"{start_ts}\", \"<第 1 个话题的末条 assistant 时间>\"], "
        f"[\"<第 2 个话题的 user 时间>\", \"{end_ts}\"]], "
        f"然后归档其中**一个**话题（其余话题留待下次分别归档）。"
        f"不要用单段模式把不同话题混在一起。")


def _validate_purity(messages: list[dict], args: ArchiveArgs) -> str | None:
    """校验所有候选区间的话题纯度，返回首个错误（含 [Error] 前缀）或 None。"""
    if args.time_range_start and args.time_range_end:
        if err := _validate_range_purity(messages, args.time_range_start, args.time_range_end):
            return f"[Error] {err}"
    for r in args.time_ranges:
        if len(r) >= 2 and r[0] and r[1]:
            if err := _validate_range_purity(messages, r[0], r[1]): return f"[Error] {err}"
    return None


async def _execute_archive(character_name: str, args: ArchiveArgs, messages: list[dict]):
    """调用 experience.adapter.archive_recall.on_archive_topic 做真正的归档。
    调用者负责捕获并翻译异常；本函数不捕获、不修改任何状态。
    """
    return await on_archive_topic(
        character_name=character_name, messages=messages,
        time_range_start=args.time_range_start,
        time_range_end=args.time_range_end,
        time_ranges=args.time_ranges if args.time_ranges else None,
        topic_hint=args.topic_hint, topic_label=args.topic_label, people=args.people,
    )


def _compute_visible(messages: list[dict], character_name: str) -> list[dict]:
    """按 compression_log 过滤出"近期对话原文"区要渲染的消息。

    manual_only=True：只看 archive_recent_talk 自己的覆盖，
    不被 auto_summarize 后台任务的覆盖段干扰（"扰乱测试"的根因）。
    """
    log = load_compression_log(character_name)
    gaps = _gaps_between_covered(len(messages), log, manual_only=True)
    visible: list[dict] = []
    for start, end in gaps: visible.extend(messages[start:end + 1])
    return visible


def _build_summary_entry(summary) -> dict:
    """从归档结果构造 summary_entry dict（聚合归档把整个范围合并为一个 entry）。  """
    return {
        "id": summary.id,
        "topic_label": summary.topic_label or summary.topic or "归档话题",
        "start_time": summary.start_time, "end_time": summary.end_time,
        "user_turns": summary.user_turns, "detail": summary.detail,
        "msg_indices": list(summary.msg_indices),
        "time_ranges": summary.time_ranges,
        "range_msg_indices": summary.range_msg_indices,  # range_msg_indices 用于 _build_recall_block 召回时分段拼接。
    }


def _build_tool_result(summary) -> str:
    """把归档结果格式化成 LLM 可见的工具结果字符串。"""
    label = summary.topic_label or summary.topic or "归档话题"
    people_str = "、".join(summary.people) if summary.people else "无特定人物"
    range_count = len(summary.range_msg_indices) if summary.range_msg_indices else 1
    detail = summary.detail or ""
    return (
        f"[OK] 话题「{label}」已归档\n"
        f"  人物: {people_str}\n"
        f"  轮次: {summary.user_turns} 轮\n"
        f"  区间数: {range_count}\n"
        f"  时间: {summary.start_time[:19] if summary.start_time else '?'} ~ "
        f"{summary.end_time[:19] if summary.end_time else '?'}\n"
        f"  摘要: {detail[:120]}{'...' if len(detail) > 120 else ''}\n"
        f"  ID: {summary.id}"
    )


def _persist_experience(character_name, entry, visible_msgs, messages,
        arguments, response, archive_ts):
    """把归档结果写入 experience.md（走 adapter.archive_recall.on_archive）。

    physical_total = history.json 当前真实长度，让 archive 写完 written_len 后，
    下次 dump_experience 不会把已渲染的工具调用重复追加。
    """
    on_archive(
        character_name,
        messages=[{"role": "system"}] * 3 + visible_msgs,
        summary_entry=entry,
        visible_msgs=visible_msgs,
        physical_total=len(messages),
    )


def _format_archive_value_error(e: ValueError) -> str:
    """把归档阶段的 ValueError 翻译成对 LLM 友好的错误字符串。

    「无新用户消息可归档」「全部已被压缩覆盖」这种语义需要明确告诉 LLM 不要重试。
    """
    msg = str(e)
    if "无新用户消息可归档" in msg or "全部已被压缩覆盖" in msg:
        return (
            f"[Error] {msg}\n"
            "提示：当前没有可归档的新内容，所有未压缩的用户消息"
            "均已被覆盖或处理。请直接告知用户该状态，**不要再次调用 archive_recent_talk**。"
        )
    return f"[Error] {msg}"


def recall_topic(arguments: dict) -> str:
    """召回已归档的话题摘要，支持按标签或 ID 精确查找。
    用户指令如「继续聊之前的话题」「回顾价值本质的讨论」时调用。
    返回续谈注入块，直接追加到上下文底部。
    """
    from tool.builtin import current_actor

    _actor = current_actor()  # 每次调用都拿最新值
    topic_label = arguments.get("topic_label", "")
    topic_id = arguments.get("topic_id", "")
    show_list = arguments.get("list_all", False)

    history = History(_actor).load()
    history_messages = history.messages

    if show_list: return build_topics_context(_actor)

    if topic_id:
        try:
            summary, block = on_recall(_actor, topic_label="", topic_id=topic_id)
            return block
        except ValueError as e:
            return f"[Error] {e}"

    if topic_label:
        try:
            summary, block = on_recall(_actor, topic_label=topic_label, topic_id="")
            label = summary.topic_label or summary.topic or "未命名"
            return (
                f"[话题回想] 找到「{label}」（ID: {summary.id}）\n"
                f"将以下内容注入上下文：\n\n{block}"
            )
        except ValueError as e:
            return f"[Error] {e}"

    return "[Error] 需要 topic_label 或 topic_id 参数，也可传 list_all=true 查看所有话题"


HANDLERS: dict[str, callable] = {
    "summarize_conversation": summarize_conversation,
    "archive_recent_talk": archive_recent_talk,
    "recall_topic": recall_topic,
}
