"""builtin_tools/characters — 角色创建/列表/跨角色调用工具。

``send_to_character`` 涉及 actor 切换（set_actor/set_display_name），
通过 ``tool.builtin`` 调度层做写入；``_current_actor`` 在函数体内延迟 import。
"""
from __future__ import annotations

from collections.abc import Callable


def create_character(arguments: dict) -> str:
    from character.registry import registry
    from data_shape import ActorConfig, IPURuntime, RoleConfig
    from yinao import IPU_REGISTRY

    name = arguments["name"]
    system_prompt = arguments["system_prompt"]
    title = arguments.get("title", name)
    traits = arguments.get("traits", "")
    ipu = arguments.get("ipu", "v4-pro")
    provider_arg = arguments.get("provider")  # 可能为 None

    if registry.exists(name):
        return f"[Error] 角色 {name} 已存在"

    if not any(c.isalnum() or c in "_-" for c in name):
        return f"[Error] 角色名只能包含字母数字和下划线"

    # ── 解析 provider ──
    if provider_arg:
        provider = provider_arg
        if ipu not in IPU_REGISTRY.get(provider, {}):  # 校验 ipu 在此 provider 下存在
            available = ", ".join(IPU_REGISTRY.get(provider, {}).keys())
            return (
                f"[Error] 智能基元 '{ipu}' 在供应商 {provider} 下不存在。\n"
                f"{provider} 可用智能基元: {available if available else '(无)'}"
            )
    else:
        found_providers = [p for p, ms in IPU_REGISTRY.items() if ipu in ms]  # 未指定 provider → 自动从 IPU_REGISTRY 反向查找
        if not found_providers:
            all_ipus = [f"{p}/{m}" for p, ms in IPU_REGISTRY.items() for m in ms]
            return (
                f"[Error] 智能基元 '{ipu}' 在所有供应商中都不存在。\n"
                f"可用智能基元: {', '.join(all_ipus)}"
            )
        if len(found_providers) > 1:
            return (
                f"[Error] 智能基元 '{ipu}' 存在于多个供应商 ({', '.join(found_providers)})。\n"
                f"请显式指定 provider 参数来消除歧义。"
            )
        provider = found_providers[0]

    config = ActorConfig(
        identity=RoleConfig(system_prompt=system_prompt, title=title, traits=traits),
        runtime=IPURuntime(
            provider=provider, ipu=ipu,
            temperature=float(arguments.get("temperature", 1.0)),
            top_p=float(arguments.get("top_p", 0.95)),
            max_icp=int(arguments.get("max_icp", 8192)),
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
        f"  引擎: {provider}/{ipu}"
    )


def list_characters() -> str:
    from tool.builtin import _current_actor
    from character.registry import registry

    chars = registry.scan()
    if not chars:
        return "[OK] 暂无角色"

    lines = [f"共 {len(chars)} 个角色:"]
    for name in chars:
        try:
            config = registry.get_config(name)
            prov = config.runtime.provider
            ipu = config.runtime.ipu
            title = config.identity.title or "(未设置头衔)"
            traits = config.identity.traits or "(无描述)"
            active = "(当前)" if name == _current_actor else ""
            lines.append(f"  {name}{active}: {title} | {prov}/{ipu} | {traits}")
        except (KeyError, AttributeError, TypeError) as e:
            # 配置损坏时降级到单行占位，不阻断其他角色展示
            from common.logger import logger
            logger.warning(f"[list_characters] 角色 {name} 配置读取失败: {type(e).__name__}: {e}")
            lines.append(f"  {name}: (配置读取失败)")
    return "\n".join(lines)


async def send_to_character(arguments: dict) -> str:
    from tool.builtin import _current_actor, set_actor
    from character import get_history_path
    from character.history import History
    from character.registry import registry
    from common.context import build_system_message, strip_context_wrapper
    from common.logger import logger
    from common.utils import set_display_name
    from yinao import resolve_ipu
    from yinao.ipu_client import resolve_chat, sync_config_to_ipu
    from yinao.ipu_client.ipu_context import (
        IPU_REGISTRY as _IPU_REGISTRY_RUNTIME, list_ipu_providers, )

    recipient = arguments["recipient"]
    message = arguments["message"]

    # 剥离 form_full_context 的结构化外壳，防止嵌套（详见 strip_context_wrapper）
    message = strip_context_wrapper(message)
    hint = f"[Error] 角色 {recipient} 不存在。使用 list_characters 查看可用角色。"
    if not registry.exists(recipient):  return hint

    # ── 1. 获取双方配置 ──
    recipient_config = registry.get_config(recipient)
    recipient_provider = recipient_config.runtime.provider
    recipient_ipu_short = recipient_config.runtime.ipu

    # 构建接收者的 context（引擎信息块 + 身份）

    try:
        recipient_provider_info, recipient_ipu_config = resolve_ipu(recipient_provider, recipient_ipu_short)
    except KeyError as e:
        return f"[Error] 角色 {recipient} 配置无效: {e}。请用 update_runtime 修正其 ipu 参数。"

    # ── 2. 写入接收者历史（接收者视角：收到新消息） ──
    recipient_history = History(str(get_history_path(recipient))).load()
    recipient_history.append_pair(f"[来自 {_current_actor} 的消息]\n{message}", "")

    # ── 同步接收者运行时配置到 model_config（之前遗漏：MC 裸建全是默认值）──
    sync_config_to_ipu(recipient_config, recipient_ipu_config)

    # ── 3. 构建接收者的 messages（复用 form_full_context 的 system 消息格式）──
    all_msgs = [build_system_message(recipient_config, recipient)]

    is_first = True
    for entry in recipient_history.messages[-20:]:  # 最近 20 条（10 轮）
        role, content = entry.get("role", "user"), entry.get("content", "")
        if role == "user":
            all_msgs.append({"role": "user", "content": content})
        elif role == "assistant" and content:
            all_msgs.append({"role": "assistant", "content": content})  # 跳过空回复
        elif role == "system" and not is_first:
            all_msgs.append({"role": "system", "content": content})
        is_first = False

    # ── 4. 调用接收者的 LLM ──

    logger.info(
        f"  [send_to_character] {_current_actor} → {recipient} | 引擎 {recipient_provider}/{recipient_ipu_short} | 历史 {len(all_msgs)} 条")

    sender_name = _current_actor  # 保存发送者名称，用于后续写入发送者历史
    set_display_name(recipient)
    set_actor(recipient)  # 终端显示名/_current_actor → 接收者（update_* 操作正确目标）

    # ── 调用接收者 LLM，失败时自动尝试其他供应商 ──
    tried_providers = {recipient_provider}
    reply = ""  # ↓ 跨 try/finally 共享：行 178/187/195 写、208/213/221/229/234/241/243 读；Pylance 不跨 try 跟踪变量，会误报"未用"。
    last_error = ""  # ↓ 同上：行 182 写、183/187/202 读；IDE flow analyzer 盲区。
    engine_fallback_note = ""  # ↓ 同上：行 200 写、244 读；break 不跳外层 try、finally 后代码可达。

    try:
        while True:
            try:
                chat_fn = resolve_chat(recipient_provider)
                result = await chat_fn(all_msgs, recipient_ipu_config, character_name=recipient)
                for msg in reversed(result.messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        reply = msg["content"]
                        break
                break  # 成功
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.error(
                    f"  [send_to_character] {recipient} @ {recipient_provider}/{recipient_ipu_short} 调用失败: {last_error}")
                available = [p for p in list_ipu_providers() if p not in tried_providers]  # 尝试切换到其他供应商
                if not available:
                    reply = f"[Error] 调用 {recipient} 的 LLM 失败 ({recipient_provider}/{recipient_ipu_short}): {last_error}"
                    break
                old_provider, old_model = recipient_provider, recipient_ipu_short
                recipient_provider = available[0]
                recipient_ipu_short = next(iter(_IPU_REGISTRY_RUNTIME.get(recipient_provider, {}).keys()), "v4-flash")
                try:
                    _, recipient_ipu_config = resolve_ipu(recipient_provider, recipient_ipu_short)
                except KeyError as ke:
                    reply = f"[Error] 无法为 {recipient} 找到可用引擎: {ke}"
                    break
                sync_config_to_ipu(recipient_config, recipient_ipu_config)  # 同步新引擎运行时配置
                all_msgs[0] = build_system_message(recipient_config, recipient)  # 重建系统消息：反映实际运行引擎
                tried_providers.add(recipient_provider)
                engine_fallback_note = (
                    f"\n⚠️ 引擎降级：{old_provider}/{old_model} → {recipient_provider}/{recipient_ipu_short}"
                    f"（原因: {last_error}）"
                )
                logger.info(f"  [send_to_character] 自动切换 {recipient} → {recipient_provider}/{recipient_ipu_short}")
    finally:
        set_actor(sender_name)
        set_display_name(sender_name)  # 恢复 _current_actor / 终端显示名
    if not reply.strip(): reply = "(未生成回复)"

    # ── 失败分支：reply 是 [Error] ... 时不应走成功响应格式 + 写历史。
    # 否则用户看到"🔔 ... 来自 X 的回复" 但内容是错误，混淆真伪。
    # 同时回滚接收者历史：之前 append_pair 写过占位 (user, ""), 不应残留。
    if reply.startswith("[Error]"):
        if (recipient_history.messages
                and recipient_history.messages[-1].get("role") == "assistant"):
            recipient_history.messages.pop()  # 弹出空 assistant
        if (recipient_history.messages
                and recipient_history.messages[-1].get("role") == "user"):
            recipient_history.messages.pop()  # 弹出占位 user
        recipient_history.save()
        return reply

    # ── 5. 写入发送者历史 ──
    # 发送者历史：完整记录发送+回复（不写空占位，避免异常残留）
    # 注意：send_to_character 后发送者的 experience.md 由 reason_action_loop
    # 自然处理（下一轮 dump_experience 会写入），此处无需手动调用 dump_experience。
    if sender_name != recipient:
        sender_history = History(str(get_history_path(sender_name))).load()
        sender_history.append_pair(message, reply)
        sender_history.save()

    # 接收者历史：补填自己的回复
    if recipient_history.messages and recipient_history.messages[-1].get("role") == "assistant":
        recipient_history.messages[-1]["content"] = reply
        recipient_history.save()
        # 接收者的 experience.md 由 reason_action_loop 自然处理（最终回复时
        # dump_experience 会正确写入），此处无需手动调用 dump_experience。

    return (
        f"🔔 {recipient} 无法看到你的普通回复——继续对话请调用 send_to_character\n\n"
        f"[来自 {recipient} 的回复]\n\n{reply}\n\n"
        f"(引擎: {recipient_provider}/{recipient_ipu_short}，"
        f"共 {len(reply)} 字)"
        f"{engine_fallback_note}"
    )


HANDLERS: dict[str, Callable] = {
    "create_character": create_character,
    "list_characters": list_characters,
    "send_to_character": send_to_character,
}
