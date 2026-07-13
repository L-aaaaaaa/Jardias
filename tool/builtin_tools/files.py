"""builtin_tools/files — 系统/文件系统工具。

``bash`` / ``read_file`` / ``write_file`` / ``list_dir`` / ``glob`` /
``grep`` / ``file_info``。这些是 dispatch 兼容的（签名是 keyword args），
``tool.builtin.execute`` 会优先 ``**arguments`` 展开。

文件工具原本在 ``builtin.py`` 里使用下划线前缀（私有），本文件统一
改用 public 名字以便其他模块直接 import——但 ``TOOL_REGISTRY`` 里的
工具名仍用同样的字符串（dispatch 走 ``_BUILTIN_HANDLERS``）。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
from datetime import datetime


def _resolve_path(path: str) -> pathlib.Path:
    p = pathlib.Path(path).expanduser()
    if not p.is_absolute(): p = pathlib.Path.cwd() / p
    return p.resolve()


def _is_text_file(path: pathlib.Path) -> bool:
    binary = {".pyc", ".png", ".jpg", ".gif", ".pdf", ".zip", ".exe", ".dll", ".so", ".woff", ".woff2", ".ttf"}
    if path.suffix.lower() in binary: return False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.read(512)
        return True
    except (OSError, UnicodeDecodeError):
        return False


def handle_bash(arguments: dict) -> str:
    from tool.builtin import _format_error

    command = arguments["command"]
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace",
        )
        out = []
        if result.stdout.strip(): out.append(result.stdout.strip())
        if result.stderr.strip(): out.append(f"[stderr]\n{result.stderr.strip()}")
        if not out: out.append(f"(exit code {result.returncode})")
        return "\n".join(out)
    except subprocess.TimeoutExpired:
        return "[Error] 命令超时（30s）"
    except Exception as e:
        return _format_error(e)


async def read_file(path: str, line_range: str | None = None) -> str:
    from tool.builtin import _format_error

    try:
        resolved = _resolve_path(path)
        if not resolved.exists(): return f"[Error] file not found: {path}"
        img_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".ico", ".tiff",
                    ".svg"}  # 图片/二进制文件不应直接读取，提示使用 vision 能力
        if resolved.suffix.lower() in img_exts:
            return (
                f"[提示] {path} 是图片文件，不要用 read_file 读取。"
                f"如果你有 vision 能力，请直接要求用户发送图片给你看；"
                f"如果没有 vision，请用 update_runtime 切换到 vision 智能基元。"
            )
        content = resolved.read_text(encoding="utf-8", errors="replace")
        if line_range:
            parts = line_range.split(",")
            if len(parts) == 2:
                start, end = int(parts[0]), int(parts[1])
                lines = content.split("\n")
                start, end = max(1, start) - 1, min(len(lines), end)
                content = "\n".join(lines[start:end])
        return content
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return _format_error(e)


async def write_file(path: str, content: str, mode: str = "w") -> str:
    from tool.builtin import _format_error

    try:
        resolved = _resolve_path(path);
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if mode == "a" and resolved.exists(): content = resolved.read_text(encoding="utf-8") + content
        resolved.write_text(content, encoding="utf-8")
        return f"[OK] wrote {len(content)} chars to {path}"
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return _format_error(e)


async def list_dir(path: str = ".") -> str:
    from tool.builtin import _format_error

    try:
        resolved = _resolve_path(path)
        if not resolved.exists(): return f"[Error] dir not found: {path}"
        if not resolved.is_dir(): return f"[Error] not a dir: {path}"
        items = []
        for item in sorted(resolved.iterdir()):
            suffix = "/" if item.is_dir() else ""
            size = ""
            if item.is_file():
                try:
                    size = f" ({item.stat().st_size} bytes)"
                except OSError:
                    pass
            label = "[DIR]" if item.is_dir() else "[FILE]"
            items.append(f"{label} {item.name}{suffix}{size}")
        return "\n".join(items) if items else "(empty)"
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return _format_error(e)


async def glob(pattern: str, path: str = ".") -> str:
    from tool.builtin import _format_error

    try:
        base = _resolve_path(path)
        matches = sorted([str(p.relative_to(base)) for p in base.glob(pattern) if p.is_file()])
        if not matches: return f"[Info] no matches for {pattern}"
        return f"共 {len(matches)} matches:\n" + "\n".join(matches)
    except Exception as e:
        return _format_error(e)


async def grep(pattern: str, path: str = ".", case_insensitive: bool = False, max_results: int = 20) -> str:
    from tool.builtin import _format_error

    try:
        re.compile(pattern)
    except re.error as e:
        return f"[Error] invalid regex: {e}"
    flags = re.IGNORECASE if case_insensitive else 0
    base = _resolve_path(path)
    results = [];
    total = 0
    files = [base] if base.is_file() else [f for f in base.rglob("*") if f.is_file() and _is_text_file(f)]
    for file_path in files:
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line, flags):
                snippet = line.strip()
                if len(snippet) > 120: snippet = snippet[:120] + "..."
                results.append(f"  {file_path.name}:{i}: {snippet}")
                total += 1
                if total >= max_results: break
        if total >= max_results: break
    if not results: return f"[Info] no matches for {pattern}"
    suffix = f"\n... and {total - max_results} more" if total > max_results else ""
    header = f"grep '{pattern}' ({total} matches):\n"
    return header + "\n".join(results[:max_results]) + suffix


async def file_info(path: str) -> str:
    from tool.builtin import _format_error

    try:
        resolved = _resolve_path(path)
        if not resolved.exists(): return f"[Error] not found: {path}"
        stat = resolved.stat()
        is_dir = resolved.is_dir()
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        size = "" if is_dir else f" size: {stat.st_size} bytes"
        label = "dir" if is_dir else "file"
        return f"[OK] {label}: {path}\n  path: {resolved}\n  modified: {mtime}{size}"
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return _format_error(e)


HANDLERS: dict[str, callable] = {
    "bash": handle_bash,
    "read_file": read_file,
    "write_file": write_file,
    "list_dir": list_dir,
    "glob": glob,
    "grep": grep,
    "file_info": file_info,
}
