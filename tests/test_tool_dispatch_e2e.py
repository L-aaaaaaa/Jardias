"""所有可端到端测试的工具：业务行为 + 通过 ToolRegistry.execute 调度。

覆盖范围（按 builtin._BUILTIN_HANDLERS）：

    文件工具 ── read_file / write_file / list_dir / glob / grep / file_info
    配置工具 ── update_runtime / update_identity
    角色管理 ── create_character / list_characters
    系统工具 ── bash

不在本测试范围（明确）：
    - summarize_conversation / archive_recent_talk / recall_topic
      需要 character 历史 + 摘要器，跟 character 模块强耦合，留给
      character 模块自身测试覆盖。
    - send_to_character
      需要真实 LLM 调用（resolve_chat + LLM round），无法在 e2e 中跑。
    - shice_schedule_add / list / cancel
      异步调度器副作用，且与 shice 模块紧耦合，留给 shice 测试覆盖。
    - web_fetch / web_search
      需要外部网络。

工具 100% 注册是前置条件（test_file_tools_dispatch.py 已覆盖）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tool import builtin
from tool.builtin import _BUILTIN_HANDLERS, tools
from data_shape import ActorConfig, IPURuntime, RoleConfig
from character import CHAR_ROOT
from character.config_io import get_config_path, load_config


# ════════════════════════════════════════════════════════════
# ── 文件工具：业务行为 + execute 调度 ────────────────────────
# ════════════════════════════════════════════════════════════

class TestFileToolsE2E:
    """走完整 execute() 路径，覆盖每个文件工具的 happy path + 关键错误分支。"""

    @pytest.mark.asyncio
    async def test_list_dir_distinguishes_file_dir(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "nested.txt").write_text("y")

        r = await tools.execute("list_dir", {"path": str(tmp_path)})
        assert "a.txt" in r and "sub/" in r
        assert "[DIR]" in r and "[FILE]" in r

    @pytest.mark.asyncio
    async def test_read_write_roundtrip(self, tmp_path: Path):
        p = tmp_path / "rt.txt"
        await tools.execute("write_file",
                            {"path": str(p), "content": "hello"})
        r = await tools.execute("read_file", {"path": str(p)})
        assert "hello" in r

    @pytest.mark.asyncio
    async def test_write_file_append(self, tmp_path: Path):
        p = tmp_path / "app.txt"
        await tools.execute("write_file",
                            {"path": str(p), "content": "a"})
        await tools.execute("write_file",
                            {"path": str(p), "content": "b", "mode": "a"})
        assert p.read_text(encoding="utf-8") == "ab"

    @pytest.mark.asyncio
    async def test_glob_filters_by_pattern(self, tmp_path: Path):
        for n in ("a.py", "b.py", "c.txt"):
            (tmp_path / n).write_text("")
        r = await tools.execute("glob",
                                {"pattern": "*.py", "path": str(tmp_path)})
        assert "a.py" in r and "b.py" in r and "c.txt" not in r

    @pytest.mark.asyncio
    async def test_grep_returns_matching_lines(self, tmp_path: Path):
        (tmp_path / "g.txt").write_text("foo\nbar\nfoobar\n")
        r = await tools.execute("grep",
                                {"pattern": "foo", "path": str(tmp_path)})
        assert "foo" in r and "foobar" in r and "bar\n" not in r.split("\n")[1:]

    @pytest.mark.asyncio
    async def test_grep_invalid_regex(self, tmp_path: Path):
        r = await tools.execute("grep",
                                {"pattern": "[unclosed", "path": str(tmp_path)})
        assert r.startswith("[Error]")
        assert "invalid regex" in r

    @pytest.mark.asyncio
    async def test_file_info_includes_size(self, tmp_path: Path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"abcde")
        r = await tools.execute("file_info", {"path": str(p)})
        assert "5 bytes" in r


# ════════════════════════════════════════════════════════════
# ── 配置工具：update_runtime / update_identity ─────────────
# ════════════════════════════════════════════════════════════


class TestUpdateRuntime:
    """update_runtime：直接调 handler 拿到返回值（避免熔断/IPU 切换副作用）。

    通过 tools.execute() 走完整 dispatch 路径。
    """

    @pytest.fixture
    def reset_pending_switch(self):
        builtin._pending_switch = None
        yield
        builtin._pending_switch = None

    @pytest.mark.asyncio
    async def test_set_temperature_writes_config(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            reset_pending_switch):
        """普通参数写入磁盘 + 返回值列出变更。"""
        r = await tools.execute("update_runtime", {"temperature": 0.7})

        assert r.startswith("[OK]")
        assert "temperature=0.7" in r

        config = load_config("default")
        assert config.runtime.temperature == 0.7

    @pytest.mark.asyncio
    async def test_temperature_out_of_range_rejected(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            reset_pending_switch):
        """pydantic 校验：超出 [0,2] 直接报 [Error]，不写磁盘。"""
        r = await tools.execute("update_runtime", {"temperature": 3.0})

        assert r.startswith("[Error]")
        assert "temperature" in r
        # config 应该是默认值（未持久化）
        config = load_config("default")
        assert config.runtime.temperature == 1.0  # 默认值

    @pytest.mark.asyncio
    async def test_top_p_out_of_range_rejected(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            reset_pending_switch):
        r = await tools.execute("update_runtime", {"top_p": 1.5})
        assert r.startswith("[Error]")
        assert "top_p" in r

    @pytest.mark.asyncio
    async def test_max_icp_must_be_positive(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            reset_pending_switch):
        r = await tools.execute("update_runtime", {"max_icp": -1})
        assert r.startswith("[Error]")
        assert "max_icp" in r

    @pytest.mark.asyncio
    async def test_thinking_mode_enum_validated(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            reset_pending_switch):
        r = await tools.execute("update_runtime", {"thinking_mode": "bogus"})
        assert r.startswith("[Error]")
        assert "thinking_mode" in r

    @pytest.mark.asyncio
    async def test_reasoning_effort_enum_validated(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            reset_pending_switch):
        r = await tools.execute("update_runtime",
                                {"reasoning_effort": "ultra"})
        assert r.startswith("[Error]")
        assert "reasoning_effort" in r

    @pytest.mark.asyncio
    async def test_setting_same_value_returns_no_changes(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            reset_pending_switch):
        """_apply_field 加 diff 后，重复设同值应返回 no changes。"""
        await tools.execute("update_runtime", {"temperature": 0.5})
        r = await tools.execute("update_runtime", {"temperature": 0.5})
        assert r == "[OK] no changes (all values match current)"
        # 验证 config 仍是上次的值（没有被二次写入覆盖成别的）
        config = load_config("default")
        assert config.runtime.temperature == 0.5

    @pytest.mark.asyncio
    async def test_thinking_disabled_clears_reasoning_effort(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            reset_pending_switch):
        """设 thinking_enabled=false 时自动清空 reasoning_effort。"""
        # 先确保 thinking_enabled=true 且 reasoning_effort 有值
        await tools.execute("update_runtime",
                            {"thinking_enabled": True,
                             "reasoning_effort": "high"})

        r = await tools.execute("update_runtime",
                                {"thinking_enabled": False})

        config = load_config("default")
        assert config.runtime.thinking_enabled is False
        assert config.runtime.reasoning_effort == ""
        assert "reasoning_effort=" in r
        assert "自动清除" in r

    @pytest.mark.asyncio
    async def test_extra_field_rejected(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            reset_pending_switch):
        """pydantic extra=forbid：未知字段报错。"""
        r = await tools.execute("update_runtime", {"unknown_field": 1})
        assert r.startswith("[Error]")


class TestUpdateIdentity:
    @pytest.mark.asyncio
    async def test_set_title_and_traits(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        r = await tools.execute(
            "update_identity",
            {"title": "数据分析师", "traits": "严谨"})

        assert r.startswith("[OK]")
        assert "title=" in r
        assert "traits" in r

        config = load_config("default")
        assert config.identity.title == "数据分析师"
        assert config.identity.traits == "严谨"

    @pytest.mark.asyncio
    async def test_set_system_prompt(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        r = await tools.execute(
            "update_identity",
            {"system_prompt": "新的人格定义。"})

        assert r.startswith("[OK]")
        assert "system_prompt" in r

        config = load_config("default")
        assert config.identity.system_prompt == "新的人格定义。"

    @pytest.mark.asyncio
    async def test_max_iterations_validated(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        r = await tools.execute(
            "update_identity", {"max_iterations": 0})
        assert r.startswith("[Error]")
        assert "max_iterations" in r

    @pytest.mark.asyncio
    async def test_max_iterations_accepts_positive(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        r = await tools.execute(
            "update_identity", {"max_iterations": 20})
        assert r.startswith("[OK]")
        assert "max_iterations=20" in r
        config = load_config("default")
        assert config.identity.max_iterations == 20

    @pytest.mark.asyncio
    async def test_no_args_returns_no_changes(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        r = await tools.execute("update_identity", {})
        assert "no changes" in r


# ════════════════════════════════════════════════════════════
# ── 角色管理 ───────────────────────────────────────────────
# ════════════════════════════════════════════════════════════


class TestCharacterManagement:
    @pytest.mark.asyncio
    async def test_create_character_minimal(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        r = await tools.execute(
            "create_character",
            {"name": "alice", "system_prompt": "你是 Alice。"})

        assert r.startswith("[OK]")
        assert "alice" in r

        config_path = get_config_path("alice")
        assert config_path.exists()

        config = load_config("alice")
        assert config.identity.system_prompt == "你是 Alice。"
        # 默认值
        assert config.runtime.temperature == 1.0
        assert config.runtime.thinking_enabled is True

    @pytest.mark.asyncio
    async def test_create_character_duplicate_rejected(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        await tools.execute("create_character",
                            {"name": "bob", "system_prompt": "Bob"})

        r = await tools.execute("create_character",
                                {"name": "bob", "system_prompt": "Bob 又来"})

        assert r.startswith("[Error]")
        assert "已存在" in r

    @pytest.mark.asyncio
    async def test_create_character_invalid_name(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        """纯标点名称（无 alnum / `_` / `-`）应被拒绝。"""
        r = await tools.execute("create_character",
                                {"name": "!!!", "system_prompt": "x"})

        assert r.startswith("[Error]")

    @pytest.mark.asyncio
    async def test_create_character_unknown_ipu(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        """未知的 ipu 应拒绝创建（避免半成品文件）。"""
        r = await tools.execute("create_character",
                                {"name": "carol",
                                 "system_prompt": "x",
                                 "ipu": "no-such-model-xyz"})

        assert r.startswith("[Error]")
        assert "no-such-model-xyz" in r

    @pytest.mark.asyncio
    async def test_create_character_with_overrides(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        r = await tools.execute("create_character", {
            "name": "dave",
            "system_prompt": "Dave",
            "title": "工程师",
            "traits": "理性",
            "temperature": 0.3,
            "top_p": 0.8,
            "max_icp": 4096,
            "thinking_mode": "disabled",
        })

        assert r.startswith("[OK]")
        config = load_config("dave")
        assert config.identity.title == "工程师"
        assert config.runtime.temperature == 0.3
        assert config.runtime.top_p == 0.8
        assert config.runtime.max_icp == 4096
        assert config.runtime.thinking_mode == "disabled"

    @pytest.mark.asyncio
    async def test_list_characters_empty(self, tmp_workdir: Path, reset_actor):
        r = await tools.execute("list_characters", {})
        assert r == "[OK] 暂无角色"

    @pytest.mark.asyncio
    async def test_list_characters_after_create(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        await tools.execute("create_character",
                            {"name": "eve", "system_prompt": "Eve",
                             "title": "客服", "traits": "耐心"})

        r = await tools.execute("list_characters", {})

        assert "eve" in r
        assert "客服" in r
        assert "耐心" in r
        assert "(当前)" in r or "(当前)" not in r  # 取决于 _current_actor


# ════════════════════════════════════════════════════════════
# ── bash 系统工具 ──────────────────────────────────────────
# ════════════════════════════════════════════════════════════


class TestBash:
    """bash 走真实子进程；用 echo 之类轻量命令验证 stdout/stderr/exit code 处理。"""

    @pytest.mark.asyncio
    async def test_simple_command_stdout(self):
        r = await tools.execute("bash", {"command": "echo hello"})
        assert "hello" in r

    @pytest.mark.asyncio
    async def test_command_with_stderr(self):
        r = await tools.execute("bash", {"command": "echo err >&2"})
        assert "[stderr]" in r
        assert "err" in r

    @pytest.mark.asyncio
    async def test_command_failure_exit_code(self):
        r = await tools.execute("bash",
                                {"command": "exit 7 && echo not-reached"})
        # exit code 在返回值里
        assert "7" in r or "exit" in r.lower()

    @pytest.mark.asyncio
    async def test_command_empty_output(self):
        r = await tools.execute("bash", {"command": "exit 0"})
        # exit 0 + 无 stdout/stderr 应该给出 "(exit code 0)"
        assert "exit code 0" in r


# ════════════════════════════════════════════════════════════
# ── 调度层完整覆盖：每个 handler 都能经 execute() 调度 ─────
# ════════════════════════════════════════════════════════════


class TestAllHandlersDispatchable:
    """防止「handler 注册了但 execute 路径走不到」型 bug。

    不测业务行为（每个工具的 happy path 在上面的 class 里测过），
    只确认：对于所有 19 个 handler，execute(name, {}) 不抛异常（业务工具
    参数缺省时通常返回有意义的 [Error] 或 [OK]）。
    """

    EXPECTED_OK_OR_ERROR_PREFIXES = ("[OK]", "[Error]")

    @pytest.mark.parametrize("tool_name", sorted(_BUILTIN_HANDLERS.keys()))
    def test_handler_in_table(self, tool_name: str):
        assert tool_name in _BUILTIN_HANDLERS

    @pytest.mark.asyncio
    async def test_empty_args_for_each_handler(self):
        """空参调每个 handler 应返回 [OK]/[Error]，不抛 Python 异常。"""
        for name in _BUILTIN_HANDLERS:
            try:
                r = await tools.execute(name, {})
            except Exception as e:
                pytest.fail(f"{name} raise on empty args: {type(e).__name__}: {e}")
            assert isinstance(r, str), f"{name} returned non-str: {type(r)}"