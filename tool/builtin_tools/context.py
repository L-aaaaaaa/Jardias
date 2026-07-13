"""builtin_tools/context — 历史摘要 / 归档 / 召回工具。

依赖 ``tool.builtin`` 调度层的 ``_current_actor / _format_error`` 以及
``character.summarizer`` 的 ``archive_recent_talk / _gaps_between_covered / 等``，
皆在函数体内延迟 import。
"""
from __future__ import annotations

import json
import re
from datetime import datetime


def summarize_conversation(arguments: dict) -> str:
    """角色主动压缩早期对话历史。"""
    from tool.builtin import _current_actor
    from character import get_history_path
    from character.summarizer import (
        L1Summary, append_compression_record, l1summary_to_context_string, save_l1,
        _analyze_slice, _describe_slice, _guess_topic, )
    from common.logger import logger

    keep_recent_turns = int(arguments.get("keep_recent_turns", 6))
    topic_hint = arguments.get("topic", "")

    history_path = get_history_path(_current_actor)
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

    saved_path = save_l1(_current_actor, summary)

    lines = [
        f"[摘要已保存] {l1summary_to_context_string(summary)}",
        f"  详情: {detail}",
        f"  截断位置: {cutoff_time} — 保留最近 {keep_recent_turns} 轮原文",
        f"  文件: {saved_path}", ]
    logger.info(f"  📦 角色主动摘要 | {user_turns} 轮 → {topic} | {saved_path}")

    # 追加 compression_log
    append_compression_record(character_name=_current_actor, source="summarize_conversation",
        l1_id=summary.id, abs_from=abs_from, abs_to=abs_to)

    return "\n".join(lines)


async def archive_recent_talk(arguments: dict) -> str:
    """按时间戳精确归档一段对话为话题摘要。
    用户指令如「转为摘要」「归档这个话题」时调用。
    """
    from tool.builtin import _current_actor, _format_error
    from character import get_history_path
    from character.summarizer import (
        _gaps_between_covered, archive_recent_talk, load_compression_log, )
    from common.experience_core import update_experience

    # 兼容老 import：history_json_to_markdown 已被移除
    try:
        from character.summarizer import history_json_to_markdown
    except ImportError:
        history_json_to_markdown = None

    # 解析参数：单段 (time_range_start/time_range_end) 或聚合 (time_ranges) 二选一
    time_range_start = (arguments.get("time_range_start") or "").strip()
    time_range_end = (arguments.get("time_range_end") or "").strip()
    time_ranges_raw = arguments.get("time_ranges")
    time_ranges: list[list[str]] = []
    if time_ranges_raw:
        # LLM 可能传 JSON 字符串或 Python list
        if isinstance(time_ranges_raw, str):
            try:
                parsed = json.loads(time_ranges_raw)
                if isinstance(parsed, list): time_ranges = \
                    [[str(x[0]), str(x[1])] for x in parsed if isinstance(x, (list, tuple)) and len(x) >= 2]
            except Exception:
                # 尝试按换行/分号 split；每个区间内部按逗号 split
                for line in time_ranges_raw.split("\n"):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) == 2 and parts[0] and parts[1]:
                        time_ranges.append([parts[0], parts[1]])
        elif isinstance(time_ranges_raw, list):
            for item in time_ranges_raw:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    time_ranges.append([str(item[0]), str(item[1])])
    topic_hint = arguments.get("topic_hint", "")
    topic_label = arguments.get("topic_label", "")
    people_str = arguments.get("people", "")
    people = [p.strip() for p in people_str.split(",") if p.strip()] if people_str else []

    history_path = get_history_path(_current_actor)
    if not history_path.exists(): return "[Error] 无历史记录"
    with open(history_path, "r", encoding="utf-8") as f:
        messages: list[dict] = json.load(f)

    # ── 话题纯度校验（防止 LLM 用连续区间把多个话题混在一起归档）──
    # 在 archive 真正执行前，校验每个候选区间内的 user 消息是否只含一个话题标记。
    # 若发现区间跨多个话题，直接报错让 LLM 改用聚合模式。
    _TOPIC_RE = re.compile(r"话题\s*([A-Za-z0-9一二三四五六七八九十百千零]+)")

    def _extract_topic_markers(text: str) -> set[str]:
        return set(_TOPIC_RE.findall(text)) if isinstance(text, str) else set()

    def _validate_range_purity(start_ts: str, end_ts: str) -> tuple[bool, str]:
        """校验 (start_ts, end_ts) 区间内所有 user 消息的话题标记是否一致。

        返回 (ok, error_msg)。ok=False 时 error_msg 描述冲突并提示用聚合模式。
        """

        def _msg_ts(m: dict) -> str:
            return (m.get("time") or "")[:19]

        slice_msgs = [m for m in messages
                      if m.get("role") == "user" and start_ts <= _msg_ts(m) <= end_ts]
        if not slice_msgs: return True, ""
        all_markers: set[str] = set()
        for m in slice_msgs: all_markers.update(_extract_topic_markers(m.get("content", "")))
        if len(all_markers) <= 1: return True, ""
        sorted_markers = sorted(all_markers)
        return False, (
            f"区间 [{start_ts}, {end_ts}] 内包含多个不同话题标记 {sorted_markers}。"
            f"**你必须改用聚合模式**：为每个话题标记单独构造一个区间，"
            f"例如 time_ranges=[[\"{start_ts}\", \"<第 1 个话题的末条 assistant 时间>\"], "
            f"[\"<第 2 个话题的 user 时间>\", \"{end_ts}\"]], "
            f"然后归档其中**一个**话题（其余话题留待下次分别归档）。"
            f"不要用单段模式把不同话题混在一起。"
        )

    if time_range_start and time_range_end:
        ok, err = _validate_range_purity(time_range_start, time_range_end)
        if not ok: return f"[Error] {err}"

    # 聚合模式：每个区间都做纯度校验
    if time_ranges:
        for r in time_ranges:
            if len(r) >= 2 and r[0] and r[1]:
                ok, err = _validate_range_purity(r[0], r[1])
                if not ok: return f"[Error] {err}"

    # 准备工具调用的可见性数据：原始 arguments JSON + 归档时间戳
    tool_call_args = json.dumps(arguments, ensure_ascii=False)
    archive_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        summary = await archive_recent_talk(
            character_name=_current_actor, messages=messages,
            time_range_start=time_range_start, time_range_end=time_range_end,
            time_ranges=time_ranges if time_ranges else None,
            topic_hint=topic_hint, topic_label=topic_label, people=people, )

        # 重新渲染近期对话原文（按 compression_log 过滤）
        #    manual_only=True：只看 archive_recent_talk 自己的覆盖，
        #    不被 auto_summarize 后台任务的覆盖段干扰（"扰乱测试"的根因）。
        log = load_compression_log(_current_actor)
        gaps = _gaps_between_covered(len(messages), log, manual_only=True)
        visible_msgs = []
        for start, end in gaps: visible_msgs.extend(messages[start:end + 1])

        # 构建 summary_entry：聚合归档把整个范围合并为一个 entry
        # range_msg_indices 用于 _build_recall_block 召回时分段拼接
        summary_entry = {
            "id": summary.id,
            "topic_label": summary.topic_label or summary.topic or "归档话题",
            "start_time": summary.start_time, "end_time": summary.end_time,
            "user_turns": summary.user_turns, "detail": summary.detail,
            "msg_indices": list(summary.msg_indices),
            "time_ranges": summary.time_ranges, "range_msg_indices": summary.range_msg_indices,
        }

        # 工具结果字符串
        label = summary.topic_label or summary.topic or "归档话题"
        people_str_out = "、".join(summary.people) if summary.people else "无特定人物"
        range_count = len(summary.range_msg_indices) if summary.range_msg_indices else 1
        tool_result = (
            f"[OK] 话题「{label}」已归档\n"
            f"  人物: {people_str_out}\n"
            f"  轮次: {summary.user_turns} 轮\n"
            f"  区间数: {range_count}\n"
            f"  时间: {summary.start_time[:19] if summary.start_time else '?'} ~ "
            f"{summary.end_time[:19] if summary.end_time else '?'}\n"
            f"  摘要: {summary.detail[:120]}{'...' if len(summary.detail) > 120 else ''}\n"
            f"  ID: {summary.id}"
        )

        # 更新 experience.md
        # physical_total = history.json 当前真实长度，让 archive 写完 written_len 后，
        # 下次 dump_experience 不会把已渲染的工具调用重复追加。
        update_experience(_current_actor, "archive", {
            "messages": [{"role": "system"}] * 3 + visible_msgs,
            "visible_msgs": visible_msgs, "summary_entry": summary_entry,
            "tool_call_args": tool_call_args, "tool_result": tool_result,
            "archive_ts": archive_ts, "physical_total": len(messages),
        })

        return tool_result
    except ValueError as e:
        msg = str(e)
        # 当没有新内容可归档时，明确告诉 LLM 不要重试
        if "无新用户消息可归档" in msg or "全部已被压缩覆盖" in msg:
            return (
                f"[Error] {msg}\n"
                "提示：当前没有可归档的新内容，所有未压缩的用户消息"
                "均已被覆盖或处理。请直接告知用户该状态，**不要再次调用 archive_recent_talk**。"
            )
        return f"[Error] {msg}"
    except Exception as e:
        return _format_error(e)


def recall_topic(arguments: dict) -> str:
    """召回已归档的话题摘要，支持按标签或 ID 精确查找。
    用户指令如「继续聊之前的话题」「回顾价值本质的讨论」时调用。
    返回续谈注入块，直接追加到上下文底部。
    """
    from tool.builtin import _current_actor
    from character.history import History
    from character.summarizer import (
        build_topics_context, recall_topic_by_id, recall_topic_by_label,
    )
    from common.experience_core import update_experience

    topic_label = arguments.get("topic_label", "")
    topic_id = arguments.get("topic_id", "")
    show_list = arguments.get("list_all", False)

    history = History(_current_actor).load()
    history_messages = history.messages

    if show_list:
        return build_topics_context(_current_actor)

    if topic_id:
        try:
            summary, block = recall_topic_by_id(_current_actor, topic_id)
            update_experience(_current_actor, "recall",
                {"topic_id": summary.id, "recall_block": block})  # 更新 experience.md
            return block
        except ValueError as e:
            return f"[Error] {e}"

    if topic_label:
        try:
            summary, block = recall_topic_by_label(_current_actor, topic_label)
            label = summary.topic_label or summary.topic or "未命名"
            update_experience(_current_actor, "recall",
                {"topic_id": summary.id, "recall_block": block})  # 更新 experience.md
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
