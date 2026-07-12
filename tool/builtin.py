from __future__ import annotations

import inspect
import json
import pathlib
import re
import subprocess
import time
from datetime import datetime
from urllib.parse import quote
from urllib.request import Request, urlopen

from character import get_history_path
from character.config_io import load_config, save_config
from character.history import History
from character.registry import registry
from character.summarizer import (
    L1Summary, append_compression_record, archive_recent_talk,
    build_topics_context, load_compression_log, l1summary_to_context_string,
    recall_topic_by_id, recall_topic_by_label, save_l1,
    _analyze_slice, _describe_slice, _gaps_between_covered, _guess_topic, )
from common.experience_core import update_experience
from common.logger import logger
from common.utils import set_display_name
from data_shape import (
    ActorConfig, IPURuntime, RoleConfig, ToolDef, UpdateRuntimeArgs, )
from schedule.strategies import wall_ms
from yinao import IPU_REGISTRY, resolve_ipu
from yinao.ipu_client import resolve_chat, sync_config_to_ipu
from yinao.ipu_client.ipu_context import (
    IPU_REGISTRY as _IPU_REGISTRY_RUNTIME, get_active_ipu, get_circuit_status, is_provider_available,
    list_ipu_providers, resolve_ipu_provider, request_switch, )


def _format_error(e: BaseException) -> str:
    return f"[Error] {type(e).__name__}: {e}"


FILE_TOOLS: list[ToolDef] = []

_BUILTIN_HANDLERS: dict[str, callable] = {}
_tools_built = False
_current_actor: str = "default"  # ── 当前操作的 actor名（由 app.py 设定） ──


class ToolRegistry:
    """注册工具"""

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
                    "parameters": tool.parameters, }})
        return result

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    async def execute(self, name: str, arguments: dict) -> str:
        arguments = arguments or {}
        handler = _BUILTIN_HANDLERS.get(name)
        if handler is not None:
            if inspect.iscoroutinefunction(handler):
                # 优先 kwargs 解包（适配文件工具：def _list_dir(path: str = ".")）；
                # 失败回退 dict 直传（适配业务工具：def _handle_xxx(arguments: dict)）。
                # 所有异常都包成 [Error] ... 字符串，避免冒泡给 LLM 调用方。
                try:
                    return await handler(**arguments)
                except TypeError:
                    pass
                try:
                    return await handler(arguments)
                except Exception as e:
                    return _format_error(e)
            try:
                return handler(**arguments)
            except TypeError:
                pass
            try:
                return handler(arguments)
            except Exception as e:
                return _format_error(e)

        # ── 普通工具（通过 ToolRegistry 注册的文件工具等）──
        tool = self._tools.get(name)
        if not tool: return f"[Error] tool not found: {name}"
        if not tool.fn: return f"[Error] tool {name} has no implementation"
        try:
            result = await tool.fn(**arguments)
            return str(result) if result is not None else ""
        except TypeError as e:
            missing = _find_missing_param(e, tool.parameters)
            if missing: return f"[Error] missing required param: {missing}"
            return f"[Error] {e}"
        except Exception as e:
            return _format_error(e)

    def register_file_tools(self):
        # 构建 FILE_TOOLS 表。仅首次构建，后续幂等。
        global _tools_built, FILE_TOOLS
        if _tools_built: return
        from tool.metadata import build_tool_defs
        FILE_TOOLS = build_tool_defs()
        _tools_built = True
        for tool_def in FILE_TOOLS: self.register(tool_def)


def set_actor(name: str):
    global _current_actor
    _current_actor = name


# ── 自手术工具实现 ──

def _handle_update_runtime(arguments: dict) -> str:
    """更新运行时智能基元参数（ipu/temperature/top_p/max_icp/thinking_mode 任意组合）。
    如果 ipu 变了 → 抛 ModelSwitched 让 app.py 重建 client。
    其他参数 → 直接写 JSON，下轮生效。
    """

    # ── 解析参数（pydantic 自动做类型/范围/枚举校验）──
    try:
        args = UpdateRuntimeArgs(**(arguments or {}))
    except Exception as e:
        return _format_validation_error(e,
            "update_runtime")  # pydantic ValidationError 单字段是嵌套结构，展平成 LLM 友好字符串

    config = load_config(_current_actor);
    rt = config.runtime
    actual_ipu = get_active_ipu()  # 实际运行引擎（fallback 后可能与文件不同）
    changes: list[str] = [];
    ipu_changed = False

    # ── ipu 切换（含熔断检查）──
    if args.has("ipu") and (args.ipu != rt.ipu or (actual_ipu and args.ipu != actual_ipu)):
        provider = resolve_ipu_provider(args.ipu)
        if provider and not is_provider_available(provider): return _format_circuit_error(provider)
        ipu_changed = _apply_field(args, rt, "ipu", changes)

    _apply_field(args, rt, "temperature", changes)
    _apply_field(args, rt, "top_p", changes)
    _apply_field(args, rt, "max_icp", changes)

    # thinking_enabled: 关闭时清空 reasoning_effort（DeepSeek 400 防呆）
    if args.has("thinking_enabled"):
        _apply_field(args, rt, "thinking_enabled", changes)
        if not args.thinking_enabled and rt.reasoning_effort:
            old = rt.reasoning_effort
            log_value = f"reasoning_effort=(自动清除 {old}，关闭 thinking 时不可设 reasoning_effort)"
            _apply_field(args, rt, "reasoning_effort", changes, value="", log_value=log_value)

    # reasoning_effort: 开启时自动开 thinking（DeepSeek 400 防呆）
    if args.has("reasoning_effort") and not rt.thinking_enabled:
        log_value = "thinking_enabled=(自动开启，reasoning_effort 需 thinking 支持)"
        _apply_field(args, rt, "thinking_enabled", changes, value=True, log_value=log_value)
    _apply_field(args, rt, "reasoning_effort", changes)
    _apply_field(args, rt, "thinking_mode", changes)
    if not changes: return "[OK] no changes (all values match current)"
    save_config(config, _current_actor)
    if not ipu_changed: return f"[OK] runtime updated: {', '.join(changes)}"
    provider = resolve_ipu_provider(rt.ipu)
    error_hint = f"[Error] 无法解析智能基元 '{rt.ipu}' 的供应商。可用智能基元: 2.7快, 2.7, chat, 千问3.6+, kimi 2.5, glm-5, M2.5"
    if provider is None: return error_hint
    if rt.ipu == provider: rt.ipu = next(iter(IPU_REGISTRY[provider].keys()))
    rt.provider = provider;
    save_config(config, _current_actor)
    request_switch(provider, rt.ipu)
    success_hint = f"[OK] runtime updated: {', '.join(changes)} → 将切换至 {provider}/{rt.ipu}"
    return success_hint


def _format_validation_error(exc: Exception, tool_name: str) -> str:
    """pydantic ValidationError → [Error] field: detail 字符串列表（LLM 友好）。"""
    details = getattr(exc, "errors", None)
    if not callable(details): return _format_error(exc)
    try:
        errs = exc.errors()  # type: ignore[attr-defined]  # pydantic v2: ValidationError.errors() 返回 list[dict]
    except Exception:
        return _format_error(exc)
    lines = []
    for err in errs:
        loc = err.get("loc", ())
        field = loc[-1] if loc else tool_name  # 跳过顶层（tool_name）
        msg = err.get("msg", "")
        detail = msg.split(", ", 1)[-1] if ", " in msg else msg  # "Value error, must be high/max" → 取后段；其它原样
        got = err.get("input")
        if got is not None and "got" not in detail: detail = f"{detail}, got {got}"  # 尽量拼出原 error 字面量格式
        lines.append(f"[Error] {field}: {detail}")
    return "\n".join(lines) if lines else _format_error(exc)


def _format_circuit_error(provider: str) -> str:
    """格式化供应商熔断错误（含所有供应商状态）。"""
    status = get_circuit_status().get(provider, {})
    remain = status.get("reset_remaining_sec", "?")
    last_err = status.get("last_error", "")
    all_status = get_circuit_status()
    status_lines = []
    for p, s in all_status.items():
        label = "🟢" if s.get("available", True) else "🔴"
        extra = ""
        if not s.get("available", True): extra = f" (熔断, {s.get('reset_remaining_sec', '?')}s 后恢复)"
        status_lines.append(f"  {label} {p}{extra}")
    formatted_status = "\n".join(status_lines) if status_lines else "  (无状态)"
    invalid_hint = (f"[Error] {provider} 当前不可用（已熔断，{remain}s 后自动恢复）。\n"
                    f"原因: {last_err}\n当前供应商状态:\n{formatted_status}")
    return invalid_hint


def _apply_field(args: "UpdateRuntimeArgs", rt, field: str,
        changes: list[str], *, value=None, log_value=None, ) -> bool:
    if value is None:  # value 缺省时从 args 取（要求 args.has(field)）
        if not args.has(field): return False
        value = getattr(args, field)
    # 与当前值一致 → 不写盘、不计入 changes（让 no changes 路径生效）。
    # value 显式传入的互斥分支也会尊重 diff：避免重复「自动清除」噪音。
    if getattr(rt, field) == value: return False
    setattr(rt, field, value)  # value 显式时直接使用，args 可缺省提供（用于互斥里的清空/开启）
    # log_value 缺省时记录 field=value
    changes.append(log_value if log_value is not None else f"{field}={value}")
    return True


def _handle_update_identity(arguments: dict) -> str:
    """更新身份参数（system_prompt/title/traits/max_iterations 任意组合）。
    写 JSON 后下轮生效。 """
    config = load_config(_current_actor)
    ident = config.identity
    changes = []
    # - parser: arguments[key] 的转换函数（str/int/...）
    # - validator: 校验函数，返回 True 通过；None 表示不校验
    # - log_with_value: True → 变更日志写 "key=value"，False → 只写 "key"
    field_specs = (  # 元组结构：(argument_key, dataclass_attr, parser, validator, log_with_value)
        ("system_prompt", "system_prompt", str, None, False),
        ("title", "title", str, None, True),
        ("traits", "traits", str, None, False),
        ("max_iterations", "max_iterations", int, lambda n: n > 0, True),)
    for key, attr, parser, validator, log_value in field_specs:
        if key not in arguments:  continue
        value = parser(arguments[key])
        if validator and not validator(value): return f"[Error] {key} 校验失败, got {value}"
        setattr(ident, attr, value)
        changes.append(f"{key}={value}" if log_value else key)
    if not changes: return "[OK] no changes"
    save_config(config, _current_actor)
    return f"[OK] identity updated: {', '.join(changes)}"


# ── 历史摘要工具 ──

async def _handle_summarize_conversation(arguments: dict) -> str:
    """角色主动压缩早期对话历史。"""

    keep_recent_turns = int(arguments.get("keep_recent_turns", 6))
    topic_hint = arguments.get("topic", "")

    history_path = get_history_path(_current_actor)
    if not history_path.exists():
        return "[Error] 无历史记录"

    with open(history_path, "r", encoding="utf-8") as f:
        messages: list[dict] = json.load(f)

    if not messages:
        return "[OK] 历史为空"

    user_indices = [i for i, m in enumerate(messages) if m["role"] == "user"]
    total_turns = len(user_indices)
    if total_turns <= keep_recent_turns:
        return f"[OK] 仅 {total_turns} 轮，无需压缩（阈值 {keep_recent_turns}）"

    cutoff_user_idx = total_turns - keep_recent_turns
    cutoff_msg_idx = user_indices[cutoff_user_idx]
    cutoff_time = messages[cutoff_msg_idx].get("time", "")

    compress_slice = messages[:cutoff_msg_idx]

    user_turns, starttime, endtime, events = _analyze_slice(compress_slice)
    topic = topic_hint or _guess_topic(events)
    detail = _describe_slice(user_turns, events, topic)

    now = datetime.now()
    sid = f"L1-{now.strftime('%Y%m%d-%H%M%S')}"
    abs_from = 0
    abs_to = cutoff_msg_idx - 1

    summary = L1Summary(
        id=sid, start_time=starttime, end_time=endtime, message_count=len(compress_slice),
        user_turns=user_turns, topic=topic, detail=detail, key_events=events,
        msg_indices=(abs_from, abs_to), source="manual",
    )

    saved_path = save_l1(_current_actor, summary)

    lines = [
        f"[摘要已保存] {l1summary_to_context_string(summary)}",
        f"  详情: {detail}",
        f"  截断位置: {cutoff_time} — 保留最近 {keep_recent_turns} 轮原文",
        f"  文件: {saved_path}",
    ]
    logger.info(f"  📦 角色主动摘要 | {user_turns} 轮 → {topic} | {saved_path}")

    # 追加 compression_log
    append_compression_record(character_name=_current_actor, source="summarize_conversation",
        l1_id=summary.id, abs_from=abs_from, abs_to=abs_to)

    return "\n".join(lines)


async def _handle_archive_recent_talk(arguments: dict) -> str:
    """按时间戳精确归档一段对话为话题摘要。
    用户指令如「转为摘要」「归档这个话题」时调用。
    """
    # 兼容老 import：history_json_to_markdown 已被移除
    try:
        from character.summarizer import history_json_to_markdown
    except ImportError:
        history_json_to_markdown = None

    # 解析参数：单段 (time_range_start/time_range_end) 或聚合 (time_ranges) 二选一
    time_range_start = (arguments.get("time_range_start") or "").strip()
    time_range_end = (arguments.get("time_range_end") or "").strip()
    time_ranges_raw = arguments.get("time_ranges")
    time_ranges: list[list[str]] = []
    if time_ranges_raw:
        # LLM 可能传 JSON 字符串或 Python list
        if isinstance(time_ranges_raw, str):
            try:
                parsed = json.loads(time_ranges_raw)
                if isinstance(parsed, list):
                    time_ranges = [[str(x[0]), str(x[1])] for x in parsed if
                                   isinstance(x, (list, tuple)) and len(x) >= 2]
            except Exception:
                # 尝试按换行/分号 split；每个区间内部按逗号 split
                for line in time_ranges_raw.split("\n"):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) == 2 and parts[0] and parts[1]:
                        time_ranges.append([parts[0], parts[1]])
        elif isinstance(time_ranges_raw, list):
            for item in time_ranges_raw:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    time_ranges.append([str(item[0]), str(item[1])])
    topic_hint = arguments.get("topic_hint", "")
    topic_label = arguments.get("topic_label", "")
    people_str = arguments.get("people", "")
    people = [p.strip() for p in people_str.split(",") if p.strip()] if people_str else []

    history_path = get_history_path(_current_actor)
    if not history_path.exists(): return "[Error] 无历史记录"
    with open(history_path, "r", encoding="utf-8") as f:
        messages: list[dict] = json.load(f)

    # ── 话题纯度校验（防止 LLM 用连续区间把多个话题混在一起归档）──
    # 在 archive 真正执行前，校验每个候选区间内的 user 消息是否只含一个话题标记。
    # 若发现区间跨多个话题，直接报错让 LLM 改用聚合模式。
    _TOPIC_RE = re.compile(r"话题\s*([A-Za-z0-9一二三四五六七八九十百千零]+)")

    def _extract_topic_markers(text: str) -> set[str]:
        return set(_TOPIC_RE.findall(text)) if isinstance(text, str) else set()

    def _validate_range_purity(start_ts: str, end_ts: str) -> tuple[bool, str]:
        """校验 (start_ts, end_ts) 区间内所有 user 消息的话题标记是否一致。

        返回 (ok, error_msg)。ok=False 时 error_msg 描述冲突并提示用聚合模式。
        """

        def _msg_ts(m: dict) -> str:
            return (m.get("time") or "")[:19]

        slice_msgs = [m for m in messages
                      if m.get("role") == "user" and start_ts <= _msg_ts(m) <= end_ts]
        if not slice_msgs: return True, ""
        all_markers: set[str] = set()
        for m in slice_msgs: all_markers.update(_extract_topic_markers(m.get("content", "")))
        if len(all_markers) <= 1: return True, ""
        sorted_markers = sorted(all_markers)
        return False, (
            f"区间 [{start_ts}, {end_ts}] 内包含多个不同话题标记 {sorted_markers}。"
            f"**你必须改用聚合模式**：为每个话题标记单独构造一个区间，"
            f"例如 time_ranges=[[\"{start_ts}\", \"<第 1 个话题的末条 assistant 时间>\"], "
            f"[\"<第 2 个话题的 user 时间>\", \"{end_ts}\"]], "
            f"然后归档其中**一个**话题（其余话题留待下次分别归档）。"
            f"不要用单段模式把不同话题混在一起。"
        )

    if time_range_start and time_range_end:
        ok, err = _validate_range_purity(time_range_start, time_range_end)
        if not ok: return f"[Error] {err}"

    # 聚合模式：每个区间都做纯度校验
    if time_ranges:
        for r in time_ranges:
            if len(r) >= 2 and r[0] and r[1]:
                ok, err = _validate_range_purity(r[0], r[1])
                if not ok: return f"[Error] {err}"

    # 准备工具调用的可见性数据：原始 arguments JSON + 归档时间戳
    tool_call_args = json.dumps(arguments, ensure_ascii=False)
    archive_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        summary = await archive_recent_talk(
            character_name=_current_actor, messages=messages,
            time_range_start=time_range_start, time_range_end=time_range_end,
            time_ranges=time_ranges if time_ranges else None,
            topic_hint=topic_hint, topic_label=topic_label, people=people,
        )

        # 重新渲染近期对话原文（按 compression_log 过滤）
        #    manual_only=True：只看 archive_recent_talk 自己的覆盖，
        #    不被 auto_summarize 后台任务的覆盖段干扰（"扰乱测试"的根因）。
        log = load_compression_log(_current_actor)
        gaps = _gaps_between_covered(len(messages), log, manual_only=True)
        visible_msgs = []
        for start, end in gaps: visible_msgs.extend(messages[start:end + 1])

        # 构建 summary_entry：聚合归档把整个范围合并为一个 entry
        # range_msg_indices 用于 _build_recall_block 召回时分段拼接
        summary_entry = {
            "id": summary.id,
            "topic_label": summary.topic_label or summary.topic or "归档话题",
            "start_time": summary.start_time, "end_time": summary.end_time,
            "user_turns": summary.user_turns, "detail": summary.detail,
            "msg_indices": list(summary.msg_indices),
            "time_ranges": summary.time_ranges, "range_msg_indices": summary.range_msg_indices,
        }

        # 工具结果字符串
        label = summary.topic_label or summary.topic or "归档话题"
        people_str_out = "、".join(summary.people) if summary.people else "无特定人物"
        range_count = len(summary.range_msg_indices) if summary.range_msg_indices else 1
        tool_result = (
            f"[OK] 话题「{label}」已归档\n"
            f"  人物: {people_str_out}\n"
            f"  轮次: {summary.user_turns} 轮\n"
            f"  区间数: {range_count}\n"
            f"  时间: {summary.start_time[:19] if summary.start_time else '?'} ~ "
            f"{summary.end_time[:19] if summary.end_time else '?'}\n"
            f"  摘要: {summary.detail[:120]}{'...' if len(summary.detail) > 120 else ''}\n"
            f"  ID: {summary.id}"
        )

        # 更新 experience.md
        # physical_total = history.json 当前真实长度，让 archive 写完 written_len 后，
        # 下次 dump_experience 不会把已渲染的工具调用重复追加。
        update_experience(_current_actor, "archive", {
            "messages": [{"role": "system"}] * 3 + visible_msgs,
            "visible_msgs": visible_msgs, "summary_entry": summary_entry,
            "tool_call_args": tool_call_args, "tool_result": tool_result,
            "archive_ts": archive_ts, "physical_total": len(messages),
        })

        return tool_result
    except ValueError as e:
        msg = str(e)
        # 当没有新内容可归档时，明确告诉 LLM 不要重试
        if "无新用户消息可归档" in msg or "全部已被压缩覆盖" in msg:
            return (
                f"[Error] {msg}\n"
                "提示：当前没有可归档的新内容，所有未压缩的用户消息"
                "均已被覆盖或处理。请直接告知用户该状态，**不要再次调用 archive_recent_talk**。"
            )
        return f"[Error] {msg}"
    except Exception as e:
        return _format_error(e)


def _handle_recall_topic(arguments: dict) -> str:
    """召回已归档的话题摘要，支持按标签或 ID 精确查找。
    用户指令如「继续聊之前的话题」「回顾价值本质的讨论」时调用。
    返回续谈注入块，直接追加到上下文底部。
    """

    topic_label = arguments.get("topic_label", "")
    topic_id = arguments.get("topic_id", "")
    show_list = arguments.get("list_all", False)

    history = History(_current_actor).load()
    history_messages = history.messages

    if show_list:
        return build_topics_context(_current_actor)

    if topic_id:
        try:
            summary, block = recall_topic_by_id(_current_actor, topic_id)
            update_experience(_current_actor, "recall",
                {"topic_id": summary.id, "recall_block": block})  # 更新 experience.md
            return block
        except ValueError as e:
            return f"[Error] {e}"

    if topic_label:
        try:
            summary, block = recall_topic_by_label(_current_actor, topic_label)
            label = summary.topic_label or summary.topic or "未命名"
            update_experience(_current_actor, "recall",
                {"topic_id": summary.id, "recall_block": block})  # 更新 experience.md
            return (
                f"[话题回想] 找到「{label}」（ID: {summary.id}）\n"
                f"将以下内容注入上下文：\n\n{block}"
            )
        except ValueError as e:
            return f"[Error] {e}"

    return "[Error] 需要 topic_label 或 topic_id 参数，也可传 list_all=true 查看所有话题"


# ── 角色管理工具 ──

async def _handle_create_character(arguments: dict) -> str:
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


def _handle_list_characters() -> str:
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
        except Exception:
            lines.append(f"  {name}: (配置读取失败)")
    return "\n".join(lines)


async def _handle_send_to_character(arguments: dict) -> str:
    recipient = arguments["recipient"]
    message = arguments["message"]

    # 剥离 form_full_context 的结构化外壳，防止嵌套（详见 strip_context_wrapper）
    from common.context import strip_context_wrapper
    message = strip_context_wrapper(message)

    if not registry.exists(recipient):
        return f"[Error] 角色 {recipient} 不存在。使用 list_characters 查看可用角色。"

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
    from common.context import build_system_message
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
    set_display_name(recipient);
    set_actor(recipient)  # 终端显示名/_current_actor → 接收者（update_* 操作正确目标）

    # ── 调用接收者 LLM，失败时自动尝试其他供应商 ──
    tried_providers = {recipient_provider}
    reply = "";
    last_error = ""
    engine_fallback_note = ""  # 记录是否发生了引擎降级

    try:
        while True:
            try:
                chat_fn = resolve_chat(recipient_provider)
                result = await chat_fn(all_msgs, recipient_ipu_config, character_name=recipient)
                for msg in reversed(result.messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        reply = msg["content"];
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
                    reply = f"[Error] 无法为 {recipient} 找到可用引擎: {ke}";
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
        set_actor(sender_name);
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


# ── 时策工具 ──

_scheduler: object | None = None  # 由 bootstrap 注入


def set_scheduler(scheduler):
    """注入 TemporalScheduler 实例（bootstrap 时调用）。"""
    global _scheduler
    _scheduler = scheduler


def _handle_shice_schedule_add(arguments: dict) -> str:
    """时策工具：LLM 传入绝对时间戳列表，注册定时任务。"""
    if _scheduler is None: return "[Error] 时策调度器未初始化"

    timestamps = arguments.get("timestamps", [])
    message = arguments.get("message", "")
    if not timestamps or not message:
        return "[Error] timestamps 和 message 为必填参数"

    now = wall_ms()
    valid = [t for t in timestamps if t >= now - 60_000]  # 过滤已过期（延迟 ≤ 60s 保留，让 missed handler 处理）
    if not valid: return "[Error] 所有时间戳均已过期超过 60 秒"

    job_id = _scheduler.add_recurring(
        name=f"时策-{message[:20]}", message=message,
        timestamps=valid, character_id=_current_actor,
    )

    dropped = len(timestamps) - len(valid)
    info = f"已注册 {len(valid)} 个时间点"
    if dropped: info += f"（{dropped} 个已过期被忽略）"

    t0 = valid[0];
    delay = (t0 - now) / 1000.0
    due_str = time.strftime("%H:%M:%S", time.localtime(t0 / 1000.0))
    return f"[OK] {info}\n  首次触发: {due_str}（{delay:.1f}秒后）\n  job_id: {job_id}"


def _handle_shice_schedule_list() -> str:
    """列出所有活跃的时策任务。"""
    if _scheduler is None: return "[OK] 时策调度器未初始化，无活跃任务"
    jobs = _scheduler.list_jobs()
    if not jobs: return "[OK] 无活跃的时策任务"
    lines = [f"共 {len(jobs)} 个活跃任务:"]
    for j in jobs:
        lines.append(f"  [{j['job_id']}] {j['name']} | 已触发 {j['fired']}/{j['total']} | 剩余 {j['remaining']}")
    return "\n".join(lines)


def _handle_shice_schedule_cancel(arguments: dict) -> str:
    """取消时策任务。"""
    if _scheduler is None: return "[Error] 时策调度器未初始化"
    job_id = arguments.get("job_id", "")
    if not job_id: return "[Error] 需要 job_id 参数（可通过 shice_schedule_list 获取）"
    ok = _scheduler.remove_remaining(job_id)
    return f"[OK] 已取消任务 {job_id}" if ok else f"[Error] 任务 {job_id} 不存在或已结束"


# ── 系统工具 ──

def _handle_bash(arguments: dict) -> str:
    command = arguments["command"]
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace",
        )
        out = []
        if result.stdout.strip(): out.append(result.stdout.strip())
        if result.stderr.strip(): out.append(f"[stderr]\n{result.stderr.strip()}")
        if not out: out.append(f"(exit code {result.returncode})")
        return "\n".join(out)
    except subprocess.TimeoutExpired:
        return "[Error] 命令超时（30s）"
    except Exception as e:
        return _format_error(e)


async def _handle_web_fetch(arguments: dict) -> str:
    url = arguments["url"]
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Agent01/1.0)"})
        with urlopen(req, timeout=15) as resp:
            content = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = content.decode(charset, errors="replace")
    except Exception as e:
        return f"[Error] 获取失败: {type(e).__name__}: {e}"

    # 简单 HTML 剥离
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
    query = arguments["query"]
    max_results = int(arguments.get("max_results", 5))

    search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    try:
        req = Request(search_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"[Error] 搜索失败: {type(e).__name__}: {e}"

    results = []
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    links = re.findall(r'class="result__url"[^>]*>(.*?)</a>', html, re.S)
    titles = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', html, re.S)

    for i in range(min(len(titles), len(snippets), max_results)):
        title = re.sub(r'<[^>]+>', '', titles[i]).strip()
        snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
        link = re.sub(r'<[^>]+>', '', links[i]).strip() if i < len(links) else ""
        results.append(f"{i + 1}. {title}\n   {snippet}\n   [{link}]")

    if not results: return f"[Info] 搜索结果为空。搜索词: {query}"
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
    if not p.is_absolute(): p = pathlib.Path.cwd() / p
    return p.resolve()


async def _read_file(path: str, line_range: str | None = None) -> str:
    try:
        resolved = _resolve_path(path)
        if not resolved.exists(): return f"[Error] file not found: {path}"
        img_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".ico", ".tiff",
                    ".svg"}  # 图片/二进制文件不应直接读取，提示使用 vision 能力
        if resolved.suffix.lower() in img_exts:
            return (
                f"[提示] {path} 是图片文件，不要用 read_file 读取。"
                f"如果你有 vision 能力，请直接要求用户发送图片给你看；"
                f"如果没有 vision，请用 update_runtime 切换到 vision 智能基元。"
            )
        content = resolved.read_text(encoding="utf-8", errors="replace")
        if line_range:
            parts = line_range.split(",")
            if len(parts) == 2:
                start, end = int(parts[0]), int(parts[1])
                lines = content.split("\n")
                start, end = max(1, start) - 1, min(len(lines), end)
                content = "\n".join(lines[start:end])
        return content
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return _format_error(e)


async def _write_file(path: str, content: str, mode: str = "w") -> str:
    try:
        resolved = _resolve_path(path);
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if mode == "a" and resolved.exists(): content = resolved.read_text(encoding="utf-8") + content
        resolved.write_text(content, encoding="utf-8")
        return f"[OK] wrote {len(content)} chars to {path}"
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return _format_error(e)


async def _list_dir(path: str = ".") -> str:
    try:
        resolved = _resolve_path(path)
        if not resolved.exists(): return f"[Error] dir not found: {path}"
        if not resolved.is_dir(): return f"[Error] not a dir: {path}"
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
        return _format_error(e)


async def _glob(pattern: str, path: str = ".") -> str:
    try:
        base = _resolve_path(path)
        matches = sorted([str(p.relative_to(base)) for p in base.glob(pattern) if p.is_file()])
        if not matches: return f"[Info] no matches for {pattern}"
        return f"共 {len(matches)} matches:\n" + "\n".join(matches)
    except Exception as e:
        return _format_error(e)


async def _grep(pattern: str, path: str = ".", case_insensitive: bool = False, max_results: int = 20) -> str:
    try:
        re.compile(pattern)
    except re.error as e:
        return f"[Error] invalid regex: {e}"
    flags = re.IGNORECASE if case_insensitive else 0
    base = _resolve_path(path)
    results = [];
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
                if len(snippet) > 120: snippet = snippet[:120] + "..."
                results.append(f"  {file_path.name}:{i}: {snippet}")
                total += 1
                if total >= max_results: break
        if total >= max_results: break
    if not results: return f"[Info] no matches for {pattern}"
    suffix = f"\n... and {total - max_results} more" if total > max_results else ""
    header = f"grep '{pattern}' ({total} matches):\n"
    return header + "\n".join(results[:max_results]) + suffix


def _is_text_file(path: pathlib.Path) -> bool:
    binary = {".pyc", ".png", ".jpg", ".gif", ".pdf", ".zip", ".exe", ".dll", ".so", ".woff", ".woff2", ".ttf"}
    if path.suffix.lower() in binary: return False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.read(512)
        return True
    except (OSError, UnicodeDecodeError):
        return False


async def _file_info(path: str) -> str:
    try:
        resolved = _resolve_path(path)
        if not resolved.exists(): return f"[Error] not found: {path}"
        stat = resolved.stat()
        is_dir = resolved.is_dir()
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        size = "" if is_dir else f" size: {stat.st_size} bytes"
        label = "dir" if is_dir else "file"
        return f"[OK] {label}: {path}\n  path: {resolved}\n  modified: {mtime}{size}"
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return _format_error(e)


# —————————执行———————————

tools = ToolRegistry()
# ── 填充调度表（必须在所有 handler 定义之后）──
_BUILTIN_HANDLERS.update({
    # ———————配置—————————
    "update_runtime": _handle_update_runtime,
    "update_identity": _handle_update_identity,
    # ———————上下文管理—————————
    "summarize_conversation": _handle_summarize_conversation,
    "archive_recent_talk": _handle_archive_recent_talk,
    "recall_topic": _handle_recall_topic,
    # ———————多角色—————————
    "create_character": _handle_create_character,
    "list_characters": _handle_list_characters,
    "send_to_character": _handle_send_to_character,
    # ———————时策—————————
    "shice_schedule_add": _handle_shice_schedule_add,
    "shice_schedule_list": _handle_shice_schedule_list,
    "shice_schedule_cancel": _handle_shice_schedule_cancel,
    # ———————文件操作—————————
    "bash": _handle_bash,
    "read_file": _read_file,
    "write_file": _write_file,
    "list_dir": _list_dir,
    "glob": _glob,
    "grep": _grep,
    "file_info": _file_info,
    # ———————网络—————————
    "web_fetch": _handle_web_fetch,
    "web_search": _handle_web_search,
})

tools.register_file_tools()
