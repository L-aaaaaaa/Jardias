"""Unit tests for tool/builtin_tools/files."""
from __future__ import annotations

import pytest

# 重要：先 import tool.builtin 触发 _BUILTIN_HANDLERS 填充，
# 再 import tool.builtin_tools.files 避免循环。
from tool.builtin import ToolRegistry  # noqa: F401
from tool.builtin_tools.files import (
    _is_text_file,
    _sanitize_binary_line,
    _format_entry,
    get_directory_tree,
    read_file,
)


# ────────────────────────────────────────────────────────────────────
# _is_text_file — 二进制检测启发式
# ────────────────────────────────────────────────────────────────────


def test_is_text_file_accepts_plain_text(tmp_path):
    p = tmp_path / "hello.txt"
    p.write_text("Hello, world!\n", encoding="utf-8")
    assert _is_text_file(p) is True


def test_is_text_file_rejects_known_binary_extension(tmp_path):
    p = tmp_path / "logo.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50)
    assert _is_text_file(p) is False


def test_is_text_file_treats_empty_file_as_text(tmp_path):
    p = tmp_path / "empty.txt"
    p.touch()
    assert _is_text_file(p) is True


def test_is_text_file_rejects_nul_byte_payload(tmp_path):
    p = tmp_path / "weird.dat"
    p.write_bytes(b"abc\x00def")
    assert _is_text_file(p) is False


def test_is_text_file_rejects_git_binary_index(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    idx = git_dir / "index"
    idx.write_bytes(b"DIRC\x00" + b"x" * 100)
    assert _is_text_file(idx) is False


def test_is_text_file_accepts_chinese(tmp_path):
    p = tmp_path / "chinese.txt"
    p.write_text("你好，世界！", encoding="utf-8")
    assert _is_text_file(p) is True


# ────────────────────────────────────────────────────────────────────
# _sanitize_binary_line — 行级过滤
# ────────────────────────────────────────────────────────────────────


def test_sanitize_binary_line_passes_normal_text():
    assert _sanitize_binary_line("hello world") == "hello world"


def test_sanitize_binary_line_rejects_nul_line():
    assert _sanitize_binary_line("abc\x00def") is None


def test_sanitize_binary_line_rejects_control_char_heavy_line():
    # 0x01 0x02 等 ASCII 控制字符占比过高
    line = "".join(chr(i) for i in range(1, 30))
    assert _sanitize_binary_line(line) is None


def test_sanitize_binary_line_allows_chinese_with_tab():
    # 制表符在白名单内
    assert _sanitize_binary_line("你好\tworld") == "你好\tworld"


# ────────────────────────────────────────────────────────────────────
# _format_entry
# ────────────────────────────────────────────────────────────────────


def test_format_entry_marks_directory_with_slash(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    out = _format_entry(d)
    assert "[DIR]" in out
    assert out.endswith("sub/")


def test_format_entry_marks_file_with_size(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"abc")
    out = _format_entry(f)
    assert "[FILE]" in out
    assert "data.bin" in out
    assert "3 bytes" in out


# ────────────────────────────────────────────────────────────────────
# get_directory_tree — 4 种深度形态
# ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def tree_dir(tmp_path):
    """构造一个简单的层级目录：
       root/
         file.txt
         sub1/
           inner.txt
           sub2/
             deep.txt
    """
    (tmp_path / "file.txt").write_text("a", encoding="utf-8")
    sub1 = tmp_path / "sub1"
    sub1.mkdir()
    (sub1 / "inner.txt").write_text("b", encoding="utf-8")
    sub2 = sub1 / "sub2"
    sub2.mkdir()
    (sub2 / "deep.txt").write_text("c", encoding="utf-8")
    return tmp_path


def test_get_directory_tree_default_depth_one_is_flat(tree_dir):
    out = asyncio_run(get_directory_tree(str(tree_dir)))
    lines = out.splitlines()
    assert any("file.txt" in line for line in lines)
    assert any("sub1" in line for line in lines)
    assert "inner.txt" not in out  # depth=1 不展开子目录


def test_get_directory_tree_depth_two_shows_one_level_children(tree_dir):
    out = asyncio_run(get_directory_tree(str(tree_dir), depth=2))
    lines = out.splitlines()
    assert any("file.txt" in line for line in lines)
    assert any("sub1/" in line for line in lines)
    assert any("inner.txt" in line for line in lines)  # 子目录里的文件出现
    assert "deep.txt" not in out


def test_get_directory_tree_recursive_equivalent_to_depth_inf(tree_dir):
    out = asyncio_run(get_directory_tree(str(tree_dir), recursive=True))
    assert "file.txt" in out
    assert "sub1" in out
    assert "inner.txt" in out
    assert "deep.txt" in out


def test_get_directory_tree_reports_error_for_missing_path(tmp_path):
    out = asyncio_run(get_directory_tree(str(tmp_path / "nope")))
    assert "[Error]" in out
    assert "nope" in out


def test_get_directory_tree_reports_error_for_non_directory(tmp_path):
    f = tmp_path / "a_file"
    f.write_text("x", encoding="utf-8")
    out = asyncio_run(get_directory_tree(str(f)))
    assert "[Error]" in out
    assert "not a dir" in out


def test_get_directory_tree_truncates_at_max_entries(tmp_path):
    # 制造 6 个文件 + max_entries=2
    for i in range(6):
        (tmp_path / f"file{i}.txt").write_text("x", encoding="utf-8")
    out = asyncio_run(get_directory_tree(str(tmp_path), max_entries=2))
    assert "and 4 more entries" in out


# ────────────────────────────────────────────────────────────────────
# read_file
# ────────────────────────────────────────────────────────────────────


def test_read_file_returns_not_found_for_missing_path(tmp_path):
    out = asyncio_run(read_file(str(tmp_path / "absent.txt")))
    assert "[Error] file not found" in out


def test_read_file_refuses_to_read_binary_file(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00\x01\x02" * 30)
    out = asyncio_run(read_file(str(f)))
    assert "[Error]" in out
    assert "二进制" in out


def test_read_file_refuses_to_read_image_file(tmp_path):
    f = tmp_path / "cover.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50)
    out = asyncio_run(read_file(str(f)))
    assert "[提示]" in out
    assert "vision" in out


def test_read_file_returns_line_range_slice(tmp_path):
    f = tmp_path / "lines.txt"
    f.write_text("\n".join(f"line {i}" for i in range(1, 11)), encoding="utf-8")
    out = asyncio_run(read_file(str(f), line_range="3,5"))
    assert "line 3" in out
    assert "line 5" in out
    assert "line 2" not in out


def test_read_file_handles_permission_error(tmp_path, monkeypatch):
    f = tmp_path / "secret.txt"
    f.write_text("hi", encoding="utf-8")
    real_read_text = f.read_text

    def deny(path, *args, **kwargs):
        raise PermissionError("nope")

    monkeypatch.setattr("pathlib.Path.read_text", deny)
    out = asyncio_run(read_file(str(f)))
    assert "permission denied" in out


# ────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────


def asyncio_run(coro):
    """同步包装 helper：Python 3.10+ 在无 running loop 的 main thread 上
    `asyncio.get_event_loop()` 会失败。这里优先沿用已有 loop，缺失时回退
    `asyncio.run()` 让其自建/自销毁。"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
