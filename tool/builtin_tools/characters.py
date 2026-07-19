"""builtin_tools/characters — 角色创建/列表/跨角色调用工具。

``send_to_character`` 涉及 actor 切换（set_actor/set_display_name），
通过 ``tool.builtin`` 调度层做写入。
"""
from __future__ import annotations

from collections.abc import Callable

from character import get_history_path
from character.history import History
from character.registry import registry
from common.logger import logger
from common.cli_output import set_display_name
from data_shape import ActorConfig, IPURuntime, RoleConfig
from tool.builtin import current_actor, set_actor
from yinao import IPU_REGISTRY, resolve_ipu, list_ipu_providers, resolve_chat, sync_config_to_ipu


_SEND_TO_CHARACTER_POST_MSG = {
    'role': 'user',
    'content': (
        '[系统] send_to_character 已完成。'
        '对方角色的回复在上方 tool result 中。'
        '对方无法看到你的普通回复文本。'
        '如需继续对话 -> 调用 send_to_character。'
        '如需向用户汇报 -> 直接输出回复。'
    ),
}


def register_post_exec(runner) -> None:
    """由 ``thought_weaver`` 在 bootstrap 时调用，把
    ``send_to_character`` 的追加消息注册到通用 ToolRunner 的钩子里。"""
    def _hook(tool_name: str, result: str, arguments: dict,
            round_idx: int, idx: int) -> list[dict]:
        return [_SEND_TO_CHARACTER_POST_MSG]
    runner.register_post_exec('send_to_character', _hook)


def create_character(arguments: dict) -> str:
    name = arguments["name"]
    system_prompt = arguments["system_prompt"]
    title = arguments.get("title", name)
    traits = arguments.get("traits", "")
    ipu = arguments.get("ipu", "v4-pro")

    if registry.exists(name): return f"[Error] 角色 {name} 已存在"

    if not any(c.isalnum() or c in "_-" for c in name):
        return f"[Error] 角色名只能包含字母数字和下划线"

    # ── 解析 provider ──
    try:
        provider = _resolve_provider(arguments.get("ipu", "v4-pro"), arguments.get("provider"))
    except ValueError as e:
        return f"[Error] {e}"

    config = ActorConfig(
        identity=RoleConfig(system_prompt=system_prompt, title=title, traits=traits),
        runtime=IPURuntime(
            provider=provider, ipu=ipu,
            temperature=float(arguments.get("temperature", 1.0)),
            top_p=float(arguments.get("top_p", 0.95)),
            max_icp=int(arguments.get("max_icp", 8192)),
            thinking_mode=str(arguments.get("thinking_mode", "auto")),
            reasoning_effort=str(arguments.get("reasoning_effort", "high")),
            thinking_enabled=bool(arguments.get("thinking_enabled", True)), ), )
    registry.create(name, config)
    return (
        f"[OK] 角色 {name} 已创建\n"
        f"  头衔: {title}\n"
        f"  特质: {traits}\n"
        f"  引擎: {provider}/{ipu}")


def _resolve_provider(ipu: str, provider_arg: str | None) -> str:
    """解析角色应使用的 provider，未指定时自动从 IPU_REGISTRY 反向查找唯一匹配项。
    Raises:ValueError: provider 缺失/不唯一/ipu 不存在
    """
    if provider_arg:
        if ipu not in IPU_REGISTRY.get(provider_arg, {}):
            available = ", ".join(IPU_REGISTRY.get(provider_arg, {}).keys())
            raise ValueError(
                f"智能基元 '{ipu}' 在供应商 {provider_arg} 下不存在。"
                f"{provider_arg} 可用智能基元: {available if available else '(无)'}")
        return provider_arg

    found_providers = [p for p, ms in IPU_REGISTRY.items() if ipu in ms]
    if not found_providers:
        all_ipus = [f"{p}/{m}" for p, ms in IPU_REGISTRY.items() for m in ms]
        raise ValueError(
            f"智能基元 '{ipu}' 在所有供应商中都不存在。"
            f"可用智能基元: {', '.join(all_ipus)}")
    if len(found_providers) > 1: raise ValueError(
        f"智能基元 '{ipu}' 存在于多个供应商 ({', '.join(found_providers)})。"
        f"请显式指定 provider 参数来消除歧义。")
    return found_providers[0]


def list_characters() -> str:
    chars = registry.scan()
    if not chars: return "[OK] 暂无角色"
    lines = [f"共 {len(chars)} 个角色:"]
    for name in chars:
        try:
            config = registry.get_config(name)
            prov = config.runtime.provider
            ipu = config.runtime.ipu
            title = config.identity.title or "(未设置头衔)"
            traits = config.identity.traits or "(无描述)"
            active = "(当前)" if name == current_actor() else ""
            lines.append(f"  {name}{active}: {title} | {prov}/{ipu} | {traits}")
        except (KeyError, AttributeError, TypeError) as e:
            # 配置损坏时降级到单行占位，不阻断其他角色展示
            logger.warning(f"[list_characters] 角色 {name} 配置读取失败: {type(e).__name__}: {e}")
            lines.append(f"  {name}: (配置读取失败)")
    return "\n".join(lines)


async def send_to_character(arguments: dict) -> str:
    recipient = arguments["recipient"]
    message = arguments["message"]
    from experience.adapter.conversation import _extract_pure_text
    message = _extract_pure_text(message)
    hint = f"[Error] 角色 {recipient} 不存在。使用 list_characters 查看可用角色。"
    if not registry.exists(recipient): return hint
    recipient_config = registry.get_config(recipient)
    try:
        initial_provider, initial_ipu_short, _ = _resolve_recipient_ipu(recipient, recipient_config)
    except ValueError as e:
        return f"[Error] {e}。请用 update_runtime 修正其 ipu 参数。"
    _sender = current_actor()
    recipient_history = History(str(get_history_path(recipient))).load()
    recipient_history.append_pair(f"[来自 {_sender} 的消息]\n{message}", "")
    all_msgs = _build_recipient_messages(recipient_config, recipient_history, recipient)

    return await _finalize_and_respond(
        recipient=recipient,
        sender_name=_sender,
        recipient_history=recipient_history,
        all_msgs=all_msgs,
        initial_provider=initial_provider,
        initial_ipu_short=initial_ipu_short,
        message=message,
        recipient_config=recipient_config, )


def _resolve_recipient_ipu(recipient: str, recipient_config) -> tuple:
    """解析接收者的 IPU 配置，返回 (provider, ipu_short, ipu_config)。"""
    provider = recipient_config.runtime.provider
    ipu_short = recipient_config.runtime.ipu
    try:
        _, ipu_config = resolve_ipu(provider, ipu_short)
    except KeyError as e:
        raise ValueError(f"角色 {recipient} 配置无效: {e}") from e
    return provider, ipu_short, ipu_config


def _build_recipient_messages(recipient_config, recipient_history, recipient: str) -> list:
    """构建发送给接收者 LLM 的 messages 列表（system + 最近20条历史）。"""
    from experience.adapter.conversation import build_system_message
    all_msgs = [build_system_message(recipient_config, recipient)]
    is_first = True
    for entry in recipient_history.messages[-20:]:
        role = entry.get("role", "user")
        content = entry.get("content", "")
        if role == "user":
            all_msgs.append({"role": "user", "content": content})
        elif role == "assistant" and content:
            all_msgs.append({"role": "assistant", "content": content})
        elif role == "system" and not is_first:
            all_msgs.append({"role": "system", "content": content})
        is_first = False
    return all_msgs


async def _finalize_and_respond(
        recipient: str, sender_name: str, recipient_history: History, all_msgs: list,
        initial_provider: str, initial_ipu_short: str, message: str,
        recipient_config, engine_fallback_note: str = "", ) -> str:
    """执行 LLM 调用（自动切换引擎）并处理后续所有收尾工作。"""
    set_display_name(recipient)
    set_actor(recipient)

    logger.info(
        f"  [send_to_character] {sender_name} → {recipient} | "
        f"引擎 {initial_provider}/{initial_ipu_short} | 历史 {len(all_msgs)} 条")

    try:
        reply, final_provider, final_ipu_short, engine_fallback_note = await _call_recipient(
            recipient, recipient_config, all_msgs, initial_provider, initial_ipu_short)
    finally:
        set_actor(sender_name)
        set_display_name(sender_name)
    if not reply.strip(): reply = "(未生成回复)"

    # ── 失败回滚 ──
    if reply.startswith("[Error]"):
        if recipient_history.messages and recipient_history.messages[-1].get("role") == "assistant":
            recipient_history.messages.pop()
        if recipient_history.messages and recipient_history.messages[-1].get("role") == "user":
            recipient_history.messages.pop()
        recipient_history.save()
        return reply

    # ── 成功后写入双方历史 ──
    if sender_name != recipient:
        sender_history = History(str(get_history_path(sender_name))).load()
        sender_history.append_pair(message, reply)
        sender_history.save()
    if recipient_history.messages and recipient_history.messages[-1].get("role") == "assistant":
        recipient_history.messages[-1]["content"] = reply
        recipient_history.save()
    # ── dump 接收者经验：与主对话循环一致，让 experience.md 同步反映本轮对话 ──
    # 主循环在 _post_round_async 调用 dump_experience(ctx.character_name, ...)，
    # 这里对应 dump 接收者，否则星空诗人等被叫角色的 experience.md 只剩占位符。
    try:
        # 延迟 import：避免与 tool.builtin/tools 在加载时产生循环
        from experience import dump_experience
        from experience.adapter.state import build_round_context
        from yinao.weaver.round_state import last_round as _last_round
        _usage = _last_round.usage if _last_round.usage else None
        round_ctx = build_round_context(recipient) if _usage else ""
        dump_experience(
            recipient, round_context=round_ctx or None,
            round_usage=_usage, )
    except Exception as e:
        logger.warning(f"  [send_to_character] dump {recipient} 经验失败: {type(e).__name__}: {e}")
    return (
        f"🔔 {recipient} 无法看到你的普通回复——继续对话请调用 send_to_character\n\n"
        f"[来自 {recipient} 的回复]\n\n{reply}\n\n"
        f"(引擎: {final_provider}/{final_ipu_short}，"
        f"共 {len(reply)} 字)"
        f"{engine_fallback_note}")


async def _call_recipient(
        recipient: str, recipient_config, all_msgs: list,
        initial_provider: str, initial_ipu_short: str, ) -> tuple:
    """调用接收者的 LLM，失败时自动尝试其他供应商。
    Returns: (reply, final_provider, final_ipu_short, engine_fallback_note)
    """
    current_provider = initial_provider
    current_ipu_short = initial_ipu_short
    _, current_ipu_config = resolve_ipu(current_provider, current_ipu_short)
    tried_providers = {initial_provider}
    engine_fallback_note = ""

    while True:
        try:
            sync_config_to_ipu(recipient_config, current_ipu_config)
            chat_fn = resolve_chat(current_provider)
            result = await chat_fn(all_msgs, current_ipu_config, character_name=recipient)
            for msg in reversed(result.messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    return msg["content"], current_provider, current_ipu_short, engine_fallback_note
            return "", current_provider, current_ipu_short, engine_fallback_note
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.error(
                f"  [send_to_character] {recipient} @ {current_provider}/{current_ipu_short} "
                f"调用失败: {last_error}")
            available = [p for p in list_ipu_providers() if p not in tried_providers]
            if not available:
                return (
                    f"[Error] 调用 {recipient} 的 LLM 失败 "
                    f"({current_provider}/{current_ipu_short}): {last_error}",
                    current_provider, current_ipu_short, "",)
            old_provider, old_model = current_provider, current_ipu_short
            current_provider = available[0]
            current_ipu_short = next(iter(IPU_REGISTRY.get(current_provider, {}).keys()), "v4-flash")
            try:
                _, current_ipu_config = resolve_ipu(current_provider, current_ipu_short)
            except KeyError as ke:
                return (
                    f"[Error] 无法为 {recipient} 找到可用引擎: {ke}",
                    current_provider, current_ipu_short, "",)
            tried_providers.add(current_provider)
            engine_fallback_note = (
                f"\n⚠️ 引擎降级：{old_provider}/{old_model} → {current_provider}/{current_ipu_short}"
                f"（原因: {last_error}）")
            logger.info(
                f"  [send_to_character] 自动切换 {recipient} → {current_provider}/{current_ipu_short}")
            all_msgs[0] = build_system_message(recipient_config, recipient)


HANDLERS: dict[str, Callable] = {
    "create_character": create_character,
    "list_characters": list_characters,
    "send_to_character": send_to_character,
}
