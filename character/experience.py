"""
experience.py — 增量写 experience.md

从 thought_weaver.py 拆出（第一梯队）。
职责：
- _choose_fence：选足够长的 markdown 代码块 fence
- _flatten：单条消息 → 可读文本
- _render_messages_as_dialogue：消息列表 → 对话格式
- dump_experience：增量追加对话历史到 experience.md

依赖：character.history.History / common.experience_core / character 目录工具
与其他 ipu_client 模块零耦合。
"""
from __future__ import annotations


# def _choose_fence(text: str) -> str:
#     """选择足够长的代码块 fence，确保不与内容中的反引号序列冲突。"""
#     max_run = 0
#     for m in re.finditer(r"`+", text):
#         run_len = len(m.group())
#         if run_len > max_run: max_run = run_len
#     n = max_run + 1 if max_run >= 3 else 3
#     return "`" * max(3, n)
#
#
# def _flatten(msg: dict) -> str:
#     """将一条消息转为可读文本。"""
#     content = msg.get("content")
#     parts: list[str] = []
#
#     # 推理内容不展示
#     if msg.get("role") == "assistant" and msg.get("reasoning_content"):
#         pass
#
#     if isinstance(content, list):
#         for item in content:
#             if item.get("type") == "image_url":
#                 url = item.get("image_url", {}).get("url", "")
#                 tag = f"[image: {url[:60]}...]" if len(url) > 60 else f"[image: {url}]"
#                 parts.append(tag)
#             else:
#                 parts.append(item.get("text", ""))
#     elif content:
#         # 仅对 user 消息剥离 form_full_context 包裹
#         if msg.get("role") == "user":
#             from common.context import strip_context_wrapper
#             clean = strip_context_wrapper(str(content))
#             parts.append(clean)
#         else:
#             parts.append(str(content))
#
#     if msg.get("role") == "tool":
#         tc_name = msg.get("name", "")
#         if tc_name: parts.insert(0, f"[tool_call: {tc_name}]")
#
#     if tool_calls := msg.get("tool_calls"):
#         tc_lines = ["[tool_calls]"]
#         for tc in tool_calls:
#             fn = tc.get("function", {})
#             name = fn.get("name", "?")
#             args = fn.get("arguments", "")
#             if isinstance(args, str) and len(args) > 120:
#                 args = args[:120] + "..."
#             elif isinstance(args, dict):
#                 args = json.dumps(args, ensure_ascii=False)
#             tc_lines.append(f"  {name}({args})")
#         parts.append("\n".join(tc_lines))
#
#     return "\n".join(parts)
#
#
# def _render_messages_as_dialogue(msgs: list[dict]) -> str:
#     """将消息列表渲染为对话格式（不含 ## 标题）。
#     用于从 messages 列表中提取历史消息，不依赖 message2 内容。
#     """
#     if not msgs: return ""
#     lines: list[str] = []
#     for m in msgs:
#         role = m.get("role", "unknown")
#         # 跳过 system 消息
#         if role == "system": continue
#         # 跳过系统提示消息
#         content = m.get("content", "")
#         if isinstance(content, str) and content.startswith("[系统]"): continue
#         text = _flatten(m)
#         fence = _choose_fence(text)
#         msg_time = m.get("time", "")
#         time_str = msg_time[:19] if msg_time else time.strftime("%Y-%m-%d %H:%M:%S")
#         lines.append(f"### [{time_str}] {role}:\n\n{fence}text\n{text}\n{fence}")
#     return "\n\n".join(lines)


def dump_experience(character_name: str, messages: list[dict] | None = None,
        round_context: str | None = None, round_usage: dict | None = None):
    """增量追加对话历史到 experience.md。

    始终从磁盘读取 history.json 获取最新消息列表。
    用 _dump_meta.json 的 written_len（消息数）作为计数器，不依赖条目数。
    round_context: 当前轮次的状态（上轮消耗/累计消耗），非空时写入 message1。
    round_usage: 当前轮次的 usage，累加到 _dump_meta.json 的累计字段（持久化）。
    """
    import json  # 局部 import（与原文件保持一致）
    from common.experience_core import update_experience, load_experience, _write_experience_file
    from character import get_character_dir, get_history_path
    from character.history import History
    from yinao.ipu_client.icp_tracker import _usage_to_icp

    # 始终从磁盘读取最新状态
    hp = str(get_history_path(character_name))
    hist = History(hp).load()
    all_msgs = hist.messages

    # history.json 直接存储对话消息 [user1, assistant1, user2, assistant2, ...]
    dialogue_msgs = all_msgs

    # 读取 _dump_meta.json 的 written_len（对话消息计数）
    meta_path = get_character_dir(character_name) / "_dump_meta.json"
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    current_written = meta.get("written_len", 0)

    # 累加本轮 usage 到 _dump_meta.json 的累计字段（持久化，跨重启累计）
    if round_usage:
        icp = _usage_to_icp(round_usage)
        meta["prompt_icp"] = meta.get("prompt_icp", 0) + icp["prompt_icp"]
        meta["completion_icp"] = meta.get("completion_icp", 0) + icp["completion_icp"]
        meta["total_icp"] = meta.get("total_icp", 0) + icp["total_icp"]
        meta["thinking_icp"] = meta.get("thinking_icp", 0) + icp["thinking_icp"]

    # 写入状态区块 (message1) — 与增量消息逻辑独立，先于 early return，
    # 即使本轮无新消息（history 已与 disk 同步），也要把 round_context 持久化。
    # 跳过占位标题（"# 状态" 单独一行，无任何数据），否则会把原占位符覆盖。
    if round_context and round_context.strip() != "# 状态":
        blocks = load_experience(character_name)
        if blocks[1] != round_context:
            blocks[1] = round_context
            path = get_character_dir(character_name) / "experience.md"
            _write_experience_file(path, blocks)

    # 没有新增则跳过增量部分（状态已写）——但仍要落盘累计字段
    if len(dialogue_msgs) <= current_written:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return

    # 只写未写部分
    new_msgs = dialogue_msgs[current_written:]
    if not new_msgs:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return

    update_experience(character_name, "dump", {
        "messages": new_msgs, "_meta": meta, "character_name": character_name,
        "round_context": round_context, })

    # 同步 _dump_meta.json（用消息数，而非条目数）
    meta["written_len"] = len(dialogue_msgs)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
