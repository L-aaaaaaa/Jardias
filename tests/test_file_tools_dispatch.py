"""回归测试：文件工具能通过 ToolRegistry.execute() 真正被调度到 handler。

背景：
    builtin.py 的 ``_BUILTIN_HANDLERS`` 历史上只登记了「业务工具」，而文件工具
    (_read_file / _write_file / _list_dir / _glob / _grep / _file_info) 只有
    ToolDef 元数据，没有 fn 指针也没进调度表 → LLM 调 list_dir 会拿到
    ``[Error] tool list_dir has no implementation``。

本测试直接走 ``tools.execute(name, args)`` 的真实分支：
    1. ``_BUILTIN_HANDLERS`` 里必须登记所有文件工具（防止再被退回）。
    2. 实际调用结果必须不是「no implementation」错误。
    3. 业务行为：list_dir 能列出真实文件、read_file 能读真实内容、write_file
       能写入、grep 能匹配、glob 能匹配、file_info 能拿元信息。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tool.builtin import tools, _BUILTIN_HANDLERS  # noqa: F401


FILE_TOOL_NAMES = (
    "read_file", "write_file", "list_dir", "glob", "grep", "file_info",
)


# ── 调度表注册完整性 ──────────────────────────────────────────


class TestBuiltinHandlersRegistration:
    """防止「只加 ToolDef 不加 handler」型 bug 复发。"""

    @pytest.mark.parametrize("name", FILE_TOOL_NAMES)
    def test_handler_registered(self, name: str):
        assert name in _BUILTIN_HANDLERS, (
            f"{name} 未在 _BUILTIN_HANDLERS 登记："
            "LLM 调它会走到「has no implementation」分支"
        )


# ── ToolRegistry.execute() 端到端 ────────────────────────────────


class TestExecuteFileTools:
    """每个文件工具都能被 execute() 真正驱动，并返回非错误结果。"""

    @pytest.mark.asyncio
    async def test_list_dir_returns_files(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
        (tmp_path / "b").mkdir()

        result = await tools.execute("list_dir", {"path": str(tmp_path)})

        # 修复前会得到 "[Error] tool list_dir has no implementation"
        assert "has no implementation" not in result
        assert "a.txt" in result
        assert "b/" in result  # 目录带 / 后缀
        assert "[DIR]" in result
        assert "[FILE]" in result

    @pytest.mark.asyncio
    async def test_list_dir_empty_dir(self, tmp_path: Path):
        result = await tools.execute("list_dir", {"path": str(tmp_path)})
        assert result == "(empty)"

    @pytest.mark.asyncio
    async def test_list_dir_missing_path(self, tmp_path: Path):
        result = await tools.execute("list_dir", {"path": str(tmp_path / "nope")})
        assert result.startswith("[Error]")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_read_file_roundtrip(self, tmp_path: Path):
        p = tmp_path / "hello.txt"
        p.write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = await tools.execute("read_file", {"path": str(p)})

        assert "has no implementation" not in result
        assert "line1" in result and "line3" in result

    @pytest.mark.asyncio
    async def test_write_file_creates_file(self, tmp_path: Path):
        p = tmp_path / "out.txt"
        result = await tools.execute(
            "write_file", {"path": str(p), "content": "xyz"})

        assert "has no implementation" not in result
        assert "[OK]" in result
        assert p.read_text(encoding="utf-8") == "xyz"

    @pytest.mark.asyncio
    async def test_write_file_append_mode(self, tmp_path: Path):
        p = tmp_path / "out.txt"
        p.write_text("a-", encoding="utf-8")

        await tools.execute("write_file",
                            {"path": str(p), "content": "b", "mode": "a"})

        assert p.read_text(encoding="utf-8") == "a-b"

    @pytest.mark.asyncio
    async def test_glob_finds_matches(self, tmp_path: Path):
        (tmp_path / "x.py").write_text("", encoding="utf-8")
        (tmp_path / "y.py").write_text("", encoding="utf-8")
        (tmp_path / "z.txt").write_text("", encoding="utf-8")

        result = await tools.execute(
            "glob", {"pattern": "*.py", "path": str(tmp_path)})

        assert "has no implementation" not in result
        assert "x.py" in result
        assert "y.py" in result
        assert "z.txt" not in result

    @pytest.mark.asyncio
    async def test_grep_finds_lines(self, tmp_path: Path):
        (tmp_path / "src.txt").write_text(
            "hello world\nfoo bar\nhello again\n", encoding="utf-8")

        result = await tools.execute(
            "grep", {"pattern": "hello", "path": str(tmp_path)})

        assert "has no implementation" not in result
        assert "hello world" in result
        assert "hello again" in result
        assert "foo bar" not in result

    @pytest.mark.asyncio
    async def test_grep_invalid_regex_returns_error(self, tmp_path: Path):
        result = await tools.execute(
            "grep", {"pattern": "[unclosed", "path": str(tmp_path)})
        assert result.startswith("[Error]")
        assert "invalid regex" in result

    @pytest.mark.asyncio
    async def test_file_info_for_file(self, tmp_path: Path):
        p = tmp_path / "f.txt"
        p.write_text("abc", encoding="utf-8")

        result = await tools.execute("file_info", {"path": str(p)})

        assert "has no implementation" not in result
        assert "[OK]" in result
        assert "file" in result
        assert "3 bytes" in result

    @pytest.mark.asyncio
    async def test_file_info_for_dir(self, tmp_path: Path):
        result = await tools.execute("file_info", {"path": str(tmp_path)})
        assert "has no implementation" not in result
        assert "dir" in result


# ── 工具集完整性 ──────────────────────────────────────────────


class TestAllFileToolsHaveHandler:
    """FILE_TOOLS（metadata 声明）与 _BUILTIN_HANDLERS（实际执行）必须对齐。

    任何只声明 ToolDef 而没注册 handler 的工具都会触发同一个 bug。
    """

    def test_all_file_tools_in_handler_table(self):
        # 直接读 builtin.FILE_TOOLS，避免对 metadata 实现细节的过度耦合
        from tool.builtin import FILE_TOOLS
        declared = {t.name for t in FILE_TOOLS}
        for name in declared:
            assert name in _BUILTIN_HANDLERS, (
                f"FILE_TOOLS 声明了 {name} 但 _BUILTIN_HANDLERS 没登记 → "
                "调用时返回 has no implementation"
            )