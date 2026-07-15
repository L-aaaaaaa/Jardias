"""formatter.py — 消息渲染模块。

将消息渲染为 markdown 条目格式。
"""
from __future__ import annotations

import json
import re
from datetime import datetime


_TIMESTAMP_PATTERN = re.compile(r"###\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})]\s*(\w+)")


def _choose_fence(text: str) -> str:
    """选择足够长的代码块 fence，确保不与内容中的反引号序列冲突。

    规则：fence 长度 = 内容中最大反引号序列长度 + 1（至少 3）。
    这样即使 user 输入中包含 ```text 这类序列，也不会与外层 fence 冲突。
    """
    max_run = 0
    for m in re.finditer(r"`+", text):
        run_len = len(m.group())
        if run_len > max_run:
            max_run = run_len
    n = max(max_run + 1, 3)
    return "`" * n


def _replace_backtick_run(m: re.Match) -> str:
    """将 3+ 个连续 backtick 替换为零宽连接符，避免破坏外层 fence。"""
    return "\u200b" * len(m.group())


def _extract_pure_text(raw_content: str) -> str:
    """从 user 消息 content 中提取纯文本，剥离 markdown wrapper 和 code block 结构。

    支持两种格式：
    - ```text\n内容\n```
    - ```text\n内容\n```

    如果提取后内容仍含 backtick 序列，替换为 Unicode 字符（\u200b = 零宽连接符），
    防止渲染时破坏外层 fence。
    """
    # 匹配 ```text（有/无空格）... ```（有/无空格）
    m = re.search(r"```text\s*\n(.*?)```", raw_content, re.DOTALL)
    if m:
        inner = m.group(1).strip()
    else:
        # 备用：剥离 ## 本次用户消息 / ### user wrapper
        inner = re.sub(r"^## 本次用户消息\s*\n+", "", raw_content)
        inner = re.sub(r"^###\s*\[[^\]]+\]\s*user\s*\n+", "", inner)

    inner = inner.strip()

    # 如果内容仍含 3+ 个连续 backtick，替换为零宽连接符（不会破坏外层 fence）
    if re.search(r"`{3,}", inner):
        inner = re.sub(r"`{3,}", _replace_backtick_run, inner)

    return inner


def _render_single_message(msg: dict) -> list[str]:
    """将单条消息渲染为 markdown 条目（含 role header + fence code block）。

    有 tool_calls 时返回 [text_entry, tool_calls_entry, ...] 多个条目。
    无实质内容时返回空列表。
    """
    role = msg.get("role", "?")

    # system role：
    #   - 以 "[智能基元切换]" 开头的引擎切换事件：渲染（让 LLM 历史可见切换轨迹）
    #   - 其它 system（prompt 段、临时注入）：静默丢弃
    if role == "system":
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "\n".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in content)
        content = str(content)
        if content.startswith("[智能基元切换]"):
            ts = msg.get("time", "")[:19] or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fence = _choose_fence(content)
            return [f"### [{ts}] system\n\n{fence}text\n{content}\n{fence}"]
        return []

    # 过滤推理消息：渲染为独立条目，不丢失
    if msg.get("_reasoning"):
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "\n".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in content)
        content = str(content) if content else ""
        if not content:
            return []
        ts = msg.get("time", "")[:19] or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fence = _choose_fence(content)
        return [f"### [{ts}] assistant(reasoning)\n\n{fence}\n{content}\n{fence}"]

    content = msg.get("content", "")
    if isinstance(content, list):
        content = "\n".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in content)
    content = str(content) if content else ""

    # 过滤系统注入的提示消息
    if content.startswith("[系统]"):
        return []

    # 过滤纯 reasoning 内容的 assistant（没有实质回复）
    if role == "assistant" and not content and not msg.get("tool_calls"):
        return []

    ts = msg.get("time", "")[:19] or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entries: list[str] = []

    # assistant 有 tool_calls：只渲染 tool_call 条目，不渲染内独白 content。
    # 设计原则（7.md）：experience.md == 真实 messages 的对话流。
    # LLM 在调工具前的"内独白"（content）属于思考过程，不是用户可见的回复；
    # 仅 tool_call 行为 + tool result 是用户实际看到的痕迹。
    if role == "assistant" and msg.get("tool_calls"):
        tc_lines: list[str] = ["[tool_calls]"]
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            name = fn.get("name", "?")
            args = fn.get("arguments", "")
            if isinstance(args, str) and len(args) > 120:
                args = args[:120] + "..."
            elif isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            tc_lines.append(f"  {name}({args})")
        tc_content = "\n".join(tc_lines)
        tc_fence = _choose_fence(tc_content)
        return [f"### [{ts}] assistant\n\n{tc_fence}text\n{tc_content}\n{tc_fence}"]

    # tool 消息：加 name 前缀
    # 设计原则：archive_recent_talk/recall_topic 的 tool result 就是 messages 列表里
    # assistant 实际看到的 tool msg content,原样渲染到 "## 近期对话原文" 段,
    # 与 7.md 示意一致——单一真相源,experience.md == 真实 messages。
    if role == "tool":
        tc_name = msg.get("name", "")
        if tc_name:
            content = f"[tool_call: {tc_name}]\n{content}"
        # 关键修复 (P1):read_file 等返回 JSON 的工具,pretty-print 后再渲染,
        # 避免把整文件内容堆在一行难以阅读。截断超长内容以保持 experience.md 体积合理。
        if tc_name == "read_file" or (content.lstrip().startswith(("[", "{")) and len(content) > 200):
            import json as _json
            try:
                parsed = _json.loads(content)
                content = _json.dumps(parsed, ensure_ascii=False, indent=2)
            except Exception:
                pass
        # 截断阈值提到 50000,作为对真正异常的保险丝(recall_block 可达 30k+)。
        # 原则上 experience.md == messages,不做信息丢失;阈值仅在极特殊长消息时生效。
        if len(content) > 50000:
            content = content[:50000] + f"\n\n... (内容过长,已截断,共 {len(content)} 字符)"
        fence = _choose_fence(content)
        # 关键修复 (P1): tool 标签带上 name,统一为 `tool(<name>)` 格式。
        # 例如 web_search 工具的 tool 消息显示 `tool(web_search)`,而非 `tool`。
        role_label = f"{role}({tc_name})" if tc_name else role
        return [f"### [{ts}] {role_label}\n\n{fence}text\n{content}\n{fence}"]

    # 普通消息（user / assistant 无 tool_calls）
    fence = _choose_fence(content)
    return [f"### [{ts}] {role}\n\n{fence}text\n{content}\n{fence}"]


def _render_messages_to_recent_section(messages: list[dict]) -> str:
    """将 messages 渲染为不含标题的对话条目（供 dump/replace 使用）。

    返回不含 "## 近期对话原文" 标题的条目字符串，
    由调用方负责追加到 blocks[2] 或替换 blocks[2] 的对应 section。
    无实质内容时返回空字符串。

    关键修复：按 time 字段升序排序。history.json 的物理写入顺序 ≠ 真实时间顺序
    （例如 send_to_character 的 assistant(tool_calls) 在子流程内先写，
    而 user 输入由 _post_round 在每轮结束后追加；recall/park 的 tool 消息
    也会在晚于其对应 user 的时刻被追加），所以渲染前必须按 time 重排。
    """
    dialogue_msgs = messages if messages else []
    if not dialogue_msgs:
        return ""

    # 按 time 字段升序排序（time 缺失或解析失败的排到原顺序）
    def _sort_key(m: dict):
        ts = m.get("time", "")
        if isinstance(ts, str) and len(ts) >= 19:
            return ts[:19]
        return "9999-99-99 99:99:99"
    dialogue_msgs = sorted(dialogue_msgs, key=_sort_key)

    rendered: list[str] = []
    for msg in dialogue_msgs:
        for entry in _render_single_message(msg):
            rendered.append(entry)

    return "\n\n".join(rendered)


def _count_recent_entries(message2: str) -> int:
    """统计 ## 近期对话原文 中已渲染的条目数量。"""
    if not message2:
        return 0
    # 去掉双骨架前缀，找到 ## 近期对话原文 之后的内容
    marker = "## 近期对话原文"
    pos = message2.find(marker)
    if pos >= 0:
        content = message2[pos + len(marker):]
    else:
        content = message2
    return len(re.findall(r"### \[", content))


__all__ = [
    '_choose_fence', '_render_single_message', '_render_messages_to_recent_section',
    '_extract_pure_text', '_count_recent_entries'
]
