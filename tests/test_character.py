"""character/ — 角色层：
- character/__init__.py：路径 / 命名解析
- character.history：History 类
- character.config_io：读写 + 字段重命名兼容
- character.registry：注册表 + ensure 骨架
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from character import (
    _make_dir_name, _resolve_dir, _DIR_NAME_RE,
    get_character_dir, get_display_name,
    get_config_path, get_history_path,
    ensure_dirs, list_characters,
)
from character.history import History
from character.config_io import (
    save_config, load_config, config_from_dict, config_to_dict,
    _dataclass_from_dict,
)
from character.registry import registry
from data_shape import ActorConfig, RoleConfig, IPURuntime


# ── 命名解析 ────────────────────────────────────────────────

class TestDisplayNameExtraction:
    def test_with_timestamp(self):
        assert get_display_name("202605011252-小明") == "小明"

    def test_plain_name(self):
        assert get_display_name("default") == "default"

    def test_no_extra_digits(self):
        """额外数字角色名（人名含数字）应保留。"""
        assert get_display_name("202605011252-aa3") == "aa3"

    def test_timestamp_strictly_12_digits(self):
        """时间戳必须恰好 12 位。"""
        assert _DIR_NAME_RE.match("202600011252-foo") is not None
        assert _DIR_NAME_RE.match("20260011-foo") is None
        assert _DIR_NAME_RE.match("foo") is None


class TestMakeDirName:
    def test_format(self):
        name = _make_dir_name("alice")
        # 形如 "202601011200-alice"（具体秒数因运行时刻而定）
        assert _DIR_NAME_RE.match(name)
        assert get_display_name(name) == "alice"


class TestResolveDirMissing:
    def test_none_for_absent(self, tmp_workdir):
        """不存在的角色名应返回 None。"""
        assert _resolve_dir("nope") is None


class TestEnsureDirs:
    def test_creates_skeleton(self, tmp_workdir):
        d = ensure_dirs("bob")
        assert d.exists()
        assert (d / "summaries" / "L1").exists()

    def test_no_timestamp_when_already_exists(self, tmp_workdir):
        """existing 时不创建新时间戳目录（避免目录爆炸）。"""
        ensure_dirs("bob")
        d1 = get_character_dir("bob")
        ensure_dirs("bob")
        d2 = get_character_dir("bob")
        assert d1 == d2

    def test_new_uses_timestamped(self, tmp_workdir):
        ensure_dirs("bob")
        d_id = get_character_dir("bob").name
        assert _DIR_NAME_RE.match(d_id)


class TestListCharacters:
    def test_empty(self, tmp_workdir):
        assert list_characters() == []

    def test_filters_underscore_prefix(self, tmp_workdir):
        """以 _ 开头的目录应被排除（系统目录）。"""
        (tmp_workdir / "character_data" / "_system_dir").mkdir()
        (tmp_workdir / "character_data" / "202601011200-real").mkdir()
        names = list_characters()
        assert "real" in names
        assert "_system_dir" not in names


# ── History ──────────────────────────────────────────────

class TestHistoryAppend:
    def test_load_missing_file(self, tmp_path):
        h = History(str(tmp_path / "nope.json"))
        h.load()
        assert h.messages == []

    def test_save_and_reload(self, tmp_path):
        p = tmp_path / "h.json"
        h = History(str(p))
        h.append_pair("hi", "hello")
        h.save()

        h2 = History(str(p)).load()
        assert len(h2.messages) == 2
        assert h2.messages[0]["role"] == "user"
        assert h2.messages[1]["role"] == "assistant"
        assert h2.messages[0]["content"] == "hi"

    def test_append_user_timestamp(self, tmp_path):
        h = History(str(tmp_path / "h.json"))
        h.append_user("msg", ts="2026-01-01 00:00:00")
        assert h.messages[0]["time"] == "2026-01-01 00:00:00"

    def test_append_tool(self, tmp_path):
        h = History(str(tmp_path / "h.json"))
        h.append_tool(tool_call_id="x", name="bash", content="ok", ts="2026-01-01 00:00:00")
        assert h.messages[0]["role"] == "tool"
        assert h.messages[0]["name"] == "bash"

    def test_append_assistant_with_tool_calls(self, tmp_path):
        h = History(str(tmp_path / "h.json"))
        h.append_assistant_msg(content="", tool_calls=[{"id": "c1", "function": {"name": "bash"}}])
        assert h.messages[0]["tool_calls"][0]["id"] == "c1"

    def test_append_trigger(self, tmp_path):
        h = History(str(tmp_path / "h.json"))
        h.append_trigger("trigger-msg")
        assert h.messages[0]["role"] == "system_trigger"
        assert h.messages[0]["content"] == "trigger-msg"


# ── Config IO ─────────────────────────────────────────────

class TestFieldRenameCompat:
    """config_io 应该把旧字段名自动转为新名。"""

    def test_role_to_title(self):
        from data_shape import RoleConfig
        r = _dataclass_from_dict(RoleConfig, {"role": "bot", "description": "tester"})
        assert r.title == "bot"
        assert r.traits == "tester"

    def test_model_to_ipu(self):
        from data_shape import IPURuntime
        rt = _dataclass_from_dict(IPURuntime, {"model": "v4-pro", "max_tokens": 4096})
        assert rt.ipu == "v4-pro"
        assert rt.max_icp == 4096

    def test_full_config_roundtrip(self, temp_character):
        cfg = ActorConfig(
            identity=RoleConfig(system_prompt="x", title="t", traits="d"),
            runtime=IPURuntime(provider="deepseek", ipu="v4-flash"),
        )
        save_config(cfg, "alice")
        loaded = load_config("alice")
        assert loaded.identity.title == "t"
        assert loaded.runtime.ipu == "v4-flash"


class TestConfigExport:
    def test_from_to_dict_roundtrip(self):
        cfg = ActorConfig(
            identity=RoleConfig(system_prompt="p", title="x"),
            runtime=IPURuntime(provider="dashscope", ipu="千问3.6+"),
        )
        d = config_to_dict(cfg)
        back = config_from_dict(d)
        assert back.identity.system_prompt == "p"
        assert back.runtime.provider == "dashscope"
        assert back.runtime.ipu == "千问3.6+"


# ── Registry ────────────────────────────────────────────

class TestRegistry:
    def test_create_makes_skeleton(self, tmp_workdir):
        cfg = ActorConfig(identity=RoleConfig(), runtime=IPURuntime())
        registry.create("alice", cfg)
        d = get_character_dir("alice")
        # 应有 history.json (空数组) 与 experience.md 占位
        assert (d / "history.json").exists()
        assert (d / "experience.md").exists()
        assert (d / "config.json").exists()
        # history.json 内容是合法 JSON
        assert json.loads((d / "history.json").read_text("utf-8")) == []

    def test_create_twice_raises(self, tmp_workdir):
        cfg = ActorConfig(identity=RoleConfig(), runtime=IPURuntime())
        registry.create("alice", cfg)
        with pytest.raises(ValueError, match="已存在"):
            registry.create("alice", cfg)

    def test_exists(self, tmp_workdir):
        cfg = ActorConfig(identity=RoleConfig(), runtime=IPURuntime())
        assert not registry.exists("alice")
        registry.create("alice", cfg)
        assert registry.exists("alice")

    def test_scan_after_create(self, tmp_workdir):
        cfg = ActorConfig(identity=RoleConfig(), runtime=IPURuntime())
        registry.create("alice", cfg)
        registry.create("bob", cfg)
        chars = sorted(registry.scan())
        assert chars == ["alice", "bob"]

    def test_delete(self, tmp_workdir):
        cfg = ActorConfig(identity=RoleConfig(), runtime=IPURuntime())
        registry.create("alice", cfg)
        registry.delete("alice")
        assert not registry.exists("alice")

    def test_cannot_delete_default(self, tmp_workdir):
        with pytest.raises(ValueError, match="default"):
            registry.delete("default")

    def test_get_config(self, tmp_workdir):
        cfg = ActorConfig(identity=RoleConfig(title="bot"),
                          runtime=IPURuntime(provider="p1", ipu="i1"))
        registry.create("alice", cfg)
        got = registry.get_config("alice")
        assert got.identity.title == "bot"
        assert got.runtime.provider == "p1"

    def test_get_experience_path(self, tmp_workdir):
        cfg = ActorConfig(identity=RoleConfig(), runtime=IPURuntime())
        registry.create("alice", cfg)
        p = registry.get_experience_path("alice")
        assert p.name == "experience.md"
        assert p.exists()
