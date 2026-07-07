"""
summarizer.py — 金字塔压缩（L1 段摘要）+ 话题归档/召回
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from common.logger import logger
from data_shape import L1Summary
from tool.llm_tool import llm_tool
from . import get_summaries_dir, get_compression_log_path


# ═══════════════════════════════════════════════════════
# LLM 工具：阈值压缩用的对话分段
# ═══════════════════════════════════════════════════════

@llm_tool(
    ipu="v4-flash",
    output_schema={
        "segments": "array of {from_msg: int, to_msg: int, topic: string, detail: string}"
    },
    system=(
            "你是对话分段摘要器。阅读带 [msg:N] 标记的对话记录，按话题变化切分为多个段。\n\n"
            "## 分段原则\n"
            "1. 话题明显变化时切分（新任务、新方向、新发现、方法论转折）\n"
            "2. 每段必须有**独立的认知价值**——如果两段信息完全重复，合并为一段\n"
            "3. 碎片/闲聊可合并到相邻话题段，不必单独拆出\n"
            "4. 最少 3 段，最多 12 段。宁可多拆也不要丢信息\n\n"
            "## 每段的 detail 必须包含\n"
            "- 做了什么 → 用了什么方法 → 得到了什么结论\n"
            "- 如果有具体数值（通过率、参数值、错误类型），写进去\n"
            "- 如果有命名实体（角色名、智能基元简称、文件名），写进去\n"
            "- 禁止「进行了测试」「讨论了配置」这类空泛说法\n\n"
            "## 正确 vs 错误示例\n"
            "✅「创建角色小高配合完成 15 个工具测试，通过率 93.3%（14/15），仅 web_search 超时未通过」\n"
            "✅「用户提供照片测试 vision 能力。actor 发现角色配置标注千问3.6+但实际引擎为 MiniMax-M2.7——角色配置≠实际引擎」\n"
            "❌「共 15 轮对话，涉及引擎切换 + 图片理解」\n"
            "❌「用户要求测试配置修改，actor 进行了修改和验证」\n\n"
            "## 硬约束\n"
            "1. from_msg/to_msg 必须是消息的 [msg:N] 编号，精确读取\n"
            "2. topic: 15 字以内\n"
            "3. 禁止出现「共 N 轮」「涉及: X, Y, Z」等模板化表述\n"
            "4. 必须覆盖全部可见轮次，不允许遗漏\n"
            "5. 最终输出必须是纯 JSON 数组，以 [ 开头，不要加任何 markdown 标签或解释文字\n"
            "6. ⚠️ detail 中避免使用英文双引号 \"，用中文引号「」代替，否则 JSON 解析会失败\n"
    ),
)
async def _summarize_conversation(conversation_text: str) -> dict:
    """Auto-invoked by @llm_tool — 返回 {"segments": [{from_msg, to_msg, topic, detail}, ...]}"""
    pass


# ═══════════════════════════════════════════════════════
# LLM 工具：话题归档（用户主动触发）
# ═══════════════════════════════════════════════════════

@llm_tool(
    ipu="v4-flash",
    output_schema={
        "topic_label": "string",
        "people": "array of string",
        "summary": "string",
        "key_points": "array of string"
    },
    system=(
            "你是话题提炼师。阅读带 [msg:N] 标记的对话记录，提炼出一个话题的语义摘要。\n\n"
            "## 提炼要求\n"
            "1. topic_label：给这个话题起一个 10 字以内的标签（如「价值本质」「电影推荐」「项目架构」），\n"
            "   优先使用对话中已出现的关键词\n"
            "2. people：从对话中识别所有提及的人名/角色名，返回这些人名列表；无特定人物则返回空数组\n"
            "3. summary：用 2-4 句话综合这段对话的核心结论，不要复述细节，要提炼洞察\n"
            "4. key_points：列出 2-5 个关键观点，每个 20 字以内，用中文句号结尾\n\n"
            "## 正确示例\n"
            "topic_label: 价值本质\n"
            "people: [张三, 李四]\n"
            "summary: 讨论了价值的本质是主观还是客观。认为价值既非纯粹主观也非纯粹客观，而是主体与客体交互过程中涌现的属性。\n"
            "key_points: [价值是主体-客体交互的涌现属性。, 演化心理学视角：价值是为了生存和繁衍的适应机制。, 主观主义认为价值完全取决于个体偏好。]\n\n"
            "## 硬约束\n"
            "1. from_msg/to_msg 精确对应 [msg:N] 编号，不可遗漏\n"
            "2. 最终输出必须是纯 JSON 对象，以 { 开头，不要加任何 markdown 标签\n"
            "3. ⚠️ 所有字符串中避免使用英文双引号 \"，用中文引号「」代替\n"
    ),
)
async def _summarize_topic(conversation_text: str) -> dict:
    """Auto-invoked by @llm_tool — 返回 {topic_label, people, summary, key_points}"""
    pass


# ═══════════════════════════════════════════════════════
# 阈值配置
# ═══════════════════════════════════════════════════════

L1_CHAR_THRESHOLD = 10_000  # 历史总字符数达到此值触发 L1 压缩
L1_KEEP_RECENT = 6           # 最近保留不压缩的消息条数
L2_COUNT_THRESHOLD = 10      # L1 摘要达到此条数触发 L2 压缩


# ═══════════════════════════════════════════════════════
# 序列化 / 反序列化（支持扩展字段）
# ═══════════════════════════════════════════════════════

def l1summary_to_dict(s: L1Summary) -> dict:
    _l1_ensure_summary(s)
    d = {
        "id": s.id,
        "start_time": s.start_time,
        "end_time": s.end_time,
        "message_count": s.message_count,
        "user_turns": s.user_turns,
        "summary": s.summary,
    }
    # 扩展字段（有值才写，保持向后兼容）
    if s.topic_label:
        d["topic_label"] = s.topic_label
    if s.people:
        d["people"] = s.people
    if s.msg_indices != (0, 0):
        d["msg_indices"] = list(s.msg_indices)
    if s.source and s.source != "auto":
        d["source"] = s.source
    return d


def l1summary_from_dict(d: dict) -> L1Summary:
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
    )
    if not inst.summary and inst.topic:
        inst.summary = [{"from": 0, "to": inst.user_turns,
                          "topic": inst.topic, "detail": inst.detail}]
    return inst


def _l1_ensure_summary(s: L1Summary):
    if not s.summary and s.topic:
        s.summary = [{"from": 0, "to": s.user_turns,
                      "topic": s.topic, "detail": s.detail}]


def l1summary_to_context_string(s: L1Summary) -> str:
    _l1_ensure_summary(s)
    payload = {
        "id": s.id, "start_time": s.start_time, "end_time": s.end_time,
        "message_count": s.message_count, "user_turns": s.user_turns,
        "summary": s.summary,
    }
    return "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"


# ═══════════════════════════════════════════════════════
# 历史统计
# ═══════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════
# 对话文本构建（带绝对消息索引）
# ═══════════════════════════════════════════════════════

def _build_conversation_text(messages: list[dict], max_chars: int = 12000,
                             abs_start: int = 0) -> str:
    """将消息列表转为带 [msg:N] 绝对索引标记的纯文本，供 LLM 使用。

    abs_start: 这段 messages 在 history.json 中的绝对起始索引。
    输出格式: [msg:5] [user]: 内容
    """
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


# ═══════════════════════════════════════════════════════
# L1 构建（机械归总 + LLM 语义）
# ═══════════════════════════════════════════════════════

def build_l1(character_name: str, messages: list[dict]) -> L1Summary | None:
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

    # 计算压缩段的绝对消息索引
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


async def build_l1_llm(character_name: str,
                        messages: list[dict]) -> L1Summary | None:
    """使用旁路小模型构建 L1 摘要（语义分段）——增量式，只摘要上次未覆盖的新轮次。

    segments 中的 from/to 是绝对消息索引（对应 history.json）。
    """
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
                "from": from_abs,      # 绝对消息索引（兼容旧字段）
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


# ═══════════════════════════════════════════════════════
# 持久化
# ═══════════════════════════════════════════════════════

def save_l1(character_name: str, summary: L1Summary) -> Path:
    """保存 L1 摘要到文件。"""
    l1_dir = get_summaries_dir(character_name)
    l1_dir.mkdir(parents=True, exist_ok=True)
    path = l1_dir / f"{summary.id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(l1summary_to_dict(summary), f, ensure_ascii=False, indent=2)
    return path


def load_all_l1(character_name: str) -> list[L1Summary]:
    """加载所有 L1 摘要（按 ID 排序）。"""
    l1_dir = get_summaries_dir(character_name)
    if not l1_dir.exists():
        return []
    summaries: list[L1Summary] = []
    for f in sorted(l1_dir.glob("*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                summaries.append(l1summary_from_dict(json.load(fh)))
        except (json.JSONDecodeError, OSError):
            pass
    return summaries


async def check_and_compress(character_name: str,
                              messages: list[dict]) -> L1Summary | None:
    """检查是否需要触发 L1 压缩，需要则用 LLM 构建并保存。失败回退机械归总。"""
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


def build_l1_context(character_name: str, max_items: int = 3) -> str:
    """构建 L1 摘要块，含 `## 摘要` 标题，输出单个 JSON 数组。"""
    summaries = load_all_l1(character_name)
    if not summaries:
        return ""
    entries = []
    for s in summaries[-max_items:]:
        _l1_ensure_summary(s)
        entries.append({
            "id": s.id,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "message_count": s.message_count,
            "user_turns": s.user_turns,
            "summary": s.summary,
        })
    json_block = "```json\n" + json.dumps(entries, ensure_ascii=False, indent=2) + "\n```"
    return "## 摘要\n" + json_block


# ═══════════════════════════════════════════════════════
# 话题归档：park_topic
# ═══════════════════════════════════════════════════════

async def park_topic(character_name: str, messages: list[dict],
                     lookback_turns: int = 8,
                     topic_hint: str = "",
                     topic_label: str = "",
                     people: list[str] = None) -> L1Summary:
    """将最近 N 轮对话归档为一个话题摘要。

    由 builtin.py 的 park_topic 工具调用。
    保存到 L1 目录，source=manual。

    Args:
        character_name: 当前角色名
        messages: history.json 的完整消息列表
        lookback_turns: 回溯多少轮用户消息（默认 8 轮）
        topic_hint: 话题提示（供 LLM 参考）
        topic_label: 用户指定的话题标签（优先使用）
        people: 用户指定的人物列表
    """
    if people is None:
        people = []

    # 找到最近 lookback_turns 轮用户消息的起始位置
    user_indices = [i for i, m in enumerate(messages) if m["role"] == "user"]
    if not user_indices:
        raise ValueError("无用户消息，无法归档")

    # 取最近 lookback_turns 轮
    recent_user_indices = user_indices[-lookback_turns:]
    abs_from = recent_user_indices[0]
    # to 取这轮用户的 assistant 回复为止
    abs_to = user_indices[-1]
    if abs_to + 1 < len(messages):
        # 包含 assistant 回复
        abs_to = abs_to + 1
    else:
        abs_to = abs_to

    slice_ = messages[abs_from:abs_to + 1]
    if len(slice_) < 2:
        raise ValueError(f"归档范围过小（{len(slice_)} 条消息）")

    user_turns, start_t, end_t, _ = _analyze_slice(slice_)
    sid = f"T-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # 构造提示文本
    hint_note = f"\n参考话题方向：{topic_hint}" if topic_hint else ""
    conversation_text = _build_topic_text(slice_, max_chars=10000, abs_start=abs_from) + hint_note

    try:
        result = await _summarize_topic(conversation_text=conversation_text)
        resolved_label = topic_label or result.get("topic_label", "")
        resolved_people = people or result.get("people", [])
        summary_text = result.get("summary", "")
        key_points = result.get("key_points", [])

        # 构造单 segment
        segment = {
            "from": abs_from,
            "to": abs_to,
            "topic": resolved_label or _guess_topic([]),
            "detail": summary_text,
        }
        if key_points:
            segment["key_points"] = key_points

        summary = L1Summary(
            id=sid,
            start_time=start_t,
            end_time=end_t,
            message_count=len(slice_),
            user_turns=user_turns,
            topic=resolved_label or "",
            detail=summary_text,
            summary=[segment],
            topic_label=resolved_label,
            people=resolved_people,
            msg_indices=(abs_from, abs_to),
            source="manual",
        )
    except Exception as e:
        logger.warning(f"  [WARN] park_topic LLM failed ({e}), using fallback")
        topic = topic_label or topic_hint or "归档话题"
        summary = L1Summary(
            id=sid,
            start_time=start_t,
            end_time=end_t,
            message_count=len(slice_),
            user_turns=user_turns,
            topic=topic,
            detail=f"共 {user_turns} 轮对话（归档时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}）",
            summary=[{
                "from": abs_from,
                "to": abs_to,
                "topic": topic,
                "detail": f"共 {user_turns} 轮对话，涉及 {topic_hint or topic}。",
            }],
            topic_label=topic_label or topic,
            people=people or [],
            msg_indices=(abs_from, abs_to),
            source="manual",
        )

    saved_path = save_l1(character_name, summary)
    _append_compression_after_save(character_name, summary, source="park_topic")
    logger.info(f"  [park] topic={summary.topic_label} | "
                f"{summary.user_turns} turns | msg[{abs_from}:{abs_to}] | {saved_path}")
    return summary


# ═══════════════════════════════════════════════════════
# 话题召回：recall_topic
# ═══════════════════════════════════════════════════════

def build_topics_context(character_name: str,
                           max_items: int = 20) -> str:
    """构建所有已归档话题的概览（供 recall_topic 发现话题用）。

    格式化为可读的列表，每条包含 topic_label、people、summary。
    """
    summaries = load_all_l1(character_name)
    manual_topics = [s for s in summaries if s.source == "manual"]
    auto_topics = [s for s in summaries if s.source == "auto"]

    lines = []

    if manual_topics:
        lines.append("## 主动归档的话题（park_topic）")
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
        for s in auto_topics[-5:]:  # 只显示最近 5 条
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


def recall_topic_by_label(character_name: str,
                            topic_label: str) -> tuple[L1Summary, str]:
    """根据话题标签查找最匹配的已归档摘要，并生成续谈注入块。

    Returns:
        (匹配的摘要, 续谈注入字符串)
    """
    summaries = load_all_l1(character_name)
    # 优先找 manual 归档，再找 auto
    candidates = sorted(summaries,
                        key=lambda s: (0 if s.source == "manual" else 1))

    matched = None
    for s in reversed(candidates):
        label = s.topic_label or s.topic or ""
        if topic_label.lower() in label.lower() or label.lower() in topic_label.lower():
            matched = s
            break

    if not matched:
        raise ValueError(f"未找到话题「{topic_label}」的归档记录")

    return matched, _build_recall_block(matched)


def recall_topic_by_id(character_name: str,
                        topic_id: str) -> tuple[L1Summary, str]:
    """根据摘要 ID 精确查找，并生成续谈注入块。"""
    summaries = load_all_l1(character_name)
    for s in summaries:
        if s.id == topic_id:
            return s, _build_recall_block(s)
    raise ValueError(f"未找到 ID 为 {topic_id} 的归档记录")


def _build_recall_block(s: L1Summary) -> str:
    """为已匹配的摘要生成续谈注入块。

    格式：
    ## 续谈话题：{label}
    [{time_range}]
    人物: {people}

    ### 核心结论
    {detail}

    ### 关键观点
    {key_points}

    ### 原始讨论位置
    history.json [msg_from:msg_to]
    """
    label = s.topic_label or s.topic or "未命名话题"
    time_range = f"{s.start_time[:19] if s.start_time else '?'} ~ {s.end_time[:19] if s.end_time else '?'}"
    people_str = "、".join(s.people) if s.people else "无特定人物"

    parts = [f"## 续谈话题：{label}", f"[{time_range}]｜人物: {people_str}", ""]

    # 核心结论
    detail = s.detail
    if not detail and s.summary:
        detail = s.summary[0].get("detail", "")
    if detail:
        parts.append("### 核心结论")
        parts.append(detail)
        parts.append("")

    # 关键观点
    all_points = []
    for seg in s.summary:
        pts = seg.get("key_points", [])
        if pts:
            all_points.extend(pts)
    if all_points:
        parts.append("### 关键观点")
        for pt in all_points[:5]:
            parts.append(f"- {pt}")
        parts.append("")

    # 原始位置
    if s.msg_indices != (0, 0):
        parts.append(f"### 原始讨论位置")
        parts.append(
            f"history.json 第 {s.msg_indices[0]} ~ {s.msg_indices[1]} 条消息 "
            f"（共 {s.msg_indices[1] - s.msg_indices[0] + 1} 条）"
        )
    else:
        if s.summary:
            parts.append(f"### 原始讨论位置")
            seg = s.summary[-1]
            parts.append(f"[轮次 {seg.get('from', '?')} ~ {seg.get('to', '?')}]")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════
# 辅助：从历史中取指定消息片段的原文
# ═══════════════════════════════════════════════════════

def get_message_slice_raw(messages: list[dict],
                           from_idx: int,
                           to_idx: int) -> str:
    """从 messages 中提取指定范围的原文，用于精细续谈。"""
    slice_ = messages[from_idx:to_idx + 1]
    lines = []
    for i, m in enumerate(slice_):
        abs_idx = from_idx + i
        role = m.get("role", "?")
        content = m.get("content", "")
        time_str = m.get("time", "")[:19]
        lines.append(f"[msg:{abs_idx}][{role}][{time_str}]: {content}")
    return "\n\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# compression_log：压缩记录表（只追加，不修改旧记录）
# ══════════════════════════════════════════════════════════════════════

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


def _save_compression_log(character_name: str, records: list[dict]):
    """直接写回压缩记录表（覆盖），供 append_compression_record 内部使用。"""
    path = get_compression_log_path(character_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def append_compression_record(character_name: str,
                              source: str,
                              l1_id: str,
                              abs_from: int,
                              abs_to: int) -> str:
    """追加一条压缩记录，返回压缩事件 ID。"""
    records = load_compression_log(character_name)
    cid = f"C-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    records.append({
        "id": cid,
        "source": source,
        "l1_id": l1_id,
        "abs_from": abs_from,
        "abs_to": abs_to,
        "compressed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_compression_log(character_name, records)
    return cid


# ══════════════════════════════════════════════════════════════════════
# 摘要选择策略（可独立演进）
# ══════════════════════════════════════════════════════════════════════

# 最多注入上下文多少条 L1 摘要，超出则用 L2 替代
MAX_L1_IN_CONTEXT = 5


def _is_msg_covered(msg_idx: int, log: list[dict]) -> bool:
    """给定消息索引，检查它是否落在某个已被压缩的段内。"""
    for rec in log:
        if rec["abs_from"] <= msg_idx <= rec["abs_to"]:
            return True
    return False


def _covered_ranges(log: list[dict]) -> list[tuple[int, int]]:
    """返回所有已压缩段的范围列表（按 abs_from 排序）。"""
    ranges = [(r["abs_from"], r["abs_to"]) for r in log]
    ranges.sort(key=lambda x: x[0])
    return ranges


def _gaps_between_covered(total: int, log: list[dict]) -> list[tuple[int, int]]:
    """找出所有「未被压缩覆盖」的消息索引区间。
    返回 [(start, end), ...]，用于渲染 ## 近期对话原文。
    """
    if not log:
        return [(0, total - 1)]

    ranges = _covered_ranges(log)
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


def select_summaries_for_context(character_name: str,
                                 messages: list[dict],
                                 log: list[dict] | None = None) -> list[L1Summary]:
    """入口统一：给定角色名+完整历史+压缩记录，返回应注入上下文的摘要列表。

    策略：
    1. 收集所有 l1_id 指向的 L1 摘要
    2. 按 abs_from 升序排列（时间顺序）
    3. 若超过 MAX_L1_IN_CONTEXT，替换为 L2（逻辑在 L2 模块，这里先返回全量）
    """
    if log is None:
        log = load_compression_log(character_name)
    if not log:
        return []

    all_l1 = load_all_l1(character_name)
    l1_by_id = {s.id: s for s in all_l1}

    # 按 abs_from 升序，选出 log 中 l1_id 存在的 L1
    log_sorted = sorted(log, key=lambda r: r["abs_from"])
    selected = []
    for rec in log_sorted:
        l1_id = rec.get("l1_id")
        if l1_id and l1_id in l1_by_id:
            selected.append(l1_by_id[l1_id])

    # 超过上限：暂时截断（后续 L2 替代逻辑在这里扩展）
    if len(selected) > MAX_L1_IN_CONTEXT:
        selected = selected[-MAX_L1_IN_CONTEXT:]

    return selected


# ══════════════════════════════════════════════════════════════════════
# 区块渲染
# ══════════════════════════════════════════════════════════════════════

def build_summary_block(selected: list[L1Summary]) -> str:
    """将选中的摘要渲染为 ## 摘要 区块。"""
    if not selected:
        return ""
    entries = []
    for s in selected:
        _l1_ensure_summary(s)
        entries.append({
            "id": s.id,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "message_count": s.message_count,
            "user_turns": s.user_turns,
            "summary": s.summary,
        })
    json_block = "```json\n" + json.dumps(entries, ensure_ascii=False, indent=2) + "\n```"
    return "## 摘要\n\n" + json_block


def _build_recent_history(history: list[dict], keep_turns: int = 6) -> str:
    """将消息列表渲染为 ## 近期对话原文 区块（仅最近 keep_turns 轮）。"""
    if not history:
        return "## 近期对话原文\n\n（暂无对话记录）"

    messages: list[str] = []
    cutoff = max(0, len(history) - keep_turns * 2)
    for msg in history[cutoff:]:
        role = msg.get("role", "unknown")
        t = msg.get("time", "")
        if role == "system_trigger":
            header = f"[{t[:19]}] 时策触发"
        elif role == "assistant":
            header = f"[{t[:19]}] assistant"
        elif role == "user":
            header = f"[{t[:19]}] user"
        elif role == "system":
            header = f"[{t[:19]}] system"
        else:
            continue

        content = msg.get("content", "")
        if not content:
            continue
        content = content.strip()
        fence = "```"
        if fence in content:
            fence = "````"
            if "````" in content:
                fence = "`````"
        messages.append(f"### {header}\n\n{fence}text\n{content}\n{fence}")

    return "## 近期对话原文\n\n" + "\n\n".join(messages)


def build_recent_history_filtered(messages: list[dict],
                                   log: list[dict] | None = None) -> str:
    """渲染 ## 近期对话原文，跳过所有已被压缩的消息段。

    log 为 None 时直接返回全部历史（原行为兼容）。
    """
    if not messages:
        return "## 近期对话原文\n\n（暂无历史记录。）"

    total = len(messages)

    if log is None or log == []:
        return _build_recent_history(messages)

    gaps = _gaps_between_covered(total, log)
    if not gaps:
        return "## 近期对话原文\n\n（所有历史均已归档为摘要。）"

    messages_out: list[dict] = []
    for start, end in gaps:
        messages_out.extend(messages[start:end + 1])

    return _build_recent_history(messages_out)


def _append_compression_after_save(character_name: str,
                                   summary: L1Summary,
                                   source: str):
    """save_l1 后统一调用：追加 compression_log。"""
    if summary.msg_indices == (0, 0):
        return
    append_compression_record(
        character_name=character_name,
        source=source,
        l1_id=summary.id,
        abs_from=summary.msg_indices[0],
        abs_to=summary.msg_indices[1],
    )
    logger.info(f"  [compression_log] +1 record | {summary.id} | "
                f"msg[{summary.msg_indices[0]}:{summary.msg_indices[1]}] | source={source}")
