"""experience_core — experience.md 的读写接口。

核心原则：experience.md 是上下文的唯一真实来源。
任何上下文变更前先读，发送请求前从经验构建。

## message2 的结构约定
<!-- message2 -->
## 摘要

```json
[
  {"id": "...", "topic_label": "...", "start_time": "...", "end_time": "...", ...},
  ...
]
```

## 近期对话原文

### [时间] user
````text
内容
````

### [时间] assistant
````text
内容
````

### [时间] tool(name)
````text
[tool_call: name]
内容
````
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from character import get_character_dir


# ── 解析相关常量 ──────────────────────────────────────────────────────────────

_BLOCK_PATTERN = re.compile(
    r"<!--\s*message(\d+)\s*-->\s*\n(.*?)(?=\n<!--\s*message|$)",
    re.DOTALL
)
_TIMESTAMP_PATTERN = re.compile(r"###\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})]\s*(\w+)")


# ── 加载：experience.md → 4 个 message 块 ─────────────────────────────────

def load_experience(character_name: str) -> dict[int, str]:
    """从 experience.md 读取并解析出 4 个 message 块。

    返回:
        {0: message0_str, 1: message1_str, 2: message2_str, 3: message3_str}
        如果某块不存在，返回空字符串。
    """
    path = get_character_dir(character_name) / "experience.md"
    if not path.exists():
        return {0: "", 1: "", 2: "", 3: ""}

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    result: dict[int, str] = {0: "", 1: "", 2: "", 3: ""}

    # 按 <!--_msg_N_--> 标记分割：['', content0, content1, content2, content3, trailing]
    parts = re.split(r"<!--_msg_\d+_-->", content)
    # parts[0] 是文件开头（通常为空），parts[1-4] 对应 message0-3
    for i in range(4):
        if i + 1 < len(parts):
            result[i] = parts[i + 1].strip()

    # 从 blocks[3] 剥离内部追踪元数据（不发给 LLM）
    # 注意：不要对 blocks[3] 做完整 strip，否则前面的 \n 会丢失导致元数据行剥离不掉
    # 只做 lstrip 和去除尾部元数据注释
    raw_m3 = parts[4] if len(parts) > 4 else ""
    m3_stripped = raw_m3.lstrip("\n")
    m3_clean = re.sub(r"^<!-- _dump_written_len=\d+ -->\s*", "", m3_stripped)
    result[3] = m3_clean.rstrip()

    return result


# ── 写入辅助 ─────────────────────────────────────────────────────────────────

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


def _replace_backtick_run(m: re.Match) -> str:
    """将 3+ 个连续 backtick 替换为零宽连接符，避免破坏外层 fence。"""
    return "\u200b" * len(m.group())


def _render_single_message(msg: dict) -> list[str]:
    """将单条消息渲染为 markdown 条目（含 role header + fence code block）。

    有 tool_calls 时返回 [text_entry, tool_calls_entry, ...] 多个条目。
    无实质内容时返回空列表。
    """
    role = msg.get("role", "?")
    if role == "system":
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


# 全局缓存：character_name -> path，dump 操作需要查 compression_log
_CHARACTER_NAME_CACHE: dict[str, str] = {}


def _infer_character_name(blocks: dict[int, str]) -> str | None:
    """从 blocks 中推测当前角色名。优先用全局缓存。"""
    return _CHARACTER_NAME_CACHE.get("current")


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


def _parse_user_input_from_message3(message3: str) -> dict | None:
    """从 message3 解析出用户输入和时间。

    message3 格式：
    ## 本次用户消息
    ### [2026-07-08 16:01:00] user

    ```text
    用户输入内容
    ```
    """
    if not message3:
        return None
    ts_match = _TIMESTAMP_PATTERN.search(message3)
    if not ts_match:
        return None
    timestamp = ts_match.group(1)
    role = ts_match.group(2)

    # 提取 ```text ``` 块内容
    text_match = re.search(r"```text\s*\n(.*?)\n```", message3, re.DOTALL)
    text = text_match.group(1).strip() if text_match else ""

    return {"timestamp": timestamp, "role": role, "text": text}


def update_experience(
    character_name: str,
    operation: Literal["用户输入", "对话完成", "dump", "recall", "archive"],
    data: dict,
) -> None:
    """更新 experience.md 的统一入口。

    operation:
        - "用户输入": data = {user_input, timestamp?}
        - "对话完成": data = {} — 只清空 message3（对话已由 dump 写入）
        - "dump": data = {messages: list[dict]} — 增量追加 messages[3:] 到近期对话原文
        - "recall": data = {topic_id, recall_block, insert_before?} — 召回内容以 tool 块形态注入
        - "archive": data = {messages, summary_entry} — 重写近期对话原文 + 追加摘要条目
            （archive_recent_talk 工具调用）
    """
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


def _write_experience_file(path: Path, blocks: dict[int, str]) -> None:
    """将 4 个 block 写入 experience.md。

    重要：即使内容为空，也要保留块标记，否则 load_experience 解析时会丢失该块。
    标记格式为 <!--_msg_N_-->（含下划线和后缀），确保不易与内容中的任何文本混淆。
    写入前剥离内容中可能含有的内嵌块标记，防止解析错位。
    """
    import re
    # 格式：<!--_msg_0_-->（下划线前缀和后缀）
    MARKER_RE = re.compile(r"<!--_msg_\d+_-->")
    lines = []
    for idx in range(4):
        lines.append(f"<!--_msg_{idx}_-->\n")
        content = blocks.get(idx, "")
        # 关键修复 (P2):message3 是用户消息区,空内容时填入占位提示
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


# ── 构建：从 experience.md → messages ──────────────────────────────────────

def build_context_from_experience(
    config,
    character_name: str,
    user_input: str,
    image_url: str | None = None,
    switch_note: str | None = None,
    round_context: str = "",
) -> list[dict]:
    """从 experience.md 构建发送给模型的 messages。

    固定结构：[system, state, history, user]
    """
    from common.context import build_system_message

    blocks = load_experience(character_name)

    # message0: 系统提示词
    system_msg = build_system_message(config, character_name, switch_note)

    # message1: 状态
    if round_context:
        state_content = round_context
    else:
        state_content = blocks[1] or "（暂无状态数据）"
    state_msg = {"role": "user", "content": state_content}

    # message2: 历史
    history_content = blocks[2] if blocks[2] else "（暂无历史记录）"
    history_msg = {"role": "user", "content": f"# 历史\n\n{history_content}"}

    # message3: 本次用户消息（从 experience.md 的 message3 读取）
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t_sent_ms = int(datetime.now().timestamp() * 1000)

    # 优先使用传入的 user_input（更准确），其次从 blocks[3] 解析
    input_text = user_input
    if not input_text:
        parsed = _parse_user_input_from_message3(blocks[3])
        if parsed:
            input_text = parsed["text"]
            ts = parsed["timestamp"]
        else:
            ts = now
    else:
        ts = now

    if image_url:
        # 清理图片 URL
        clean_input = re.sub(
            r"(?:https?://[^\s]+|[A-Za-z]:[\\/][^\s]+)\.(?:png|jpg|jpeg|webp|gif|bmp)(?:\?[^\s]*)?\s*",
            "", user_input, flags=re.IGNORECASE
        ).strip() or user_input

        user_content = [
            {"type": "image_url", "image_url": {"url": image_url}},
            {
                "type": "text",
                "text": f"## 本次用户消息\n\n### [{ts}] user (t_sent={t_sent_ms}ms):\n\n```text\n{clean_input}\n```",
            },
        ]
    else:
        user_content = f"## 本次用户消息\n\n### [{ts}] user (t_sent={t_sent_ms}ms):\n\n```text\n{input_text}\n```"

    user_msg = {"role": "user", "content": user_content}

    return [system_msg, state_msg, history_msg, user_msg]


# ── 初始化：创建默认 experience.md ────────────────────────────────────────

def _flatten_content(content) -> str:
    """将 message content 规范化为字符串。"""
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content) if content else ""


def init_experience(character_name: str, config) -> None:
    """为新角色创建默认的 experience.md。"""
    from common.context import build_system_message

    blocks: dict[int, str] = {0: "", 1: "", 2: "", 3: ""}

    # message0: 系统提示词
    system_msg = build_system_message(config, character_name)
    blocks[0] = _flatten_content(system_msg["content"])

    # message1: 状态（占位）
    blocks[1] = "# 状态\n\n（暂无状态数据）"

    # message2: 历史（占位）
    blocks[2] = ""

    # message3: 本次用户消息（占位）
    blocks[3] = ""

    path = get_character_dir(character_name) / "experience.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_experience_file(path, blocks)
