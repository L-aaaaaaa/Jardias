from __future__ import annotations

import inspect
import re

from data_shape import ToolDef


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
                    "name": tool.name, "description": tool.description, "parameters": tool.parameters, }})
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
                # 失败回退 dict 直传（适配业务工具：def xxx(arguments: dict)）。
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


def current_actor() -> str:
    """获取当前 actor 名（每次访问读取最新值）。

    ⚠️ 不要用 `from tool.builtin import _current_actor` 拿变量——
    `from ... import x` 是绑定导入时的旧值，后续 `set_actor()` 修改模块全局
    不会反映到这个局部绑定。所有工具函数应该调 ``current_actor()`` 拿最新值。
    """
    return _current_actor


# ── update_runtime 工具使用的 helper ────────────────────────────
# 这些被 builtin_tools.config 的 handle_update_runtime 调用，
# 也被 tests/test_update_runtime.py 直接 import 测试。

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
    from yinao.weaver import get_circuit_status

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


def _apply_field(args: "Any", rt, field: str,
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


# ────────────────────── 调度层其他 API ────────────────────────

_pending_switch: str | None = None
"""非空时表示下轮需切换到指定角色。由 conversation_loop 消费。"""


def clear_pending_switch() -> str | None:
    global _pending_switch
    v = _pending_switch
    _pending_switch = None
    return v


_scheduler: object | None = None  # 由 bootstrap 注入


def set_scheduler(scheduler):
    """注入 TemporalScheduler 实例（bootstrap 时调用）。"""
    global _scheduler
    _scheduler = scheduler


# ── 工具注册（合并 builtin_tools 各分类的 HANDLERS） ──────────

def _find_missing_param(type_error: TypeError, schema: dict) -> str | None:
    msg = str(type_error)
    m = re.search(r"missing 1 required positional argument: '(\w+)'", msg)
    if m: return m.group(1)
    return None


def _load_builtin_handlers() -> dict[str, callable]:
    """从 tool.builtin_tools 各分类模块加载 HANDLERS 字典。
    延迟 import：避免 builtin.py 模块体执行时就触发对自身的循环引用。
    """
    from tool.builtin_tools import (config, experience, characters, shice, files, web, )
    merged: dict[str, callable] = {}
    for module in (config, experience, characters, shice, files, web):
        merged.update(module.HANDLERS)
    return merged


# —————————执行———————————

tools = ToolRegistry()
_BUILTIN_HANDLERS.update(_load_builtin_handlers())  # ── 填充调度表（必须在所有 handler 定义之后）──
tools.register_file_tools()
