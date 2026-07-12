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
            "2. people：**只识别对话中直接参与发言的角色名**（通过 send_to_character 调用的目标角色、或对话对象）；"
            "对话中**被提及**但不是直接参与者的角色不应写入（如「像之前和 XX 讨论时那样」这类引用不算）。返回这些角色名列表；无特定人物则返回空数组\n"
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
    if s.time_ranges:
        d["time_ranges"] = s.time_ranges
    if s.range_msg_indices:
        d["range_msg_indices"] = s.range_msg_indices
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
        time_ranges=d.get("time_ranges", []) or [],
        range_msg_indices=d.get("range_msg_indices", []) or [],
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
# 话题归档：archive_recent_talk
# ═══════════════════════════════════════════════════════

async def archive_recent_talk(character_name: str, messages: list[dict],
                              time_range_start: str = "",
                              time_range_end: str = "",
                              time_ranges: list[list[str]] | None = None,
                              topic_hint: str = "",
                              topic_label: str = "",
                              people: list[str] = None) -> L1Summary:
    """按时间戳精确归档一段对话为话题摘要。

    由 builtin.py 的 archive_recent_talk 工具调用。
    保存到 L1 目录，source=manual。

    核心原则：传参即结果。
    - 单段模式（向后兼容）：time_range_start / time_range_end 为字符串。
    - 聚合模式：time_ranges 为 [[start1, end1], [start2, end2], ...] 数组，
      多区间一次性合并为同一条 L1、共享同一个 id；compression_log 每区间一条记录。

    Args:
        character_name: 当前角色名
        messages: history.json 的完整消息列表
        time_range_start: 单段模式起始时间戳 'YYYY-MM-DD HH:MM:SS'
        time_range_end: 单段模式结束时间戳 'YYYY-MM-DD HH:MM:SS'
        time_ranges: 聚合模式时间戳区间数组，每个元素 [start, end]，按时间升序。
        topic_hint: 话题提示
        topic_label: 用户指定的话题标签（优先）
        people: 用户指定的人物列表
    """
    if people is None:
        people = []

    # ── 归一化输入为区间数组 ──
    # 先把 comp_log 加载完（topic_label 自动匹配模式要查 compression_log）
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
        # 单段模式
        ranges = [(time_range_start.strip(), time_range_end.strip())]
    elif topic_label:
        # ── 话题标签自动匹配模式（新增）──
        # LLM 只传 topic_label（如"话题1"），工具自动在未归档 user 中
        # 找含该标签的所有 user，并为每条 user 构造独立区间
        # [user.time, user 后第一条 assistant.time]。
        # 解决「秒级时间戳相同无法精确切分」的难题：
        # 当话题1和话题2在同一秒交错出现时，LLM 无法用 time_range 切分，
        # 但可以靠文本话题标记精确定位 user 消息。
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
            # 跳过归档指令自身（"归档话题1" 等）—— 它是触发归档的元命令，
            # 不该被识别为"话题1的对话"。
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
                # 直接记下索引对，避免后面 _resolve_pair 的"<=" 区间搜索
                # 把范围内其它 user 也吞进来
                auto_label_idx_ranges.append((i, j))
        if not auto_ranges:
            raise ValueError(
                f"未找到含话题标签「{topic_label}」的未归档 user 消息。"
                f"请确认该标签存在于近期对话原文的 user 内容中。"
            )
        ranges = sorted(auto_ranges, key=lambda x: x[0])
    else:
        raise ValueError("必须传 time_range_start/end、time_ranges 或 topic_label 至少一个")

    # 读取 compression_log 用来处理"留空字符串 = 取最早/到末尾"的边界
    # (上面已在入口处加载,这里保留注释说明不再重复赋值)
    if 'comp_log' not in locals():
        comp_log = []

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
            raise ValueError(
                f"归档范围非法：start={start_ts} > end={end_ts}"
            )
        return a_from, a_to

    # 解析每个区间
    resolved_ranges: list[tuple[str, str, int, int]] = []
    if auto_label_idx_ranges:
        # topic_label 自动匹配模式：直接用预计算的 (a_from, a_to) 索引对，
        # 跳过 _resolve_pair 的"<=" 区间搜索。
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


# ═══════════════════════════════════════════════════════
# 话题回想：recall_topic
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
    """根据话题标签查找最匹配的已归档摘要,并生成续谈注入块。

    Returns:
        (匹配的摘要, 续谈注入字符串)

    修复:同一标签可能有多条 manual 归档(覆盖/扩展时多次归档),
    召回时按时间倒序优先取最新且标签完全匹配的一条,避免早期
    buggy 的窄范围 L1 阻挡后续正确的全范围 L1。
    """
    summaries = load_all_l1(character_name)
    # 优先找 manual 归档。同一标签可能有多条 manual L1(扩展归档等),
    # 召回时优先取最新创建的那条(按文件名倒序),排除早期 buggy 的窄范围 L1。
    candidates = sorted(summaries, key=lambda s: (0 if s.source == "manual" else 1))
    # manual 内部按 ID(文件名)倒序 → 最新创建优先
    manual = [s for s in candidates if s.source == "manual"]
    auto = [s for s in candidates if s.source != "manual"]
    manual_sorted = sorted(manual, key=lambda s: s.id, reverse=True)

    matched = None
    # 第一轮:精确匹配按时间倒序
    for s in manual_sorted:
        label = s.topic_label or s.topic or ""
        if label.lower() == topic_label.lower():
            matched = s
            break
    if not matched and auto:
        auto_sorted = sorted(auto, key=lambda s: s.id, reverse=True)
        for s in auto_sorted:
            label = s.topic_label or s.topic or ""
            if label.lower() == topic_label.lower():
                matched = s
                break
    # 第二轮:子串匹配(manual 按倒序)
    if not matched:
        for s in manual_sorted:
            label = s.topic_label or s.topic or ""
            if (topic_label.lower() in label.lower()
                    or label.lower() in topic_label.lower()):
                matched = s
                break
    if not matched and auto:
        auto_sorted = sorted(auto, key=lambda s: s.id, reverse=True)
        for s in auto_sorted:
            label = s.topic_label or s.topic or ""
            if (topic_label.lower() in label.lower()
                    or label.lower() in topic_label.lower()):
                matched = s
                break

    if not matched:
        raise ValueError(f"未找到话题「{topic_label}」的归档记录,请检查标签是否正确或用 list_all 列出全部")

    return matched, _build_recall_block(character_name, matched)


def recall_topic_by_id(character_name: str,
                        topic_id: str) -> tuple[L1Summary, str]:
    """根据摘要 ID 精确查找，并生成续谈注入块。"""
    summaries = load_all_l1(character_name)
    for s in summaries:
        if s.id == topic_id:
            return s, _build_recall_block(character_name, s)
    raise ValueError(f"未找到 ID 为 {topic_id} 的归档记录")


def _build_recall_block(character_name: str, s: L1Summary) -> str:
    """为已匹配的摘要生成续谈注入块。

    与 7.md 示范一致：工具 result 用 "### [timestamp] role:" + code block
    的格式渲染原对话（与「近期对话原文」段同款），保证 LLM 看到的是
    统一格式的对话流。
    """
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

    # 用 experience_core 的 _render_single_message 渲染每条消息
    # ——与「## 近期对话原文」段同款 7.md 格式：### [ts] role(name) + code block。
    try:
        from common.experience_core import _render_single_message
    except ImportError:
        return ""  # 降级：返回空字符串（让上层暴露为 tool error）

    rendered_sections: list[str] = []
    for a_from, a_to in ranges_to_load:
        original = _load_original_messages(character_name, a_from, a_to)
        if not original:
            continue
        # 渲染该区间
        section_entries: list[str] = []
        for m in original:
            section_entries.extend(_render_single_message(m))
        if section_entries:
            rendered_sections.append("\n\n".join(section_entries))

    return "\n\n".join(rendered_sections)


def _load_original_messages(character_name: str,
                              abs_from: int, abs_to: int) -> list[dict]:
    """从角色 history.json 加载 [abs_from, abs_to] 索引范围的原文。

    找不到文件 / 索引越界时回退到空列表(留给调用方降级)。
    """
    try:
        from . import get_history_path
        from .history import History
    except ImportError:
        try:
            from character import get_history_path
            from character.history import History
        except ImportError:
            return []

    hp = get_history_path(character_name)
    if not hp.exists():
        return []
    try:
        msgs = History(str(hp)).load().messages
    except Exception:
        return []
    if abs_from < 0:
        abs_from = 0
    if abs_to >= len(msgs):
        abs_to = len(msgs) - 1
    if abs_from > abs_to:
        return []
    return msgs[abs_from:abs_to + 1] 


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
                              abs_to: int,
                              segment_index: int = 0,
                              segment_count: int = 1) -> str:
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
    _save_compression_log(character_name, records)
    return cid


# ══════════════════════════════════════════════════════════════════════
# 摘要选择策略（可独立演进）
# ══════════════════════════════════════════════════════════════════════

# 最多注入上下文多少条 L1 摘要，超出则用 L2 替代
MAX_L1_IN_CONTEXT = 5


def _extract_send_to_character_targets(messages: list[dict]) -> list[str]:
    """从消息列表中提取所有 send_to_character 调用的目标角色名。

    用于 archive_recent_talk 的代码层兜底：people 字段应该基于工具调用的
    ground truth,而不是依赖 LLM 的自由识别。

    Returns:
        去重后的角色名列表(按首次出现顺序)
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


def _build_topic_label_regex(label: str) -> "re.Pattern[str]":
    """把 LLM 传的话题标签转为正则。

    避免「话题1」误匹配「话题12」。lookahead 拒绝后一个字符是数字。
    前缀不做限制——让"我们讨论话题1"也能匹配（语义上是话题1的对话）。
    """
    import re as _re
    escaped = _re.escape(label)
    return _re.compile(rf"{escaped}(?!\d)")


# 归档指令前缀：含这些前缀的 user 消息是工具调用上下文，不应作为归档目标
_ARCHIVE_TRIGGER_PREFIXES = (
    "归档", "总结", "转摘要", "压缩", "先放一放", "收尾",
    "聊完了", "话题结束", "这个话题结束", "把刚才的",
)


def _is_archive_trigger(content: str) -> bool:
    """user 消息是否含归档触发前缀。"""
    if not isinstance(content, str):
        return False
    s = content.strip()
    return any(s.startswith(p) for p in _ARCHIVE_TRIGGER_PREFIXES)


def _covered_ranges(log: list[dict], manual_only: bool = False) -> list[tuple[int, int]]:
    """返回所有已压缩段的范围列表（按 abs_from 排序，合并重叠/相邻）。

    manual_only=True 时只返回 source=="archive_recent_talk" 的记录——用于
    archive_recent_talk 工具调用时，避开 auto_summarize 的覆盖段干扰（"扰乱测试"）。

    默认 manual_only=False：experience.md 对话原文区渲染时仍然过滤全部压缩段，
    保留与 LLM 注入上下文一致的体验（auto L1 替代的对话也应从原文区移除）。
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
    """找出所有「未被压缩覆盖」的消息索引区间。
    返回 [(start, end), ...]，用于渲染 ## 近期对话原文。

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
    """save_l1 后统一调用：追加 compression_log。

    关键设计：archive_recent_talk（manual）和 auto_summarize 都写 compression_log，
    但用 source 区分。_gaps_between_covered / _covered_ranges 只过滤 source=="archive_recent_talk"
    的记录，避免 auto L1 的覆盖段"扰乱" archive 的精确归档判定。
    """
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
