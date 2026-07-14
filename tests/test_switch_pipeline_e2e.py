"""切换链路端到端测试。

覆盖 ipu_switch 的核心协议：
  request_switch(provider, ipu) → 写入 switch_request
  pop_switch() → 读取并清空
  IPUSwitched 异常被外层捕获时携带 (provider, ipu)
  reload_after_switch 用新 ipu 重建 ctx
  set_active_ipu / get_active_ipu 在 fallback 后的可观测性

不调用真实 LLM：直接调 request_switch / pop_switch / reload_after_switch，
验证它们作为"切换协议"的语义正确性。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from data_shape import ActorConfig, IPURuntime, RoleConfig
from yinao.ipu_client.ipu_switch import (
    IPUSwitched, pop_switch, request_switch, get_active_ipu, set_active_ipu,
)


# ── Fixture：建 alice 角色（reload_after_switch 需要） ──────────────


@pytest.fixture
def alice(tmp_workdir: Path, reset_circuit_breakers) -> tuple[str, str, str]:
    """建一个默认 dashscope/千问3.6+ 的角色。返回 (角色名, 起始 provider, 起始 ipu)。"""
    from character.registry import registry
    from character.config_io import save_config

    char_name = "alice"
    if registry.exists(char_name):
        registry.delete(char_name)

    config = ActorConfig(
        identity=RoleConfig(
            system_prompt="你是#{character_name}，一个测试用的角色。",
            title="alice", traits="测试用",
        ),
        runtime=IPURuntime(provider="dashscope", ipu="千问3.6+"),
    )
    registry.create(char_name, config)
    save_config(config, char_name)
    return char_name, "dashscope", "千问3.6+"


# ════════════════════════════════════════════════════════════════════
# ── request_switch / pop_switch 协议 ─────────────────────────────
# ════════════════════════════════════════════════════════════════════


class TestSwitchProtocol:
    """写 / 读 / 清空 这三个动作的协议正确性。"""

    def test_request_then_pop_returns_same_value(self, alice):
        """request_switch 写入 → pop_switch 读出同一个 IPUSwitch。"""
        request_switch("dashscope", "千问3.6+")
        from yinao.ipu_client.ipu_switch import switch_request
        assert switch_request is not None
        result = pop_switch()
        assert result is not None
        assert result.provider == "dashscope"
        assert result.ipu == "千问3.6+"

    def test_pop_clears_slot(self, alice):
        """pop_switch 取出后清空 → 第二次 pop 返回 None。"""
        request_switch("dashscope", "千问3.6+")
        first = pop_switch()
        assert first is not None
        second = pop_switch()
        assert second is None

    def test_pop_empty_returns_none(self, alice):
        """从未 request_switch 直接 pop → 返回 None。"""
        assert pop_switch() is None

    def test_unknown_provider_raises_value_error(self, alice):
        """未注册的 provider → ValueError，包含可用列表。"""
        with pytest.raises(ValueError) as exc:
            request_switch("nonexistent-vendor", "some-ipu")
        assert "未知供应商" in str(exc.value)
        assert "dashscope" in str(exc.value)  # 应列出已注册供应商

    def test_unknown_ipu_raises_value_error(self, alice):
        """已注册的 provider + 未注册的 ipu → ValueError，列出可用 ipu。"""
        with pytest.raises(ValueError) as exc:
            request_switch("dashscope", "fake-model-9000")
        assert "未知智能基元" in str(exc.value)
        # 应列出 dashscope 下可用 ipu
        msg = str(exc.value)
        assert "千问3.6+" in msg or "qwen" in msg.lower()

    def test_overwrite_replaces_previous(self, alice):
        """第二次 request_switch 覆盖第一次（链式调用场景）。"""
        request_switch("dashscope", "千问3.6+")
        request_switch("deepseek", "v4-pro")
        result = pop_switch()
        assert result.provider == "deepseek"
        assert result.ipu == "v4-pro"


# ════════════════════════════════════════════════════════════════════
# ── IPUSwitched 异常传递 ────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════


class TestIPUSwitchedException:
    """IPUSwitched 异常被外层捕获时携带 (provider, ipu)。"""

    def test_carries_provider_and_ipu(self):
        with pytest.raises(IPUSwitched) as exc_info:
            raise IPUSwitched("dashscope", "千问3.6+")
        assert exc_info.value.provider == "dashscope"
        assert exc_info.value.ipu == "千问3.6+"

    def test_message_includes_path(self):
        with pytest.raises(IPUSwitched) as exc_info:
            raise IPUSwitched("deepseek", "v4-pro")
        assert "deepseek/v4-pro" in str(exc_info.value)


# ════════════════════════════════════════════════════════════════════
# ── set_active_ipu / get_active_ipu ───────────────────────────────
# ════════════════════════════════════════════════════════════════════


class TestActiveIpuTracking:
    """实际运行的 ipu 跟踪：fallback 后与 config 不同。"""

    def test_initial_empty(self, alice):
        """未 set_active_ipu → get_active_ipu 返回空字符串。"""
        assert get_active_ipu() == ""

    def test_set_then_get(self, alice):
        set_active_ipu("dashscope", "千问3.6+")
        assert get_active_ipu() == "千问3.6+"

    def test_set_overwrites(self, alice):
        """set_active_ipu 多次 → 只保留最后一次的值。"""
        set_active_ipu("dashscope", "千问3.6+")
        set_active_ipu("deepseek", "v4-pro")
        assert get_active_ipu() == "v4-pro"

    def test_fallback_tracking(self, alice):
        """模拟 fallback 后：config 写的是 dashscope/千问3.6+，
        实际跑的是 deepseek/v4-pro（fallback 降级）。get_active_ipu 反映实际。"""
        from character.config_io import load_config
        cfg = load_config("alice")
        cfg.runtime.provider = "dashscope"
        cfg.runtime.ipu = "千问3.6+"
        # 不写盘 → 保持原状
        # 现在调 set_active_ipu 标记实际跑的
        set_active_ipu("deepseek", "v4-pro")
        # config 上是 dashscope / 实际是 deepseek
        assert cfg.runtime.provider == "dashscope"
        assert get_active_ipu() == "v4-pro"


# ════════════════════════════════════════════════════════════════════
# ── reload_after_switch 重建 ctx ──────────────────────────────────
# ════════════════════════════════════════════════════════════════════


class TestReloadAfterSwitch:
    """reload_after_switch 用新 ipu 重建 ctx。"""

    def test_reload_picks_up_new_ipu_from_config(self, alice):
        """config.json 已切换 → reload_after_switch 后 ctx.ipu 是新的。"""
        from dataclasses import dataclass
        from character.config_io import load_config, save_config
        from yinao.ipu_client import reload_after_switch

        @dataclass
        class _FakeCtx:
            character_name: str = "alice"
            config_dir = None
            provider: str = ""
            ipu: str = ""
            chat_fn = None
            ipu_config = None

        ctx = _FakeCtx()
        # 改 config.json（模拟 update_runtime 后的持久化）
        cfg = load_config("alice")
        cfg.runtime.provider = "deepseek"
        cfg.runtime.ipu = "v4-pro"
        save_config(cfg, "alice")

        reload_after_switch(ctx)
        assert ctx.provider == "deepseek"
        assert ctx.ipu == "v4-pro"

    def test_reload_after_no_op_ipu_unchanged(self, alice):
        """config.json 没改 → reload 仍然是旧的。"""
        from dataclasses import dataclass
        from yinao.ipu_client import reload_after_switch

        @dataclass
        class _FakeCtx:
            character_name: str = "alice"
            config_dir = None
            provider: str = ""
            ipu: str = ""
            chat_fn = None
            ipu_config = None

        ctx = _FakeCtx()
        reload_after_switch(ctx)
        assert ctx.provider == "dashscope"
        assert ctx.ipu == "千问3.6+"


# ════════════════════════════════════════════════════════════════════
# ── 完整握手：request_switch → reload_after_switch ─────────────
# ════════════════════════════════════════════════════════════════════


class TestSwitchHandshake:
    """模拟 app.py 真正的切换握手：
       request_switch 写入 → pop_switch 在 weave_thought 里读出 →
       外层判断 should_switch=True → reload_after_switch 重建 ctx
    """

    def test_full_round_trip_changes_ctx_state(self, alice):
        from dataclasses import dataclass
        from character.config_io import save_config, load_config
        from yinao.ipu_client import reload_after_switch

        char_name, _, _ = alice

        @dataclass
        class _FakeCtx:
            character_name: str = "alice"
            config_dir = None
            provider: str = ""
            ipu: str = ""
            chat_fn = None
            ipu_config = None

        ctx = _FakeCtx()
        # 1. 切引擎前
        reload_after_switch(ctx)
        assert ctx.provider == "dashscope"
        assert ctx.ipu == "千问3.6+"

        # 2. update_runtime 在工具里：写 config + request_switch
        cfg = load_config(char_name)
        cfg.runtime.provider = "deepseek"
        cfg.runtime.ipu = "v4-pro"
        save_config(cfg, char_name)
        request_switch("deepseek", "v4-pro")

        # 3. weave_thought 出口处取
        switch = pop_switch()
        assert switch is not None
        assert switch.provider == "deepseek"
        assert switch.ipu == "v4-pro"

        # 4. 第二次 pop 已清空
        assert pop_switch() is None

        # 5. 应答 ChatResult.should_switch=True 时外层 reload
        reload_after_switch(ctx)
        assert ctx.provider == "deepseek"
        assert ctx.ipu == "v4-pro"
