from __future__ import annotations

import inspect as _inspect
import pathlib
import re

from common.logger import logger
from data_shape import ToolDef

# ── 工具执行调度表（延迟赋值，避免循环导入）──
_BUILTIN_HANDLERS: dict[str, callable] = {}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool_def: ToolDef):
        self._tools[tool_def.name] = tool_def

    def get_definitions(self) -> list[dict]:
        result = []
        for name, tool in self._tools.items():
            result.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
            })
        return result

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    async def execute(self, name: str, arguments: dict) -> str:
        handler = _BUILTIN_HANDLERS.get(name)
        if handler is not None:
            if _inspect.iscoroutinefunction(handler):
                return await handler(arguments)
            try:
                return handler(arguments)
            except TypeError:
                return handler()

        # ── 普通工具（通过 ToolRegistry 注册的文件工具等）──
        tool = self._tools.get(name)
        if not tool:
            return f"[Error] tool not found: {name}"
        if not tool.fn:
            return f"[Error] tool {name} has no implementation"
        try:
            result = await tool.fn(**arguments)
            return str(result) if result is not None else ""
        except TypeError as e:
            missing = _find_missing_param(e, tool.parameters)
            if missing:
                return f"[Error] missing required param: {missing}"
            return f"[Error] {e}"
        except Exception as e:
            return f"[Error] {type(e).__name__}: {e}"

    def register_file_tools(self):
        _ensure_tools()
        for tool_def in FILE_TOOLS:
            self.register(tool_def)


# ── 当前操作的 agent 名（由 app.py 设定） ──
_current_agent: str = "default"


def set_agent(name: str):
    """设置当前操作用 agent 名（app.py 启动时调用）。"""
    global _current_agent
    _current_agent = name


# ── 自手术工具实现 ──

def _handle_update_runtime(arguments: dict) -> str:
    """更新运行时参数（model/temperature/top_p/max_tokens/thinking_mode 任意组合）。
    如果 model 变了 → 抛 ModelSwitched 让 app.py 重建 client。
    其他参数 → 直接写 JSON，下轮生效。
    """
    from agent_config import load_config, save_config
    from model_client.model_context import resolve_provider, get_actual_model, is_provider_available, \
        get_circuit_status

    config = load_config(_current_agent)
    rt = config.runtime
    actual_model = get_actual_model()  # 实际运行引擎（fallback 后可能与文件不同）

    changes = []
    model_changed = False

    if "model" in arguments:
        new_model = arguments["model"]
        # 比较 config 文件 AND 实际运行状态（fallback 可能未持久化）
        if new_model != rt.model or (actual_model and new_model != actual_model):
            # 切换前检查目标供应商是否已熔断
            provider = resolve_provider(new_model)
            if provider and not is_provider_available(provider):
                status = get_circuit_status().get(provider, {})
                remain = status.get("reset_remaining_sec", "?")
                last_err = status.get("last_error", "")
                # 格式化所有供应商状态（LLM 友好）
                all_status = get_circuit_status()
                status_lines = []
                for p, s in all_status.items():
                    label = "🟢" if s.get("available", True) else "🔴"
                    extra = ""
                    if not s.get("available", True):
                        extra = f" (熔断, {s.get('reset_remaining_sec', '?')}s 后恢复)"
                    status_lines.append(f"  {label} {p}{extra}")
                formatted_status = "\n".join(status_lines) if status_lines else "  (无状态)"
                return (
                    f"[Error] {provider} 当前不可用（已熔断，{remain}s 后自动恢复）。\n"
                    f"原因: {last_err}\n"
                    f"当前供应商状态:\n{formatted_status}"
                )
            rt.model = new_model
            changes.append(f"model={new_model}")
            model_changed = True

    if "temperature" in arguments:
        t = float(arguments["temperature"])
        if t < 0 or t > 2:
            return f"[Error] temperature must be in [0, 2], got {t}"
        rt.temperature = t
        changes.append(f"temperature={t}")

    if "top_p" in arguments:
        p = float(arguments["top_p"])
        if p < 0 or p > 1:
            return f"[Error] top_p must be in [0, 1], got {p}"
        rt.top_p = p
        changes.append(f"top_p={p}")

    if "max_tokens" in arguments:
        n = int(arguments["max_tokens"])
        if n <= 0:
            return f"[Error] max_tokens must be positive, got {n}"
        rt.max_tokens = n
        changes.append(f"max_tokens={n}")

    if "thinking_enabled" in arguments:
        enabled = bool(arguments["thinking_enabled"])
        rt.thinking_enabled = enabled
        changes.append(f"thinking_enabled={enabled}")
        if not enabled and rt.reasoning_effort:
            # DeepSeek: thinking=disabled 时不能传 reasoning_effort，否则 400
            old_effort = rt.reasoning_effort
            rt.reasoning_effort = ""
            changes.append(f"reasoning_effort=(自动清除 {old_effort}，关闭 thinking 时不可设 reasoning_effort)")

    if "reasoning_effort" in arguments:
        effort = arguments["reasoning_effort"].lower()
        if effort not in ("high", "max"):
            return f"[Error] reasoning_effort must be high/max, got {effort}"
        if not rt.thinking_enabled:
            # DeepSeek: 设 reasoning_effort 必须开 thinking，否则 400
            rt.thinking_enabled = True
            changes.append("thinking_enabled=(自动开启，reasoning_effort 需 thinking 支持)")
        rt.reasoning_effort = effort
        changes.append(f"reasoning_effort={effort}")

    if "thinking_mode" in arguments:
        mode = arguments["thinking_mode"].lower()
        if mode not in ("enabled", "disabled", "auto"):
            return f"[Error] thinking_mode must be enabled/disabled/auto, got {mode}"
        rt.thinking_mode = mode
        changes.append(f"thinking_mode={mode}")

    if not changes:
        return "[OK] no changes (all values match current)"

    save_config(config, _current_agent)

    if model_changed:
        provider = resolve_provider(rt.model)
        if provider is None:
            return f"[Error] 无法解析模型 '{rt.model}' 的 provider。可用模型: 2.7快, 2.7, chat, 千问3.6+, kimi 2.5, glm-5, M2.5"
        if rt.model == provider:
            from agent_config import MODEL_NAMES
            first_model = next(iter(MODEL_NAMES[provider].keys()))
            rt.model = first_model
        rt.provider = provider
        save_config(config, _current_agent)
        from model_client.model_context import request_switch
        request_switch(provider, rt.model)
        return f"[OK] runtime updated: {', '.join(changes)} → 将切换至 {provider}/{rt.model}"

    return f"[OK] runtime updated: {', '.join(changes)}"


def _handle_update_identity(arguments: dict) -> str:
    """更新身份参数（system_prompt/role/description/max_iterations 任意组合）。
    写 JSON 后下轮生效。
    """
    from agent_config import load_config, save_config

    config = load_config(_current_agent)
    ident = config.identity

    changes = []

    if "system_prompt" in arguments:
        ident.system_prompt = arguments["system_prompt"]
        changes.append("system_prompt")

    if "title" in arguments:
        ident.title = arguments["title"]
        changes.append(f"title={arguments['title']}")

    if "traits" in arguments:
        ident.traits = arguments["traits"]
        changes.append("traits")

    if "max_iterations" in arguments:
        n = int(arguments["max_iterations"])
        if n <= 0:
            return f"[Error] max_iterations must be positive, got {n}"
        ident.max_iterations = n
        changes.append(f"max_iterations={n}")

    if not changes:
        return "[OK] no changes"

    save_config(config, _current_agent)
    return f"[OK] identity updated: {', '.join(changes)}"


# ── 历史摘要工具 ──

async def _handle_summarize_conversation(arguments: dict) -> str:
    """角色主动压缩早期对话历史。"""
    import json
    from datetime import datetime
    from character import get_history_path
    from character.summarizer import (
        L1Summary, _analyze_slice, _guess_topic, _describe_slice, save_l1,
    )

    keep_recent_turns = int(arguments.get("keep_recent_turns", 6))
    topic_hint = arguments.get("topic", "")

    history_path = get_history_path(_current_agent)
    if not history_path.exists():
        return "[Error] 无历史记录"

    with open(history_path, "r", encoding="utf-8") as f:
        messages: list[dict] = json.load(f)

    if not messages:
        return "[OK] 历史为空"

    # 按用户消息算轮次，找到截断点
    user_indices = [i for i, m in enumerate(messages) if m["role"] == "user"]
    total_turns = len(user_indices)
    if total_turns <= keep_recent_turns:
        return f"[OK] 仅 {total_turns} 轮，无需压缩（阈值 {keep_recent_turns}）"

    cutoff_user_idx = total_turns - keep_recent_turns
    cutoff_msg_idx = user_indices[cutoff_user_idx]
    cutoff_time = messages[cutoff_msg_idx].get("time", "")

    compress_slice = messages[:cutoff_msg_idx]

    user_turns, start_t, end_t, events = _analyze_slice(compress_slice)
    topic = topic_hint or _guess_topic(events)
    detail = _describe_slice(user_turns, events, topic)

    now = datetime.now()
    sid = f"L1-{now.strftime('%Y%m%d-%H%M%S')}"

    summary = L1Summary(
        id=sid,
        start_time=start_t,
        end_time=end_t,
        message_count=len(compress_slice),
        user_turns=user_turns,
        topic=topic,
        detail=detail,
        key_events=events,
    )

    saved_path = save_l1(_current_agent, summary)

    lines = [
        f"[摘要已保存] {summary.to_context_string()}",
        f"  详情: {detail}",
        f"  截断位置: {cutoff_time} — 保留最近 {keep_recent_turns} 轮原文",
        f"  文件: {saved_path}",
    ]
    logger.info(f"  📦 角色主动摘要 | {user_turns} 轮 → {topic} | {saved_path}")
    return "\n".join(lines)


# ── 角色管理工具 ──

async def _handle_create_character(arguments: dict) -> str:
    name = arguments["name"]
    system_prompt = arguments["system_prompt"]
    title = arguments.get("title", name)
    traits = arguments.get("traits", "")
    model = arguments.get("model", "v4-pro")
    provider_arg = arguments.get("provider")  # 可能为 None

    from character.registry import registry
    from data_shape import AgentConfig, IdentityConfig, RuntimeConfig
    from agent_config import MODEL_NAMES

    if registry.exists(name):
        return f"[Error] 角色 {name} 已存在"

    if not any(c.isalnum() or c in "_-" for c in name):
        return f"[Error] 角色名只能包含字母数字和下划线"

    # ── 解析 provider ──
    if provider_arg:
        provider = provider_arg
        # 校验 model 在此 provider 下存在
        if model not in MODEL_NAMES.get(provider, {}):
            available = ", ".join(MODEL_NAMES.get(provider, {}).keys())
            return (
                f"[Error] 模型 '{model}' 在供应商 {provider} 下不存在。\n"
                f"{provider} 可用模型: {available if available else '(无)'}"
            )
    else:
        # 未指定 provider → 自动从 MODEL_NAMES 反向查找
        found_providers = [p for p, ms in MODEL_NAMES.items() if model in ms]
        if not found_providers:
            all_models = []
            for p, ms in MODEL_NAMES.items():
                for m in ms:
                    all_models.append(f"{p}/{m}")
            return (
                f"[Error] 模型 '{model}' 在所有供应商中都不存在。\n"
                f"可用模型: {', '.join(all_models)}"
            )
        if len(found_providers) > 1:
            return (
                f"[Error] 模型 '{model}' 存在于多个供应商 ({', '.join(found_providers)})。\n"
                f"请显式指定 provider 参数来消除歧义。"
            )
        provider = found_providers[0]

    config = AgentConfig(
        identity=IdentityConfig(
            system_prompt=system_prompt,
            title=title,
            traits=traits,
        ),
        runtime=RuntimeConfig(
            provider=provider,
            model=model,
            temperature=float(arguments.get("temperature", 1.0)),
            top_p=float(arguments.get("top_p", 0.95)),
            max_tokens=int(arguments.get("max_tokens", 8192)),
            thinking_mode=str(arguments.get("thinking_mode", "auto")),
            reasoning_effort=str(arguments.get("reasoning_effort", "high")),
            thinking_enabled=bool(arguments.get("thinking_enabled", True)),
        ),
    )
    registry.create(name, config)
    return (
        f"[OK] 角色 {name} 已创建\n"
        f"  头衔: {title}\n"
        f"  特质: {traits}\n"
        f"  引擎: {provider}/{model}"
    )


def _handle_list_characters() -> str:
    from character.registry import registry
    chars = registry.scan()
    if not chars:
        return "[OK] 暂无角色"

    lines = [f"共 {len(chars)} 个角色:"]
    for name in chars:
        try:
            config = registry.get_config(name)
            prov = config.runtime.provider
            model = config.runtime.model
            title = config.identity.title or "(未设置头衔)"
            traits = config.identity.traits or "(无描述)"
            active = "(当前)" if name == _current_agent else ""
            lines.append(f"  {name}{active}: {title} | {prov}/{model} | {traits}")
        except Exception:
            lines.append(f"  {name}: (配置读取失败)")
    return "\n".join(lines)


async def _handle_send_to_character(arguments: dict) -> str:
    recipient = arguments["recipient"]
    message = arguments["message"]

    # 剥离 form_full_context 的结构化外壳，防止嵌套（详见 strip_context_wrapper）
    from common.context import strip_context_wrapper
    message = strip_context_wrapper(message)

    from character.registry import registry
    if not registry.exists(recipient):
        return f"[Error] 角色 {recipient} 不存在。使用 list_characters 查看可用角色。"

    from character.history import History
    from character import get_history_path

    # ── 1. 获取双方配置 ──
    recipient_config = registry.get_config(recipient)
    recipient_provider = recipient_config.runtime.provider
    recipient_model_short = recipient_config.runtime.model

    # 构建接收者的 context（引擎信息块 + 身份）

    from agent_config import resolve_model as resolve_model_fn
    try:
        recipient_provider_info, recipient_mc = resolve_model_fn(recipient_provider, recipient_model_short)
    except KeyError as e:
        return f"[Error] 角色 {recipient} 配置无效: {e}。请用 update_runtime 修正其 model 参数。"

    # ── 2. 写入接收者历史（接收者视角：收到新消息） ──
    recipient_history = History(str(get_history_path(recipient))).load()
    recipient_history.append_pair(f"[来自 {_current_agent} 的消息]\n{message}", "")

    # ── 同步接收者运行时配置到 model_config（之前遗漏：MC 裸建全是默认值）──
    from model_client.switch import sync_config_to_model
    sync_config_to_model(recipient_config, recipient_mc)

    # ── 3. 构建接收者的 messages（复用 form_full_context 的 system 消息格式）──
    from common.context import build_system_message
    all_msgs = [build_system_message(recipient_config, recipient)]

    is_first = True
    for entry in recipient_history.messages[-20:]:  # 最近 20 条（10 轮）
        role = entry.get("role", "user")
        content = entry.get("content", "")
        if role == "user":
            all_msgs.append({"role": "user", "content": content})
        elif role == "assistant":
            if content:  # 跳过空回复
                all_msgs.append({"role": "assistant", "content": content})
        elif role == "system" and not is_first:
            all_msgs.append({"role": "system", "content": content})
        is_first = False

    # ── 4. 调用接收者的 LLM ──
    from model_client.switch import resolve_chat
    from common.logger import logger as _logger

    _logger.info(
        f"  [send_to_character] {_current_agent} → {recipient} | 引擎 {recipient_provider}/{recipient_model_short} | 历史 {len(all_msgs)} 条")

    from common.utils import set_display_name as _set_dn

    _prev_agent = _current_agent
    _set_dn(recipient)  # 终端显示名 → 接收者
    set_agent(recipient)  # _current_agent → 接收者（update_runtime/update_identity 操作正确目标）

    # ── 调用接收者 LLM，失败时自动尝试其他供应商 ──
    from model_client.model_context import MODEL_NAMES as _MN, list_providers as _list_prov
    tried_providers = {recipient_provider}
    reply = ""
    last_error = ""
    engine_fallback_note = ""  # 记录是否发生了引擎降级

    try:
        while True:
            try:
                chat_fn = resolve_chat(recipient_provider)
                result = await chat_fn(all_msgs, recipient_mc, character_name=recipient)
                reply = ""
                for msg in reversed(result.messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        reply = msg["content"]
                        break
                break  # 成功
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                _logger.error(
                    f"  [send_to_character] {recipient} @ {recipient_provider}/{recipient_model_short} 调用失败: {last_error}")
                # 尝试切换到其他供应商
                available = [p for p in _list_prov() if p not in tried_providers]
                if not available:
                    reply = f"[Error] 调用 {recipient} 的 LLM 失败 ({recipient_provider}/{recipient_model_short}): {last_error}"
                    break
                old_provider = recipient_provider
                old_model = recipient_model_short
                recipient_provider = available[0]
                recipient_model_short = next(iter(_MN.get(recipient_provider, {}).keys()), "v4-flash")
                from agent_config import resolve_model as _rm2
                try:
                    _, recipient_mc = _rm2(recipient_provider, recipient_model_short)
                except KeyError as ke:
                    reply = f"[Error] 无法为 {recipient} 找到可用引擎: {ke}"
                    break
                # 同步新引擎运行时配置
                sync_config_to_model(recipient_config, recipient_mc)
                # 重建系统消息：反映实际运行引擎（非配置的旧引擎）
                all_msgs[0] = build_system_message(recipient_config, recipient)
                tried_providers.add(recipient_provider)
                engine_fallback_note = (
                    f"\n⚠️ 引擎降级：{old_provider}/{old_model} → {recipient_provider}/{recipient_model_short}"
                    f"（原因: {last_error}）"
                )
                _logger.info(
                    f"  [send_to_character] 自动切换 {recipient} → {recipient_provider}/{recipient_model_short}")
    finally:
        set_agent(_prev_agent)  # 恢复 _current_agent
        _set_dn(_prev_agent)  # 恢复终端显示名

    if not reply.strip():
        reply = "(未生成回复)"

    # ── 5. 写入双方历史 ──
    # 发送者历史：完整记录发送+回复（不写空占位，避免异常残留）
    if _current_agent != recipient:
        sender_history = History(str(get_history_path(_current_agent))).load()
        sender_history.append_pair(message, reply)
        sender_history.save()

    # 接收者历史：补填自己的回复
    if recipient_history.messages and recipient_history.messages[-1].get("role") == "assistant":
        recipient_history.messages[-1]["content"] = reply
        recipient_history.save()

    return (
        f"🔔 {recipient} 无法看到你的普通回复——继续对话请调用 send_to_character\n\n"
        f"[来自 {recipient} 的回复]\n\n{reply}\n\n"
        f"(引擎: {recipient_provider}/{recipient_model_short}，"
        f"共 {len(reply)} 字)"
        f"{engine_fallback_note}"
    )


# ── 系统工具 ──

def _handle_bash(arguments: dict) -> str:
    import subprocess

    command = arguments["command"]
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace",
        )
        out = []
        if result.stdout.strip():
            out.append(result.stdout.strip())
        if result.stderr.strip():
            out.append(f"[stderr]\n{result.stderr.strip()}")
        if not out:
            out.append(f"(exit code {result.returncode})")
        return "\n".join(out)
    except subprocess.TimeoutExpired:
        return "[Error] 命令超时（30s）"
    except Exception as e:
        return f"[Error] {type(e).__name__}: {e}"


async def _handle_web_fetch(arguments: dict) -> str:
    url = arguments["url"]
    try:
        from urllib.request import urlopen, Request
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Agent01/1.0)"
        })
        with urlopen(req, timeout=15) as resp:
            content = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = content.decode(charset, errors="replace")
    except Exception as e:
        return f"[Error] 获取失败: {type(e).__name__}: {e}"

    # 简单 HTML 剥离
    import re
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.S | re.I)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)

    max_chars = 4000
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n... (截断，原文共 {len(text)} 字)"
    return text.strip() or "(页面无文字内容)"


# ── 角色切换 ──

_pending_switch: str | None = None
"""非空时表示下轮需切换到指定角色。由 conversation_loop 消费。"""


def clear_pending_switch() -> str | None:
    global _pending_switch
    v = _pending_switch
    _pending_switch = None
    return v


# ── Web 搜索 ──

async def _handle_web_search(arguments: dict) -> str:
    from urllib.request import urlopen, Request
    from urllib.parse import quote

    query = arguments["query"]
    max_results = int(arguments.get("max_results", 5))

    search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    try:
        req = Request(search_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"[Error] 搜索失败: {type(e).__name__}: {e}"

    import re
    results = []
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    links = re.findall(r'class="result__url"[^>]*>(.*?)</a>', html, re.S)
    titles = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', html, re.S)

    for i in range(min(len(titles), len(snippets), max_results)):
        title = re.sub(r'<[^>]+>', '', titles[i]).strip()
        snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
        link = re.sub(r'<[^>]+>', '', links[i]).strip() if i < len(links) else ""
        results.append(f"{i + 1}. {title}\n   {snippet}\n   [{link}]")

    if not results:
        return f"[Info] 搜索结果为空。搜索词: {query}"
    return f"搜索 \"{query}\" (共 {len(results)} 条):\n\n" + "\n\n".join(results)


# ── 工具注册 ──

def _find_missing_param(type_error: TypeError, schema: dict) -> str | None:
    msg = str(type_error)
    m = re.search(r"missing 1 required positional argument: '(\w+)'", msg)
    if m:
        return m.group(1)
    return None


def _resolve_path(path: str) -> pathlib.Path:
    p = pathlib.Path(path).expanduser()
    if not p.is_absolute():
        p = pathlib.Path.cwd() / p
    return p.resolve()


async def _read_file(path: str, line_range: str | None = None) -> str:
    try:
        resolved = _resolve_path(path)
        if not resolved.exists():
            return f"[Error] file not found: {path}"
        content = resolved.read_text(encoding="utf-8", errors="replace")
        if line_range:
            parts = line_range.split(",")
            if len(parts) == 2:
                start, end = int(parts[0]), int(parts[1])
                lines = content.split("\n")
                start = max(1, start) - 1
                end = min(len(lines), end)
                content = "\n".join(lines[start:end])
        return content
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return f"[Error] {type(e).__name__}: {e}"


async def _write_file(path: str, content: str, mode: str = "w") -> str:
    try:
        resolved = _resolve_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if mode == "a" and resolved.exists():
            current = resolved.read_text(encoding="utf-8")
            content = current + content
        resolved.write_text(content, encoding="utf-8")
        return f"[OK] wrote {len(content)} chars to {path}"
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return f"[Error] {type(e).__name__}: {e}"


async def _list_dir(path: str = ".") -> str:
    try:
        resolved = _resolve_path(path)
        if not resolved.exists():
            return f"[Error] dir not found: {path}"
        if not resolved.is_dir():
            return f"[Error] not a dir: {path}"
        items = []
        for item in sorted(resolved.iterdir()):
            suffix = "/" if item.is_dir() else ""
            size = ""
            if item.is_file():
                try:
                    size = f" ({item.stat().st_size} bytes)"
                except OSError:
                    pass
            label = "[DIR]" if item.is_dir() else "[FILE]"
            items.append(f"{label} {item.name}{suffix}{size}")
        return "\n".join(items) if items else "(empty)"
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return f"[Error] {type(e).__name__}: {e}"


async def _glob(pattern: str, path: str = ".") -> str:
    try:
        base = _resolve_path(path)
        matches = sorted([str(p.relative_to(base)) for p in base.glob(pattern) if p.is_file()])
        if not matches:
            return f"[Info] no matches for {pattern}"
        return f"共 {len(matches)} matches:\n" + "\n".join(matches)
    except Exception as e:
        return f"[Error] {type(e).__name__}: {e}"


async def _grep(pattern: str, path: str = ".", case_insensitive: bool = False, max_results: int = 20) -> str:
    try:
        re.compile(pattern)
    except re.error as e:
        return f"[Error] invalid regex: {e}"
    flags = re.IGNORECASE if case_insensitive else 0
    base = _resolve_path(path)
    results = []
    total = 0
    files = [base] if base.is_file() else [f for f in base.rglob("*") if f.is_file() and _is_text_file(f)]
    for file_path in files:
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line, flags):
                snippet = line.strip()
                if len(snippet) > 120:
                    snippet = snippet[:120] + "..."
                results.append(f"  {file_path.name}:{i}: {snippet}")
                total += 1
                if total >= max_results:
                    break
        if total >= max_results:
            break
    if not results:
        return f"[Info] no matches for {pattern}"
    suffix = f"\n... and {total - max_results} more" if total > max_results else ""
    header = f"grep '{pattern}' ({total} matches):\n"
    return header + "\n".join(results[:max_results]) + suffix


async def _file_info(path: str) -> str:
    try:
        resolved = _resolve_path(path)
        if not resolved.exists():
            return f"[Error] not found: {path}"
        stat = resolved.stat()
        is_dir = resolved.is_dir()
        import datetime
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        size = "" if is_dir else f" size: {stat.st_size} bytes"
        label = "dir" if is_dir else "file"
        return f"[OK] {label}: {path}\n  path: {resolved}\n  modified: {mtime}{size}"
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return f"[Error] {type(e).__name__}: {e}"


def _is_text_file(path: pathlib.Path) -> bool:
    binary = {".pyc", ".png", ".jpg", ".gif", ".pdf", ".zip", ".exe", ".dll", ".so", ".woff", ".woff2", ".ttf"}
    if path.suffix.lower() in binary:
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.read(512)
        return True
    except (OSError, UnicodeDecodeError):
        return False


FILE_TOOLS: list[ToolDef] = []
_tools_built = False


def _ensure_tools():
    global _tools_built, FILE_TOOLS
    if not _tools_built:
        # 动态获取可用模型
        try:
            import agent_config.model_resolver as mr
            providers_desc = ", ".join(mr.MODEL_NAMES.keys())
            models_by_provider = []
            for p, ms in mr.MODEL_NAMES.items():
                models_by_provider.append(f"{p}: {', '.join(ms.keys())}")
            model_list_explicit = []
            for p, ms in mr.MODEL_NAMES.items():
                for short_name in ms.keys():
                    model_list_explicit.append(f"{short_name}")
            runtime_desc = (
                f"update runtime params. Any combination is supported. "
                f"model: short name ONLY. Available: {', '.join(model_list_explicit)}. "
                f"temperature: 0-2. top_p: 0-1. max_tokens: positive int. "
                f"thinking_mode: enabled/disabled/auto. "
                f"reasoning_effort: high/max (需 thinking_enabled=true，否则自动开启 thinking)。 "
                f"thinking_enabled: true/false (关 thinking 时自动清除 reasoning_effort；"
                f"开 thinking 时 temperature/top_p 由 DeepSeek API 忽略不生效)。"
            )
            identity_desc = (
                "update identity config. system_prompt: core personality. "
                "title: title/position. traits: trait description. "
                "max_iterations: max reasoning iterations (positive int)."
            )
        except Exception:
            runtime_desc = "update runtime params: model, temperature, top_p, max_tokens, thinking_mode"
            identity_desc = "update identity: system_prompt, role, description, max_iterations"
        FILE_TOOLS = [
            ToolDef(
                name="read_file",
                description="read file content. params: path, line_range (optional, format start,end)",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "line_range": {"type": "string"}
                    },
                    "required": ["path"]
                },
                fn=_read_file,
            ),
            ToolDef(
                name="write_file",
                description="write content to file. params: path, content, mode (w or a)",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "mode": {"type": "string", "default": "w"}
                    },
                    "required": ["path", "content"]
                },
                fn=_write_file,
            ),
            ToolDef(
                name="list_dir",
                description="list directory contents. params: path (default .)",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": []
                },
                fn=_list_dir,
            ),
            ToolDef(
                name="glob",
                description="glob pattern matching. params: pattern, path (default .)",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"}
                    },
                    "required": ["pattern"]
                },
                fn=_glob,
            ),
            ToolDef(
                name="grep",
                description="regex search in files. params: pattern, path, case_insensitive, max_results",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                        "case_insensitive": {"type": "boolean"},
                        "max_results": {"type": "integer"}
                    },
                    "required": ["pattern"]
                },
                fn=_grep,
            ),
            ToolDef(
                name="file_info",
                description="get file/dir info. params: path",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]
                },
                fn=_file_info,
            ),
            # ── 自手术工具 ──
            ToolDef(
                name="update_runtime",
                description=runtime_desc,
                parameters={
                    "type": "object",
                    "properties": {
                        "model": {"type": "string"},
                        "temperature": {"type": "number"},
                        "top_p": {"type": "number"},
                        "max_tokens": {"type": "integer"},
                        "thinking_mode": {"type": "string"},
                        "reasoning_effort": {"type": "string"},
                        "thinking_enabled": {"type": "boolean"},
                    },
                    "required": []
                },
                fn=None,
            ),
            ToolDef(
                name="update_identity",
                description=identity_desc,
                parameters={
                    "type": "object",
                    "properties": {
                        "system_prompt": {"type": "string"},
                        "title": {"type": "string"},
                        "traits": {"type": "string"},
                        "max_iterations": {"type": "integer"},
                    },
                    "required": []
                },
                fn=None,
            ),
            # ── 历史摘要工具 ──
            ToolDef(
                name="summarize_conversation",
                description=(
                    "将较早的对话历史压缩为摘要，节省上下文。"
                    "当 token 消耗过高或话题自然切换时主动调用。"
                    "keep_recent_turns: 保留最近 N 轮原文不压缩（默认 6）。"
                    "topic: 摘要主题（可选，留空自动推断）。"
                    "压缩后的摘要下轮自动注入上下文。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "keep_recent_turns": {"type": "integer"},
                        "topic": {"type": "string"},
                    },
                    "required": []
                },
                fn=None,
            ),
            # ── 角色管理工具 ──
            ToolDef(
                name="create_character",
                description=(
                    "创建新角色。name: 角色名称，system_prompt: 核心人格定义，"
                    "title: 头衔（可选），traits: 特质描述（可选），"
                    "model: 模型短名（可选），provider: 供应商（可选），"
                    "temperature: 0-2（可选），top_p: 0-1（可选），"
                    "thinking_enabled: true/false（可选，默认 true），"
                    "reasoning_effort: high/max（可选，默认 high）。"
                    "注意：thinking_enabled=false 时不应设 reasoning_effort（互斥）。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "system_prompt": {"type": "string"},
                        "title": {"type": "string"},
                        "traits": {"type": "string"},
                        "model": {"type": "string"},
                        "provider": {"type": "string"},
                        "temperature": {"type": "number"},
                        "top_p": {"type": "number"},
                        "max_tokens": {"type": "integer"},
                        "thinking_enabled": {"type": "boolean"},
                        "thinking_mode": {"type": "string"},
                        "reasoning_effort": {"type": "string"},
                    },
                    "required": ["name", "system_prompt"]
                },
                fn=None,
            ),
            ToolDef(
                name="list_characters",
                description="列出所有已注册角色及其模型和描述",
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": []
                },
                fn=None,
            ),
            ToolDef(
                name="send_to_character",
                description=(
                    "向目标角色发送消息，触发对方生成回复。\n\n"
                    "**重要：当你需要与另一个角色对话时，必须使用此工具，不要直接生成角色扮演文本。**\n\n"
                    "调用后：\n"
                    "1. 你的消息被转发给 recipient，写入对方对话历史\n"
                    "2. recipient 以角色身份生成回复（实时展示给用户）\n"
                    "3. 回复内容通过返回值返回，供你决定下一步\n\n"
                    "参数：recipient: 目标角色名，message: 消息内容。\n"
                    "**注意：每调用一次 = 一轮对话。需要多轮时，等结果返回后再调用一次。**"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "recipient": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["recipient", "message"]
                },
                fn=None,
            ),
            # ── 系统工具 ──
            ToolDef(
                name="bash",
                description=(
                    "执行 shell 命令。command: 要执行的命令。"
                    "结果包含 stdout 和 stderr。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                    },
                    "required": ["command"]
                },
                fn=None,
            ),
            ToolDef(
                name="web_fetch",
                description=(
                    "获取网页内容。url: 网页地址。"
                    "返回提取的文本内容。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                    },
                    "required": ["url"]
                },
                fn=None,
            ),
            ToolDef(
                name="web_search",
                description=(
                    "搜索网页。query: 搜索关键词。"
                    "max_results: 最大结果数 (默认 5)。"
                    "返回搜索结果的标题、摘要和链接。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["query"]
                },
                fn=None,
            ),
        ]
        _tools_built = True


# —————————执行———————————

tools = ToolRegistry()
# ── 填充调度表（必须在所有 handler 定义之后）──
_BUILTIN_HANDLERS.update({
    "update_runtime": _handle_update_runtime,
    "update_identity": _handle_update_identity,
    "summarize_conversation": _handle_summarize_conversation,
    "create_character": _handle_create_character,
    "list_characters": _handle_list_characters,
    "send_to_character": _handle_send_to_character,
    "bash": _handle_bash,
    "web_fetch": _handle_web_fetch,
    "web_search": _handle_web_search,
})

tools.register_file_tools()
