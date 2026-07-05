"""
summarizer.py — 金字塔压缩（L1 段摘要）
"""
from __future__ import annotations

import json
from datetime import datetime

from common.logger import logger
from data_shape import L1Summary
from tool.llm_tool import llm_tool
from . import get_summaries_dir


# ── 旁路小模型：对话摘要 ──

@llm_tool(
    ipu="v4-flash",
    output_schema={
        "segments": "array of {from: int, to: int, topic: string, detail: string}"
    },
    system=(
            "你是对话分段摘要器。阅读带 [轮次 N] 标记的对话记录，按话题变化切分为多个段。\n\n"
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
            "1. from/to 从 [轮次 N] 标记精确读取\n"
            "2. topic: 15 字以内\n"
            "3. 禁止出现「共 N 轮」「涉及: X, Y, Z」等模板化表述\n"
            "4. 必须覆盖全部可见轮次，不允许遗漏\n"
            "5. 最终输出必须是纯 JSON 数组，以 [ 开头，不要加任何 markdown 标签或解释文字\n"
            "6. ⚠️ detail 中避免使用英文双引号 \"，用中文引号「」代替，否则 JSON 解析会失败\n"
    ),
)
async def _summarize_conversation(conversation_text: str) -> dict:
    """Auto-invoked by @llm_tool — 返回 {"segments": [{from, to, topic, detail}, ...]}"""
    pass


# ── 阈值配置 ──

L1_CHAR_THRESHOLD = 10_000  # 历史总字符数达到此值触发 L1 压缩
L1_KEEP_RECENT = 6  # 最近保留不压缩的消息条数
L2_COUNT_THRESHOLD = 10  # L1 摘要达到此条数触发 L2 压缩


def l1summary_to_dict(s: L1Summary) -> dict:
    _l1_ensure_summary(s)
    return {
        "id": s.id,
        "start_time": s.start_time,
        "end_time": s.end_time,
        "message_count": s.message_count,
        "user_turns": s.user_turns,
        "summary": s.summary,
    }


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
    )
    if not inst.summary and inst.topic:
        inst.summary = [{"from": 0, "to": inst.user_turns, "topic": inst.topic, "detail": inst.detail}]
    return inst


def _l1_ensure_summary(s: L1Summary):
    if not s.summary and s.topic:
        s.summary = [{"from": 0, "to": s.user_turns, "topic": s.topic, "detail": s.detail}]


def l1summary_to_context_string(s: L1Summary) -> str:
    _l1_ensure_summary(s)
    payload = {
        "id": s.id, "start_time": s.start_time, "end_time": s.end_time,
        "message_count": s.message_count, "user_turns": s.user_turns,
        "summary": s.summary,
    }
    return "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"


# ── 历史统计 ──

def _analyze_slice(messages: list[dict]) -> tuple[int, str, str, list[str]]:
    """分析一段消息：轮次数、起止时间、关键事件。"""
    if not messages:
        return 0, "", "", []

    user_msgs = [m for m in messages if m.get("role") == "user"]
    asst_msgs = [m for m in messages if m.get("role") == "assistant"]

    user_turns = len(user_msgs)
    start_time = messages[0].get("time", "")
    end_time = messages[-1].get("time", "")

    # 提取关键事件：工具调用、模型切换、用户明确指令
    events: list[str] = []
    for m in asst_msgs:
        content = m.get("content", "")
        if "切换" in content and (
                "引擎" in content or "deepseek" in content.lower() or "千问" in content or "minimax" in content.lower()):
            if "完成" in content or "成功" in content:
                events.append("引擎切换")

    # 用户发了什么主题的词
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
    """根据事件猜主题。"""
    if not events:
        return "基础对话测试"
    # 去重保持顺序
    seen = set()
    unique = [e for e in events if not (e in seen or seen.add(e))]
    return " + ".join(unique[:3])


def _describe_slice(user_turns: int, events: list[str], topic: str) -> str:
    """生成 2-3 句详细描述。"""
    parts = [f"共 {user_turns} 轮对话"]
    if topic != "基础对话测试":
        parts.append(f"涉及: {topic}")
    if any(e in events for e in ["引擎切换"]):
        parts.append("期间进行了智能基元切换测试")
    if any(e in events for e in ["身份探索"]):
        parts.append("反复验证身份定义与引擎感知")
    return "，".join(parts[:3]) + "。"


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
    )


async def build_l1_llm(character_name: str, messages: list[dict]) -> L1Summary | None:
    """使用旁路小模型构建 L1 摘要（语义索引）——增量式，只摘要上次未覆盖的新轮次。

    失败时不产生任何摘要。
    """
    total_chars = sum(len(m.get("content", "")) for m in messages)
    if total_chars < L1_CHAR_THRESHOLD:
        return None

    # 找到上次压缩的终点（绝对轮次号），只摘要增量部分
    existing = load_all_l1(character_name)
    last_covered_turn = -1
    if existing:
        for s in existing:
            if s.summary:
                for seg in s.summary:
                    last_covered_turn = max(last_covered_turn, seg.get("to", -1))

    # 将 messages 按 user 消息分轮次，找到增量起点
    user_indices: list[int] = []
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            user_indices.append(i)
    start_turn_idx = last_covered_turn + 1  # 绝对轮次号
    if start_turn_idx >= len(user_indices):
        return None  # 没有新轮次需要摘要
    start_msg_idx = user_indices[start_turn_idx]

    # 增量切片：新轮次起，保留最近 L1_KEEP_RECENT 条消息不压缩
    incremental_slice = messages[start_msg_idx:-L1_KEEP_RECENT] if len(messages) > L1_KEEP_RECENT else messages[
        start_msg_idx:]
    if len(incremental_slice) < 2:  # 至少需要一轮对话（user + assistant）
        return None

    user_turns, start_t, end_t, _events = _analyze_slice(incremental_slice)
    if user_turns == 0:
        return None
    sid = f"L1-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # 构建对话文本 → 调旁路模型（传入绝对起始轮次号）
    conversation_text = _build_conversation_text(incremental_slice, start_turn=start_turn_idx)
    try:
        result = await _summarize_conversation(conversation_text=conversation_text)
        segments = result.get("segments", [])
        if not segments:
            raise ValueError("LLM returned empty segments")
        summary_entries: list[dict] = []
        for seg in segments:
            entry = {
                "from": int(seg.get("from", 0)),
                "to": int(seg.get("to", 0)),
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

    return L1Summary(
        id=sid,
        start_time=start_t,
        end_time=end_t,
        message_count=len(incremental_slice),
        user_turns=user_turns,
        summary=summary_entries,
    )


def _build_conversation_text(messages: list[dict], max_chars: int = 12000, start_turn: int = 0) -> str:
    """将消息列表转为带绝对轮次标记的纯文本，控制长度给 LLM 用。"""
    lines: list[str] = []
    total = 0
    turn = start_turn
    for i, m in enumerate(messages):
        role = m.get("role", "unknown")
        content = m.get("content", "")
        if not content:
            continue

        # 每轮对话（user + assistant）一个轮次号
        prefix = ""
        if role == "user":
            prefix = f"[轮次 {turn}] "
            turn += 1

        line = f"{prefix}[{role}]: {content}"
        if total + len(line) > max_chars:
            remaining = len(messages) - i
            lines.append(
                f"…（对话截断：后续 {remaining} 条消息（约 {remaining // 2} 轮）已省略。"
                f"请仅基于以上对话内容生成 segments，最后一轮的 to 设置为最后一个可见轮次）…"
            )
            break
        lines.append(line)
        total += len(line)
    return "\n\n".join(lines)


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


async def check_and_compress(character_name: str, messages: list[dict]) -> L1Summary | None:
    """检查是否需要触发 L1 压缩，需要则用 LLM 构建并保存。失败回退机械归总。"""
    summary = await build_l1_llm(character_name, messages)
    if summary is None:
        return None

    saved_path = save_l1(character_name, summary)
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
