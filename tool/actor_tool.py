"""
@actor_tool — 角色侧旁路小模型调用框架

与普通 @tool 的关键区别：
- 固定 pipeline：单次 API 调用，不允许 tool chain
- 结构化输出：output_schema 约束返回 JSON
- 不进 history：不追加 tool_call / tool_result 到对话消息
- 独立 system prompt：与 worker 模型完全隔离
"""

from __future__ import annotations
import json
from typing import Any, Callable

_registry: dict[str, dict] = {}
_executor: Callable | None = None


def set_actor_executor(fn: Callable):
    """注入 API 执行器（由 app.py 在启动时调用）。"""
    global _executor
    _executor = fn


def actor_tool(
    *,
    ipu: str,
    output_schema: dict[str, str],
    system: str,
):
    """装饰器：将函数标记为旁路智能基元调用工具。

    调用时 → 组 system + user prompt → 单次 API → JSON 解析 → 返回 dict。

    示例:
        @actor_tool(
            ipu="qwen-turbo",
            output_schema={"topic": "str", "detail": "str"},
            system="你是对话摘要器。输出 JSON。"
        )
        def summarize(messages: str) -> dict:
            pass  # 函数体不执行，由装饰器替换
    """
    def decorator(fn: Callable):
        name = fn.__name__
        _registry[name] = {
            "ipu": ipu,
            "output_schema": output_schema,
            "system": system,
            "fn": fn,
        }

        async def wrapper(**kwargs) -> dict:
            if not _executor:
                raise RuntimeError(
                    f"@actor_tool '{name}' called before executor is set. "
                    "Call set_actor_executor() at startup."
                )
            user_text = "\n".join(f"{k}:\n{v}" for k, v in kwargs.items())
            return await _executor(
                ipu=ipu,
                system_prompt=system,
                user_message=user_text,
                output_schema=output_schema,
            )
        wrapper.__name__ = name
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator


def list_actor_tools() -> dict[str, dict]:
    """返回已注册的 @actor_tool 列表（名称 → 元数据）。"""
    return dict(_registry)
