"""context — 上下文构建：将 ActorConfig + History → IPU 可消费的 messages。"""
import re as _re_module

from tool.builtin import tools
from yinao import IPU_REGISTRY, get_ipu_capabilities


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
        # 排除自己
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


def _get_full_ipu_name(provider: str, short_name: str) -> str:
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
    """注入运行时引擎信息（IPU + ICP 视角）。

    character_name 用于在角色列表中排除自己。
    """
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
        tools.get_definitions() and [t["function"]["name"] for t in tools.get_definitions()] or []
    )

    import platform as _platform, os as _os
    _cwd = _os.getcwd()
    _env_block = f"""## 运行环境

- 操作系统: {_platform.system()} (shell: {"cmd" if _os.name == "nt" else "bash"})
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


def strip_context_wrapper(message: str) -> str:
    if not message:
        return message
    pattern = (
        r'^##\s*本次用户消息\s*'
        r'\n+###\s*\[[^\]]+\]\s*user(?:\s*\(t_sent=\d+ms\))?:\s*'
        r'\n+```(?:text|json)\s*\n'
        r'(.*?)'
        r'\n```\s*$'
    )
    m = _re_module.match(pattern, message, _re_module.DOTALL)
    if m:
        return m.group(1).strip()
    return message


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

    engine_block = build_config_context(config, character_name=character_name)
    parts = [f"# 系统提示词\n\n## 身份\n\n{identity_meta}\n\n{identity_block}{birth_note}", engine_block]
    if switch_note:
        parts.insert(1, switch_note)
    return {"role": "system", "content": "\n\n".join(parts)}


def form_full_context(config, history: list[dict], user_input: str,
        image_url: str = None, switch_note: str = None,
        round_context: str = "", character_name: str = "default") -> list[dict]:
    """固定 3 消息 + 用户输入 = 消息数 O(1)。"""
    from common.experience_core import (
        build_context_from_experience,
        update_experience,
        load_experience,
    )

    # ── 确保 experience.md 存在 ──
    exp_blocks = load_experience(character_name)
    if not exp_blocks[0]:  # message0 为空，说明未初始化
        from common.experience_core import init_experience
        init_experience(character_name, config)

    # ── 步骤1：先将用户输入写入 experience.md（用户输入先写原则） ──
    update_experience(character_name, "用户输入", {"user_input": user_input})

    # ── 步骤2：从 experience.md 构建 messages ──
    return build_context_from_experience(
        config=config,
        character_name=character_name,
        user_input=user_input,
        image_url=image_url,
        switch_note=switch_note,
        round_context=round_context,
    )
