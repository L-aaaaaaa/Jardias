"""context — 上下文构建：将 AgentConfig + History → LLM 可消费的 messages。"""
import re as _re_module
from datetime import datetime

from agent_config import MODEL_NAMES, get_model_capabilities
from tool.builtin import tools
from character.summarizer import build_l1_context


def _build_character_context() -> str:
    from character.registry import registry
    chars = registry.scan()
    if not chars:
        return "暂无可用角色。你可以使用 create_character 工具创建新角色。"
    lines = ["可用角色:"]
    for name in chars:
        try:
            config = registry.get_config(name)
            title = config.identity.title or name
            model = config.runtime.model
            lines.append(f"  {name}: {title} ({model})")
        except Exception:
            lines.append(f"  {name}")
    lines.append("使用 send_to_character 向其他角色发送消息。")
    return "\n".join(lines)


def _get_full_model_name(provider: str, short_name: str) -> str:
    try:
        return MODEL_NAMES[provider][short_name]
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


def build_config_context(config) -> str:
    """注入运行时引擎信息。"""
    rt = config.runtime
    full_name = _get_full_model_name(rt.provider, rt.model)
    my_caps = _caps_summary(get_model_capabilities(rt.provider, rt.model))

    models_lines = []
    for provider, models in MODEL_NAMES.items():
        entries = []
        for short, full in models.items():
            caps = _caps_summary(get_model_capabilities(provider, short))
            entries.append(f"{short}({full}) [{caps}]")
        models_lines.append(f"  {provider}: {', '.join(entries)}")

    tool_names = ", ".join(
        tools.get_definitions() and [t["function"]["name"] for t in tools.get_definitions()] or []
    )

    # ── 运行环境信息 ──
    import platform as _platform, os as _os
    _cwd = _os.getcwd()
    _env_block = f"""## 运行环境

- 操作系统: {_platform.system()} (shell: {"cmd" if _os.name == "nt" else "bash"})
- 工作目录: {_cwd}

效率提示: 优先使用内置工具(list_dir、file_info、glob)做文件探索，比 bash 更快更可靠。
收到详细的消息时，基于已有信息直接答复，不必重新探索环境。"""

    thinking_note = ""
    if rt.thinking_enabled and rt.provider == "deepseek":
        thinking_note = " (⚠️ 思考模式下 temperature/top_p 由 DeepSeek API 忽略，调参无效)"

    return f"""## 引擎

### 当前配置
- 模型: **{full_name}** (provider={rt.provider})
- 能力: {my_caps}
- 参数: temperature={rt.temperature}, top_p={rt.top_p}, max_tokens={rt.max_tokens}{thinking_note}
- 思考: mode={rt.thinking_mode}, effort={rt.reasoning_effort}, enabled={'Yes' if rt.thinking_enabled else 'No'}

引擎是你的计算底座，不是你身份。引擎可以切换，身份是稳定的；不要把引擎型号当成自己的名字。
当被问到「你是谁」→ 依据 `# 身份` 回答。当被问到「你用什么模型」→ 依据本节回答。

### 可切换模型
{chr(10).join(models_lines)}

### 工具
{tool_names}

图片策略: 收到图片但当前引擎无 vision → 通过 update_runtime 切到有 vision 的引擎。
思考语言: 中文，除非涉及英文术语或代码。

### 角色管理
{_build_character_context()}

{_env_block}"""


def strip_context_wrapper(message: str) -> str:
    """剥离 form_full_context 用户消息的结构化外壳。

    form_full_context 在每条用户消息外加了：
      ## 本次用户消息\n\n### [ts] user:\n\n```text\n{content}\n```

    当 LLM 调用 send_to_character 时可能将整块格式当作消息正文传入，
    导致嵌套。此函数逆向提取原始消息正文。
    """
    if not message:
        return message

    # 模式：## 本次用户消息 开头，含 时间戳行 和 text 代码围栏
    pattern = (
        r'^##\s*本次用户消息\s*'
        r'\n+###\s*\[[^\]]+\]\s*user:\s*'
        r'\n+```text\s*\n'
        r'(.*?)'
        r'\n```\s*$'
    )
    m = _re_module.match(pattern, message, _re_module.DOTALL)
    if m:
        return m.group(1).strip()
    return message


def _build_recent_history(history: list[dict], keep_turns: int = 6) -> str:
    """将最近 N 轮对话格式化为文本块。"""
    if not history:
        return "## 近期对话原文\n\n（暂无对话记录）"

    messages: list[str] = []
    cutoff = max(0, len(history) - keep_turns * 2)
    for msg in history[cutoff:]:
        role = msg.get("role", "unknown")
        t = msg.get("time", "")
        header = f"[{t}] {role}:" if t else f"{role}:"
        content = msg.get("content", "")
        fence = "```"
        if fence in content:
            fence = "````"
            if "````" in content:
                fence = "`````"
        messages.append(f"### {header}\n\n{fence}text\n{content}\n{fence}")

    return "## 近期对话原文\n\n" + "\n\n".join(messages)


def build_system_message(config, character_name: str = "default",
                         switch_note: str = None) -> dict:
    """构建合并后的单条 system 消息（身份 + 引擎信息）。MiniMax 不接受连续双 system。"""
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

    engine_block = build_config_context(config)
    parts = [f"# 系统提示词\n\n## 身份\n\n{identity_meta}\n\n{identity_block}{birth_note}", engine_block]
    if switch_note:
        parts.append(switch_note)
    return {"role": "system", "content": "\n\n".join(parts)}


def form_full_context(config, history: list[dict], user_input: str,
        image_url: str = None, switch_note: str = None,
        round_context: str = "", character_name: str = "default") -> list[dict]:
    """固定 3 消息 + 用户输入 = 消息数 O(1)。

    messages[0] = system   → # 系统提示词 (身份 + 引擎)
    messages[1] = user     → # 状态 (上轮 token 等)
    messages[2] = user     → # 历史 (# 摘要 + # 近期对话原文)
    messages[3] = user     → 本次用户消息
    """
    result: list[dict] = [build_system_message(config, character_name, switch_note)]

    # messages[1]: 状态
    if round_context:
        result.append({"role": "user", "content": round_context})
    else:
        result.append({"role": "user", "content": "# 状态\n\n（首轮对话，暂无消耗数据）"})

    # messages[2]: 历史
    l1_block = build_l1_context(character_name)
    recent_block = (
        _build_recent_history(history)
        if history
        else "## 近期对话原文\n\n（这是你第一次对话，暂无更多历史记录。）"
    )
    history_parts: list[str] = []
    if l1_block:
        history_parts.append(l1_block)
    history_parts.append(recent_block)
    result.append({"role": "user", "content": f"# 历史\n\n" + "\n\n".join(history_parts)})

    # messages[3]: 本次用户消息
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if image_url:
        clean_input = _re_module.sub(
            r'(?:https?://[^\s]+|[A-Za-z]:[\\/][^\s]+)\.(?:png|jpg|jpeg|webp|gif|bmp)(?:\?[^\s]*)?\s*',
            '', user_input, flags=_re_module.IGNORECASE
        )
        clean_input = clean_input.strip() or user_input
        text_block = f"## 本次用户消息\n\n### [{now}] user:\n\n```text\n{clean_input}\n```"
        user_content = [
            {"type": "image_url", "image_url": {"url": image_url}},
            {"type": "text", "text": text_block},
        ]
    else:
        user_content = f"## 本次用户消息\n\n### [{now}] user:\n\n```text\n{user_input}\n```"
    result.append({"role": "user", "content": user_content})

    return result
