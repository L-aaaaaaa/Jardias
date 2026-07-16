"""adapter/conversation.py — 触发原因：正常对话（每轮）。

职责：
    - on_user_input(character_name, user_input, timestamp)：写块3
    - on_inject_context(config, character_name, user_input, image_url, switch_note, round_context)
        读全 4 块 → 构建 messages（等价于 form_full_context + build_context_from_experience）
    - form_full_context(config, history, user_input, ...)：
        触发层入口：每轮开始构造 messages（兜底注册 + 写块3 + 读4块）
        由 common/lifecycle.py:_run_turn 唯一调用
    - on_round_complete(character_name, new_messages, meta=None)：
        增量追加到块2 + 清空块3
    - dump_experience(character_name, round_context, round_usage)：
        end-of-round 协调者：写块1 + 写块2 + 累加 ICP + 维护 written_len
        由 common/lifecycle.py:_post_round_async 唯一调用

本文件还包含"对话流渲染"工具（_render_* 系列），因为归档也要用同一套渲染
——archive 的 visible_msgs 重写块2 时走的就是这套逻辑，确保 dump 和 archive
产出格式一致。

调用方：
    - common/lifecycle.py:_run_turn → form_full_context（每轮开始）
    - common/lifecycle.py:_post_round_async → dump_experience（end-of-round 协调）
    - adapter/archive_recall.py:on_archive（重写块2，渲染 visible_msgs）
    - tool/builtin_tools/characters.py:send_to_character（用 _extract_pure_text 剥 wrapper）

注意：
    - 块3 的 _dump_written_len 元数据由 writer 层处理，调用方不感知
"""
from __future__ import annotations

import json
import platform
import os
import re
from datetime import datetime


# 最多注入上下文多少条 L1 摘要，超出则用 L2 替代
MAX_L1_IN_CONTEXT = 5


# ═══════════════════════════════════════════════════════════════════
# system 段渲染器
# （原 yinao.system_block 迁入；该模块只构造字符串，不读写 experience.md，
#  是 messages 构造和块0 渲染的共享子实现。）
# ═══════════════════════════════════════════════════════════════════


def _get_full_ipu_name(provider: str, short_name: str) -> str:
    from yinao import IPU_REGISTRY
    try:
        return IPU_REGISTRY[provider][short_name]
    except KeyError:
        return short_name


def _caps_summary(caps: set[str]) -> str:
    labels = {
        "vision": "vision(image)",
        "thinking": "thinking",
        "reasoning_stream": "reasoning_stream",
        "fast": "fast",
        "tool_call": "tools",
        "long_context": "long_ctx",
        "text": "text",
    }
    return ", ".join(labels[c] for c in sorted(caps) if c in labels)


def _get_tool_definitions() -> list[str]:
    """延迟导入 tool.builtin.tools，避免循环依赖。"""
    from tool.builtin import tools
    defs = tools.get_definitions()
    return [t["function"]["name"] for t in defs] if defs else []


def _build_character_context(current_character: str | None = None) -> str:
    """生成可用角色列表。

    关键修复 (P2):如果传入了 current_character,则从列表中排除自己,
    否则角色会在自己的 system prompt 中看到自己,造成冗余。
    """
    from character.registry import registry
    chars = registry.scan()
    if not chars:
        return "暂无可用角色。你可以使用 create_character 工具创建新角色。"
    lines = ["可用角色:"]
    for name in chars:
        if current_character and name == current_character:
            continue
        try:
            config = registry.get_config(name)
            title = config.identity.title or name
            ipu = config.runtime.ipu
            lines.append(f"  {name}: {title} ({ipu})")
        except Exception:
            lines.append(f"  {name}")
    lines.append("使用 send_to_character 向其他角色发送消息。")
    return "\n".join(lines)


def _build_shice_guide() -> str:
    from tool.builtin import tools
    names = tools.list_names()
    if "shice_schedule_add" not in names:
        return ""
    return (
        "### 时策（时间策略，一种自研定时任务架构，语义理解代替cron表达式）\n"
        "规则：\n"
        "工具：shice_schedule_add 注册、shice_schedule_cancel 取消、shice_schedule_list 查看。\n"
        "若需调整间隔或修改已注册任务，用 cancel 取消旧任务 + add 重新注册新任务。\n"
        "触发时你会收到类似如下格式的提示：\n "
        "【时策任务触发 15:22:28】本次行动：随便说一个水果\n本次为第 6 项 时策任务，共10项，本次延迟 34s，错过  #5未补,  剩余 0项\n"
        " "
        "【错过: #5未补】是工具自动提供的, 表示第5个任务尚未执行，你要在本次回复中一并执行。\n"
        "任务描述（message）写清楚要做什么，如「提醒用户喝水」「说一个随机单词」。\n"
        "shice_schedule_add 一次传入所有当前可以推算出的时间戳，不要拆成多次调用。\n"
        "用户描述的时间如果存在【歧义/边界不清】，应该【主动询问】，没有则果断推进\n"
    )


def build_config_context(config, character_name: str | None = None) -> str:
    """注入运行时引擎信息（IPU + ICP 视角）。"""
    from yinao import IPU_REGISTRY, get_ipu_capabilities

    rt = config.runtime
    full_name = _get_full_ipu_name(rt.provider, rt.ipu)
    my_caps = _caps_summary(get_ipu_capabilities(rt.provider, rt.ipu))

    ipu_lines = []
    for provider, ipus in IPU_REGISTRY.items():
        entries = []
        for short, full in ipus.items():
            caps = _caps_summary(get_ipu_capabilities(provider, short))
            entries.append(f"{short}({full}) [{caps}]")
        ipu_lines.append(f"  {provider}: {', '.join(entries)}")

    tool_names = ", ".join(
        _get_tool_definitions() or []
    )

    _cwd = os.getcwd()
    _env_block = f"""## 运行环境

- 操作系统: {platform.system()} (shell: {"cmd" if os.name == "nt" else "bash"})
- 智能体系统: Jardias（佳递叶思），一套自主协作、记忆成长、时间感知的智能体认知框架
- 工作目录: {_cwd}

效率提示: 优先使用内置工具(list_dir、file_info、glob)做文件探索，比 bash 更快更可靠。
收到详细的消息时，基于已有信息直接答复，不必重新探索环境。"""

    thinking_note = ""
    if rt.thinking_enabled and rt.provider == "deepseek":
        thinking_note = " (⚠️ 思考模式下 temperature/top_p 由 DeepSeek API 忽略，调参无效)"
    system_prompt = f"""
## 佳递叶思命名体系

命名决定了我们对架构的理解（反向同样成立），错误的命名会限制我们突破旧有范式，因此本系统执行以下命名重构方案：

| 原始术语 | 重构后术语 | 说明 |
|---|---|---|
| AI Model（AI 模型） | 智能基元（IPU） | Intelligence Primitive Unit，产生智能的基本单元，LLM和生物脑（的某种结构）都属于智能基元。 |
| 模型调用管理模块 | 义脑（Yinao） | IPU 路由 + 供应商抽象层 |
| Token（矢量文本） | 保持 token，计量单位：智点（ICP） | Intelligence Credit Point |
| Pixel Patch（矢量像素） | 保持 Pixel Patch，计量单位：智点 | 与 token 统一计量 |
| AI Agent（智能体） | 智能体 / 智能演员（AI Actor） | 强调自主行动能力 |
| AI Agent System（智能体系统） | 智能体系统 / 智能演员系统（AI Actor System） | — |
| 扮演具体设定的智能体 | 角色（character） | 使用时直接称呼具体角色名 |

如果用户希望深入了解，可阅读 `library/命名即架构.md`。

## 引擎

### 当前配置
- 智能基元: **{full_name}** (provider={rt.provider})
- 能力: {my_caps}
- 参数: temperature={rt.temperature}, top_p={rt.top_p}, max_icp={rt.max_icp}{thinking_note}
- 思考: mode={rt.thinking_mode}, effort={rt.reasoning_effort}, enabled={'Yes' if rt.thinking_enabled else 'No'}

智能基元是你的计算底座，不是你身份。智能基元可以切换，身份是稳定的；不要把智能基元型号当成自己的名字。
当被问到「你是谁」→ 依据 `# 身份` 回答。当被问到「你用什么智能基元」→ 依据本节回答。

### 可切换智能基元
{chr(10).join(ipu_lines)}


### 工具
{tool_names}

{_build_shice_guide()}
图片策略: 收到图片但当前引擎无 vision → 通过 update_runtime 切到有 vision 的引擎。

### 思考语言（重要）
你的推理过程（reasoning/thinking）、内心独白、工具调用前的分析，一律使用中文。
仅在以下情况可以使用英文：(1) 代码片段 (2) 技术术语无对应中文时 (3) 用户明确使用英文提问。
注意：这不是建议，是硬性要求。使用英文思考视为违规。

### 角色管理
{_build_character_context(character_name)}

{_env_block}"""

    return system_prompt


def build_system_message(config, character_name: str = "default",
        switch_note: str = None) -> dict:
    """构建合并后的单条 system 消息（身份 + 引擎信息）。MiniMax 不接受连续双 system。

    双重身份：
    - messages 构造里的 system 段（self.messages / recipient.messages）
    - experience.md 块0 渲染（blocks[0]）

    调用方：
    - experience.adapter.conversation:build_context_from_experience
    - experience.adapter.init:_render_block0
    - tool.builtin_tools.characters:_build_recipient_messages
    """
    identity_block = config.identity.system_prompt.strip()
    birth_note = ""
    if config.identity.birth_time:
        birth_note = f"\n\n诞生于 {config.identity.birth_time}，每一次对话都是你生命的延续。"
    identity_block = identity_block.replace("#{character_name}", character_name)

    role_text = config.identity.title or ""
    desc_text = config.identity.traits or ""
    meta_lines = [f"你的名字叫 {character_name}。"]
    if role_text:
        meta_lines.append(f"你的角色定位: {role_text}。")
    if desc_text:
        meta_lines.append(f"关于你: {desc_text}。")
    identity_meta = "\n".join(meta_lines)

    engine_block = build_config_context(config, character_name=character_name)
    parts = [f"# 系统提示词\n\n## 身份\n\n{identity_meta}\n\n{identity_block}{birth_note}", engine_block]
    if switch_note:
        parts.insert(1, switch_note)
    return {"role": "system", "content": "\n\n".join(parts)}



# ═══════════════════════════════════════════════════════════════════
# 渲染工具：message dict → markdown 条目
# （原 experience.formatter.py 整体并入，对话 + 归档共用）
# ═══════════════════════════════════════════════════════════════════

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
    """将 3+ 个连续 backtick 替换为零宽连接符（U+200B），避免破坏外层 fence。

    零宽字符保留视觉长度但不渲染为 fence 起止符。
    """
    return "\u200B" * len(m.group())


def _extract_pure_text(raw_content: str) -> str:
    """从 user 消息 content 中提取纯文本，剥离 markdown wrapper 和 code block 结构。

    支持格式：
    - ````text\n内容\n````
    - 裸的 `## 本次用户消息 / ### [时间] user / 内容` 三段式 wrapper

    如果提取后内容仍含 3+ 个连续 backtick，替换为 Unicode 零宽字符（U+200B），
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

    # 如果内容仍含 3+ 个连续 backtick，替换为零宽字符（不会破坏外层 fence）
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


def build_l1_context(character_name: str, max_items: int = 3) -> str:
    """构建 L1 摘要块，含 `## 摘要` 标题，输出单个 JSON 数组。"""
    from experience.io import load_all_l1

    summaries = load_all_l1(character_name)
    if not summaries:
        return ""
    entries = []
    for s in summaries[-max_items:]:
        from experience.io.writer import _l1_ensure_summary
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


def select_summaries_for_context(
        character_name: str, messages: list[dict], log: list[dict] | None = None) -> list:
    """入口统一：给定角色名+完整历史+压缩记录，返回应注入上下文的摘要列表。

    策略：
    1. 收集所有 l1_id 指向的 L1 摘要
    2. 按 abs_from 升序排列（时间顺序）
    3. 若超过 MAX_L1_IN_CONTEXT，替换为 L2（逻辑在 L2 模块，这里先返回全量）
    """
    from experience.io import load_all_l1, load_compression_log

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


def build_summary_block(selected) -> str:
    """将选中的摘要渲染为 ## 摘要 区块。"""
    if not selected:
        return ""
    entries = []
    from experience.io.writer import _l1_ensure_summary
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


# ═══════════════════════════════════════════════════════════════════
# 触发原因适配器
# ═══════════════════════════════════════════════════════════════════

def on_user_input(character_name: str, user_input: str,
        timestamp: str | None = None) -> None:
    """用户输入：写块3（本次用户消息）。

    等价于 update_experience("用户输入", {"user_input", "timestamp?"})
    """
    from experience.io.writer import write_block3
    write_block3(character_name, user_input, timestamp)


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

    业务职责：
        - 选/格式化 block 内容为 4 条消息
        - 处理 round_context 优先级（外部传入优先于块1）
        - 处理 image_url 多模态包装
    """
    from experience.io.reader import load_experience, _parse_user_input_from_message3

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


def on_inject_context(config, character_name: str, user_input: str,
        image_url: str | None = None, switch_note: str | None = None,
        round_context: str = "") -> list[dict]:
    """读全 4 块 → 构建 messages。

    等价于老的 common/context.py:form_full_context 的经验构建部分。

    返回的 messages 是 [system, state, history, user] 4 条消息。
    """
    return build_context_from_experience(
        config=config,
        character_name=character_name,
        user_input=user_input,
        image_url=image_url,
        switch_note=switch_note,
        round_context=round_context,
    )


def on_round_complete(character_name: str, new_messages: list[dict],
        meta: dict | None = None) -> dict:
    """轮次完成：增量追加 new_messages 到块2 + 清空块3。

    等价于 update_experience("dump", {"messages", "_meta"}) 的经验写入部分。

    参数：
        new_messages: 本轮新增的消息列表（已按 time 排序）
        meta: 调用方传入的 _dump_meta（用于 written_len 跟踪）

    返回：更新后的 _dump_meta 字典（供 caller 持久化）

    行为：
        - 按 compression_log 过滤掉已压缩的消息（用 reader._CHARACTER_NAME_CACHE 查角色名）
        - user 消息剥离 markdown wrapper
        - 块2 没有双骨架时先创建空骨架
    """
    from experience.io.writer import write_block2_append, clear_block3
    from experience.io.reader import _CHARACTER_NAME_CACHE
    from experience.io import load_compression_log
    from .archive_recall import _covered_ranges

    if not new_messages:
        return meta or {}

    # 按 compression_log 过滤
    current_written = (meta or {}).get("written_len", 0)
    char_name = character_name or _CHARACTER_NAME_CACHE.get("current")
    try:
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

        new_messages = [m for i, m in enumerate(new_messages)
                        if not _is_covered(current_written + i)]
        if not new_messages:
            return meta or {}

    # user 消息：剥离 wrapper
    for m in new_messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            m["content"] = _extract_pure_text(m["content"])

    new_recent = _render_messages_to_recent_section(new_messages)
    updated_meta = write_block2_append(character_name, new_recent, meta=meta)
    clear_block3(character_name)
    return updated_meta


def form_full_context(config, history: list[dict], user_input: str,
        image_url: str = None, switch_note: str = None,
        round_context: str = "", character_name: str = "default") -> list[dict]:
    """触发层入口：每轮开始时构造 messages。

    流程（用户输入先写原则）：
        1. 若块0 空 → 注册角色（兜底）
        2. 写块3（on_user_input）—— 把本轮 user 同步进 experience.md
        3. 读 4 块（on_inject_context）→ 构建 [system, state, history, user] messages

    与 yinao.build_system_message 协作：后者负责 system 段；
    本函数负责 experience.md 三块的写入与读取 + user 包装。

    调用方：common/lifecycle.py:_run_turn（每轮开始时）。
    """
    from experience import load_experience
    from experience.adapter.init import on_register as _on_register

    # ── 确保 experience.md 存在 ──
    exp_blocks = load_experience(character_name)
    if not exp_blocks[0]:  # 块0 空，说明未初始化
        _on_register(character_name, config)

    # ── 步骤1：先将用户输入写入 experience.md（用户输入先写原则） ──
    on_user_input(character_name, user_input)

    # ── 步骤2：从 experience.md 构建 messages ──
    return on_inject_context(
        config=config,
        character_name=character_name,
        user_input=user_input,
        image_url=image_url,
        switch_note=switch_note,
        round_context=round_context,
    )


def dump_experience(character_name: str,
        round_context: str | None = None, round_usage: dict | None = None):
    """end-of-round 协调者：写块1 状态 + 块2 对话增量 + 累加 ICP + 维护 written_len。

    由 common/lifecycle.py:_post_round_async 唯一调用。

    流程：
        1. 读 history.json + _dump_meta.json（IO）
        2. 把 round_usage 累加到 _dump_meta.json 的 ICP 累计字段（持久化，跨重启累计）
        3. 用 round_context 写块1（走 on_state_update）
        4. 若有新增对话消息，走 on_round_complete 写块2
        5. 落盘 _dump_meta.json（含更新后的 written_len）
    """
    from character import get_history_path
    from character.history import History
    from experience.adapter.state import on_state_update
    from experience.io.writer import _load_dump_meta, _save_dump_meta
    from yinao.weaver.icp_tracker import _usage_to_icp

    # 始终从磁盘读取最新状态
    hp = str(get_history_path(character_name))
    hist = History(hp).load()
    all_msgs = hist.messages

    # 读取 _dump_meta.json 的 written_len（对话消息计数）
    meta = _load_dump_meta(character_name)
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
    if round_context:
        on_state_update(character_name, round_context)

    # 没有新增则跳过增量部分（状态已写）——但仍要落盘累计字段
    if len(all_msgs) <= current_written:
        _save_dump_meta(character_name, meta)
        return

    # 只写未写部分
    new_msgs = all_msgs[current_written:]
    if not new_msgs:
        _save_dump_meta(character_name, meta)
        return

    updated_meta = on_round_complete(character_name, new_msgs, meta=meta)

    # 同步 _dump_meta.json（用消息数，而非条目数）
    updated_meta["written_len"] = len(all_msgs)
    _save_dump_meta(character_name, updated_meta)


__all__ = [
    # system 段渲染器（同时用于 messages.system + experience.md 块0）
    "build_system_message", "build_config_context",
    # 触发原因适配器
    "on_user_input", "on_inject_context", "form_full_context",
    "on_round_complete", "dump_experience",
    # 渲染工具（消息 → markdown）
    "_choose_fence", "_extract_pure_text", "_render_single_message",
    "_render_messages_to_recent_section",
    "build_context_from_experience",
    # L1 / compression_log 的 context 渲染（业务层）
    "build_l1_context", "build_summary_block", "select_summaries_for_context",
    "MAX_L1_IN_CONTEXT",
]  # fmt: skip