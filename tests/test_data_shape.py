"""data_shape/ — 数据形状声明 + 序列化行为。"""
from __future__ import annotations

import dataclasses
import json

import pytest

from data_shape import (
    ActorConfig, IPURuntime, RoleConfig,
    IPUConfig, IPUEntry, IPUProviderConfig, IPUConfigFile,
    L1Summary, TopicSegment,
)


# ── 角色配置（dataclass）───────────────────────────────────────────

class TestRoleConfigDefaults:
    def test_defaults(self):
        rc = RoleConfig()
        assert rc.title == ""
        assert rc.traits == ""
        assert rc.max_iterations == 10
        assert rc.birth_time == ""


class TestIPURuntimeDefaults:
    def test_defaults(self):
        rt = IPURuntime()
        assert rt.provider == "minimax"
        assert rt.ipu == "2.7"
        assert rt.temperature == 1.0
        assert rt.top_p == 0.95
        assert rt.max_icp == 8192
        assert rt.thinking_mode == "auto"
        assert rt.thinking_enabled is True


class TestActorConfigComposition:
    def test_actor_default(self):
        a = ActorConfig()
        assert isinstance(a.identity, RoleConfig)
        assert isinstance(a.runtime, IPURuntime)
        assert a.identity.title == ""

    def test_field_independence(self):
        """两个 ActorConfig 默认字段不能共享 list/dict 引用。"""
        a1 = ActorConfig()
        a2 = ActorConfig()
        # RoleConfig / IPURuntime 字段都是不可变类型，但确认下：
        a1.identity.title = "foo"
        assert a2.identity.title == ""

    def test_dataclass_asdict_round(self):
        a = ActorConfig(identity=RoleConfig(system_prompt="hi", title="机器人"),
                        runtime=IPURuntime(provider="deepseek", ipu="v4-flash"))
        d = dataclasses.asdict(a)
        assert d["identity"]["system_prompt"] == "hi"
        assert d["runtime"]["provider"] == "deepseek"
        # 序列化为 JSON 验证
        assert json.loads(json.dumps(d)) == d


# ── IPU 配置（Pydantic）─────────────────────────────────────────

class TestIPUConfig:
    def test_defaults(self):
        c = IPUConfig()
        assert c.ipu == "MiniMax-M2.7"
        assert c.stream is True
        assert c.tools == []
        assert c.tool_choice == "auto"
        assert c.max_icp == 2048
        assert c.thinking_enabled is True

    def test_extra_allow(self):
        """配置类允许 extra 字段（透传到 API）。"""
        c = IPUConfig(custom="abc", num=42)
        dumped = c.model_dump()
        assert dumped["custom"] == "abc"
        assert dumped["num"] == 42


class TestIPUEntryAndProvider:
    def test_entry(self):
        e = IPUEntry(id="deepseek-v3", caps=["text", "thinking"])
        assert e.id == "deepseek-v3"
        assert "thinking" in e.caps

    def test_provider_config(self):
        p = IPUProviderConfig(
            name="deepseek",
            api_key_env="DEEPSEEK_API_KEY",
            base_url="https://api.deepseek.com",
            ipus={"v3": {"id": "deepseek-v3"}},
        )
        assert p.name == "deepseek"
        assert "v3" in p.ipus

    def test_config_file_serialization(self):
        cfg = IPUConfigFile(providers=[
            IPUProviderConfig(
                name="test",
                api_key_env="X",
                base_url="http://x",
                ipus={"a": {"id": "a-id"}},
            )
        ])
        j = cfg.model_dump_json()
        assert "test" in j
        assert "X" in j


# ── L1Summary + TopicSegment ────────────────────────────────────

class TestTopicSegment:
    def test_required_fields(self):
        seg = TopicSegment(from_msg_idx=0, to_msg_idx=5,
                            topic="t", detail="d", key_points=["k"])
        assert seg.from_msg_idx == 0
        assert seg.to_msg_idx == 5
        assert seg.key_points == ["k"]


class TestL1SummaryDefaults:
    def test_defaults(self):
        s = L1Summary(id="L1-x")
        assert s.start_time == ""
        assert s.message_count == 0
        assert s.summary == []
        assert s.msg_indices == (0, 0)
        assert s.source == "auto"
        assert s.range_msg_indices == []

    def test_tuple_msg_indices_persists(self):
        """msg_indices 是 tuple[int, int]，应保持不变。"""
        s = L1Summary(id="x", msg_indices=(3, 10))
        assert isinstance(s.msg_indices, tuple)
        assert s.msg_indices == (3, 10)

    def test_time_ranges_multi_range(self):
        s = L1Summary(id="x", time_ranges=[["2026-01-01 00:00:00", "2026-01-01 00:01:00"],
                                             ["2026-01-02 00:00:00", "2026-01-02 00:01:00"]])
        assert len(s.time_ranges) == 2


# ── data_shape/__init__ 导出完整性 ─────────────────────────────

class TestExports:
    """data_shape/__init__.py 应导出文档中列出的全部符号。"""

    def test_all_exports_importable(self):
        from data_shape import (
            ActorConfig, RoleConfig, IPURuntime,
            IPUEntry, IPUProviderConfig, IPUConfigFile,
            IPUConfig, ToolCall, RoundOutput, ChatResult, RoundMeta, IPUSwitch,
            L1Summary, TopicSegment,
            ToolDef, ToolParam,
        )
        # 仅校验名字存在
        for cls in (ActorConfig, RoleConfig, IPURuntime,
                    IPUEntry, IPUProviderConfig, IPUConfigFile,
                    IPUConfig, ToolCall, RoundOutput, ChatResult, RoundMeta, IPUSwitch,
                    L1Summary, TopicSegment,
                    ToolDef, ToolParam):
            assert cls is not None
