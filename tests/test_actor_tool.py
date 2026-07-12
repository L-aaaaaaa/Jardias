"""tool/actor_tool.py — @actor_tool 装饰器。

签名：`@actor_tool(ipu=..., output_schema=..., system=...)`
职责：
- 注册到 _registry
- 装饰器返回可调用 wrapper
- 未注入 executor 时调用才抛
"""
from __future__ import annotations

import pytest

from tool.actor_tool import actor_tool, list_actor_tools, set_actor_executor


# ── 装饰器基本 ─────────────────────────────────────────

class TestBasicDecoration:
    def test_returns_callable(self):
        @actor_tool(ipu="qwen-turbo",
                     output_schema={"x": "str"},
                     system="你是测试")
        def foo(x: str) -> dict:
            return {"x": x}
        # foo 仍是可调用的（返回 coroutine，需要 await）
        assert callable(foo)
        # 未注入 executor 调用 sync → 不抛（wrapper 自身不抛），但调用返回 coroutine
        coro = foo(x="a")
        try:
            # 未注入 executor 时调用 → coroutine 立即返回
            # 在 await 时才抛 RuntimeError；这里只验证 callable 形状
            assert coro is not None
        finally:
            # 关闭未等待的 coroutine（避免 RuntimeWarning）
            coro.close()

    def test_registered(self):
        @actor_tool(ipu="test", output_schema={"x": "int"}, system="s")
        def unique_t1(x: int) -> dict:
            return {"x": x}
        reg = list_actor_tools()
        assert "unique_t1" in reg
        assert reg["unique_t1"]["ipu"] == "test"
        assert reg["unique_t1"]["output_schema"] == {"x": "int"}

    def test_wrapper_uses_executor(self):
        called = {}

        async def fake_executor(ipu, system_prompt, user_message, output_schema):
            called["ipu"] = ipu
            called["system"] = system_prompt
            called["user"] = user_message
            called["schema"] = output_schema
            return {"x": 1}

        set_actor_executor(fake_executor)

        @actor_tool(ipu="prov1", output_schema={"x": "int"}, system="sys-tpl")
        def my_tool(x: int) -> dict:
            return {}

        import asyncio
        result = asyncio.run(my_tool(x=42))
        assert result == {"x": 1}
        assert called["ipu"] == "prov1"
        assert called["system"] == "sys-tpl"
        assert "42" in called["user"]


class TestSetExecutor:
    def test_set_replaces(self):
        async def exec_a(*args, **kwargs):
            return {"a": 1}

        async def exec_b(*args, **kwargs):
            return {"b": 2}

        set_actor_executor(exec_a)
        # 再次设置不抛
        try:
            set_actor_executor(exec_b)
        except Exception:
            pytest.fail("set_actor_executor should not raise")
