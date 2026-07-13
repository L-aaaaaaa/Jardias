"""回归测试：文件工具能通过 ToolRegistry.execute() 真正被调度到 handler。

背景：
    builtin.py 的 ``_BUILTIN_HANDLERS`` 历史上只登记了「业务工具」，而文件工具
    (_read_file / _write_file / _get_directory_tree / _search_in_path /
    _search_in_content / _get_file_metadata) 只有 ToolDef 元数据，没有 fn
    指针也没进调度表 → LLM 调 get_directory_tree 会拿到
    ``[Error] tool get_directory_tree has no implementation``。

本测试直接走 ``tools.execute(name, args)`` 的真实分支：
    1. ``_BUILTIN_HANDLERS`` 里必须登记所有文件工具（防止再被退回）。
    2. 实际调用结果必须不是「no implementation」错误。
    3. 业务行为：get_directory_tree 能列出真实文件、read_file 能读真实内容、
       write_file 能写入、search_in_content 能匹配、search_in_path 能匹配、
       get_file_metadata 能拿元信息。
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

from tool.builtin import tools, _BUILTIN_HANDLERS  # noqa: F401


FILE_TOOL_NAMES = (
    "read_file", "write_file", "get_directory_tree", "search_in_path", "search_in_content", "get_file_metadata",
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
    async def test_get_directory_tree_returns_files(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
        (tmp_path / "b").mkdir()

        result = await tools.execute("get_directory_tree", {"path": str(tmp_path)})

        # 修复前会得到 "[Error] tool get_directory_tree has no implementation"
        assert "has no implementation" not in result
        assert "a.txt" in result
        assert "b/" in result  # 目录带 / 后缀
        assert "[DIR]" in result
        assert "[FILE]" in result

    @pytest.mark.asyncio
    async def test_get_directory_tree_empty_dir(self, tmp_path: Path):
        result = await tools.execute("get_directory_tree", {"path": str(tmp_path)})
        assert result == "(empty)"

    @pytest.mark.asyncio
    async def test_get_directory_tree_missing_path(self, tmp_path: Path):
        result = await tools.execute("get_directory_tree", {"path": str(tmp_path / "nope")})
        assert result.startswith("[Error]")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_get_directory_tree_depth_two(self, tmp_path: Path):
        """depth=2 应展开一层子目录,使用 tree 风格的分支符号。"""
        (tmp_path / "top.txt").write_text("x", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "inner.txt").write_text("y", encoding="utf-8")
        (tmp_path / "sub" / "subsub").mkdir()

        result = await tools.execute(
            "get_directory_tree", {"path": str(tmp_path), "depth": 2})

        assert "has no implementation" not in result
        assert "top.txt" in result
        assert "inner.txt" in result
        assert "subsub" in result
        # tree 风格分支符号
        assert "|--" in result or "`--" in result

    @pytest.mark.asyncio
    async def test_get_directory_tree_recursive_flag(self, tmp_path: Path):
        """recursive=True 应展开所有层,depth=1 时被覆盖。"""
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "b").mkdir()
        (tmp_path / "a" / "b" / "c.txt").write_text("z", encoding="utf-8")

        result = await tools.execute(
            "get_directory_tree", {"path": str(tmp_path), "recursive": True})

        assert "c.txt" in result
        assert "|--" in result or "`--" in result

    @pytest.mark.asyncio
    async def test_get_directory_tree_depth_overrides_recursive(self, tmp_path: Path):
        """depth > 1 时即使 recursive=False 也会展开到 depth 层。"""
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "b.txt").write_text("x", encoding="utf-8")
        (tmp_path / "a" / "deep").mkdir()
        (tmp_path / "a" / "deep" / "c.txt").write_text("y", encoding="utf-8")

        # depth=2 但 recursive=False：仍应展开到 a 下的 b.txt 和 deep/。
        result = await tools.execute(
            "get_directory_tree", {"path": str(tmp_path), "depth": 2, "recursive": False})
        assert "b.txt" in result
        # depth=2 不足以看到 c.txt
        assert "c.txt" not in result

    @pytest.mark.asyncio
    async def test_get_directory_tree_truncation_with_note(self, tmp_path: Path):
        """超过 max_entries 时应截断并提示。"""
        # 生成 5 个文件,max_entries=2 触发截断
        for i in range(5):
            (tmp_path / f"f{i}.txt").write_text(str(i), encoding="utf-8")

        result = await tools.execute(
            "get_directory_tree", {"path": str(tmp_path), "max_entries": 2})

        assert "... and 3 more entries" in result

    @pytest.mark.asyncio
    async def test_get_directory_tree_handles_unreadable_subdir(self, tmp_path: Path):
        """子目录不可读时不应让整个 get_directory_tree 崩掉。"""
        sub = tmp_path / "locked"
        sub.mkdir()
        # 触发 PermissionError:在 POSIX 上把目录权限收为 0。
        if sys.platform != "win32":
            import os
            os.chmod(sub, 0o000)
            try:
                result = await tools.execute(
                    "get_directory_tree", {"path": str(tmp_path), "depth": 3})
                assert "has no implementation" not in result
                assert "locked" in result
            finally:
                os.chmod(sub, 0o755)
        else:
            # Windows 上无简单方式模拟拒绝访问,跳过即可——只保证不崩。
            result = await tools.execute(
                "get_directory_tree", {"path": str(tmp_path), "depth": 3})
            assert "has no implementation" not in result

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
    async def test_search_in_path_finds_matches(self, tmp_path: Path):
        (tmp_path / "x.py").write_text("", encoding="utf-8")
        (tmp_path / "y.py").write_text("", encoding="utf-8")
        (tmp_path / "z.txt").write_text("", encoding="utf-8")

        result = await tools.execute(
            "search_in_path", {"pattern": "*.py", "path": str(tmp_path)})

        assert "has no implementation" not in result
        assert "x.py" in result
        assert "y.py" in result
        assert "z.txt" not in result

    @pytest.mark.asyncio
    async def test_search_in_content_finds_lines(self, tmp_path: Path):
        (tmp_path / "src.txt").write_text(
            "hello world\nfoo bar\nhello again\n", encoding="utf-8")

        result = await tools.execute(
            "search_in_content", {"pattern": "hello", "path": str(tmp_path)})

        assert "has no implementation" not in result
        assert "hello world" in result
        assert "hello again" in result
        assert "foo bar" not in result

    @pytest.mark.asyncio
    async def test_search_in_content_invalid_regex_returns_error(self, tmp_path: Path):
        result = await tools.execute(
            "search_in_content", {"pattern": "[unclosed", "path": str(tmp_path)})
        assert result.startswith("[Error]")
        assert "invalid regex" in result

    @pytest.mark.asyncio
    async def test_get_file_metadata_for_file(self, tmp_path: Path):
        p = tmp_path / "f.txt"
        p.write_text("abc", encoding="utf-8")

        result = await tools.execute("get_file_metadata", {"path": str(p)})

        assert "has no implementation" not in result
        assert "[OK]" in result
        assert "file" in result
        assert "3 bytes" in result

    @pytest.mark.asyncio
    async def test_get_file_metadata_for_dir(self, tmp_path: Path):
        result = await tools.execute("get_file_metadata", {"path": str(tmp_path)})
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


# ── 乱码防护 ──────────────────────────────────────────────
#
# 背景：历史上有两起"乱码进 history.json → 召回时再渲染"的回归：
# 1. execute_command 工具硬编码 encoding="utf-8",Windows cmd 输出 OEM 代码页(cp936/GBK)
#    → 整段中文文件名/输出被乱码替换。
# 2. search_in_content/read_file 的 _is_text_file 只读 512 字节且用 errors="replace",
#    让 .git/index 这类无扩展名二进制文件通过判定 → 整行二进制字节被输出。
# 这两个修复必须守住,否则召回历史时会再次复现乱码。


class TestBinaryDetection:
    """`_is_text_file` / `_sanitize_binary_line` 的二进制识别能力。"""

    def test_text_file_passes(self, tmp_path: Path):
        from tool.builtin_tools.files import _is_text_file
        p = tmp_path / "hello.txt"
        p.write_text("hello world\n你好世界\n", encoding="utf-8")
        assert _is_text_file(p) is True

    def test_pyc_extension_rejected(self, tmp_path: Path):
        from tool.builtin_tools.files import _is_text_file
        p = tmp_path / "module.pyc"
        p.write_bytes(b"\x00\x01\x02\x03")
        assert _is_text_file(p) is False

    def test_git_index_rejected(self, tmp_path: Path):
        from tool.builtin_tools.files import _is_text_file
        # 模拟 .git/index:无扩展名 + 二进制内容
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        idx = git_dir / "index"
        # 真实 .git/index 的前几字节含 NUL 和不可打印字符
        idx.write_bytes(b"DIRC\x00\x00\x00\x02\x00\x00\x00\x05"
                         b"\x00\x0a\x9d\x60\xe7\xa4\x81\x12"
                         b"\x00\x0a\x9d\x60\xe7\xa4\x81\x12")
        assert _is_text_file(idx) is False

    def test_null_byte_in_content_rejected(self, tmp_path: Path):
        from tool.builtin_tools.files import _is_text_file
        p = tmp_path / "weird.bin"
        p.write_bytes(b"some text\x00\x01\x02\x03more")
        assert _is_text_file(p) is False

    def test_sanitize_binary_line_strips_null(self):
        from tool.builtin_tools.files import _sanitize_binary_line
        assert _sanitize_binary_line("hello\x00world") is None

    def test_sanitize_binary_line_replaces_nothing_now(self):
        from tool.builtin_tools.files import _sanitize_binary_line
        # 含 \x01 这种控制字符的整行：旧版会替换为 U+FFFD,
        # 新版（只过滤 NUL）会保留原字符,靠文件级 NUL 字节检测拦截二进制行。
        # 这里验证含 \x01 的行不返回 None（让 search_in_content 在更高层级过滤）。
        assert _sanitize_binary_line("hello\x01world") is not None


class TestSearchInContentSkipsBinary:
    """`search_in_content` 必须跳过二进制文件（包括 .git/index 这类无扩展名）。"""

    @pytest.mark.asyncio
    async def test_search_in_content_skips_git_index(self, tmp_path: Path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        idx = git_dir / "index"
        # .git/index 是 git 的二进制索引,里面含很多不可打印字符
        idx.write_bytes(b"DIRC\x00\x00\x00\x02" + b"\x00" * 200 + b"some text")

        # 同时放一个文本文件,search_in_content 应只匹配到文本文件
        (tmp_path / "src.txt").write_text("hello world\n", encoding="utf-8")

        result = await tools.execute("search_in_content", {"pattern": "hello", "path": str(tmp_path)})

        assert "src.txt" in result
        assert "index" not in result, (
            "search_in_content 把 .git/index 当文本读了 → 整行二进制字节被当成匹配结果输出"
        )

    @pytest.mark.asyncio
    async def test_search_in_content_skips_pyc_files(self, tmp_path: Path):
        (tmp_path / "mod.pyc").write_bytes(b"\x00\x01magic\x00\x02hello\x00\x03")
        (tmp_path / "src.txt").write_text("hello\n", encoding="utf-8")

        result = await tools.execute("search_in_content", {"pattern": "hello", "path": str(tmp_path)})

        assert "src.txt" in result
        assert "mod.pyc" not in result


class TestReadFileRejectsBinary:
    """`read_file` 不应让二进制文件溜过去（之前会被 errors='replace' 解码成乱码）。"""

    @pytest.mark.asyncio
    async def test_read_file_rejects_git_index(self, tmp_path: Path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        idx = git_dir / "index"
        idx.write_bytes(b"DIRC\x00\x00\x00\x02" + b"\x00" * 200)

        result = await tools.execute("read_file", {"path": str(idx)})

        assert result.startswith("[Error]")
        assert "二进制文件" in result

    @pytest.mark.asyncio
    async def test_read_file_rejects_pyc(self, tmp_path: Path):
        p = tmp_path / "mod.pyc"
        p.write_bytes(b"\x00\x01\x02\x03")

        result = await tools.execute("read_file", {"path": str(p)})

        assert result.startswith("[Error]")
        assert "二进制文件" in result


class TestExecuteCommandEncoding:
    """`execute_command` 工具在 Windows 上不应把 cmd 输出按 UTF-8 错误解码。

    验证策略：Unix 环境下命令前缀不会被加上 chcp（直接检查）。"""

    @pytest.mark.asyncio
    async def test_execute_command_does_not_prefix_chcp_on_unix(self, monkeypatch):
        """非 Windows 平台：命令保持原样,不会加 chcp 前缀。"""
        import tool.builtin_tools.files as files_mod

        captured: dict = {}

        class _FakeResult:
            stdout = "hello\n"
            stderr = ""
            returncode = 0

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["encoding"] = kwargs.get("encoding")
            return _FakeResult()

        monkeypatch.setattr(files_mod.sys, "platform", "linux")
        monkeypatch.setattr(files_mod.subprocess, "run", _fake_run)

        result = files_mod.execute_command({"command": "echo hello"})

        assert captured["cmd"] == "echo hello"
        assert captured["encoding"] == "utf-8"
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_execute_command_prefixes_chcp_on_windows(self, monkeypatch):
        """Windows 平台：自动加 chcp 65001 前缀避免 cmd OEM 代码页乱码。"""
        import tool.builtin_tools.files as files_mod

        captured: dict = {}

        class _FakeResult:
            stdout = "你好\n"
            stderr = ""
            returncode = 0

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["encoding"] = kwargs.get("encoding")
            return _FakeResult()

        monkeypatch.setattr(files_mod.sys, "platform", "win32")
        monkeypatch.setattr(files_mod.subprocess, "run", _fake_run)

        files_mod.execute_command({"command": "echo 你好"})

        assert captured["cmd"].startswith("chcp 65001")
        assert "echo 你好" in captured["cmd"]
        assert captured["encoding"] == "utf-8"

    @pytest.mark.asyncio
    async def test_execute_command_does_not_double_prefix_chcp(self, monkeypatch):
        """用户命令已含 chcp 时不要再加一次。"""
        import tool.builtin_tools.files as files_mod

        captured: dict = {}

        class _FakeResult:
            stdout = ""
            stderr = ""
            returncode = 0

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakeResult()

        monkeypatch.setattr(files_mod.sys, "platform", "win32")
        monkeypatch.setattr(files_mod.subprocess, "run", _fake_run)

        files_mod.execute_command({"command": "chcp 1252 && dir"})

        # 只应该出现一次 chcp
        assert captured["cmd"].lower().count("chcp ") == 1