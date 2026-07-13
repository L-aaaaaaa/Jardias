"""builtin_tools/files — 系统/文件系统工具。

``execute_command`` / ``read_file`` / ``write_file`` /
``get_directory_tree`` / ``search_in_path`` / ``search_in_content`` /
``get_file_metadata``。这些是 dispatch 兼容的（签名是 keyword args），
``tool.builtin.execute`` 会优先 ``**arguments`` 展开。

文件工具原本在 ``builtin.py`` 里使用下划线前缀（私有），本文件统一
改用 public 名字以便其他模块直接 import——但 ``TOOL_REGISTRY`` 里的
工具名仍用同样的字符串（dispatch 走 ``_BUILTIN_HANDLERS``）。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
from datetime import datetime

# Windows cmd 默认代码页 = OEM 代码页（中文系统上是 cp936/GBK），
# Python subprocess 默认按 UTF-8 解码 stdout 会乱码。
# 在命令前加 chcp 65001 强制 cmd 输出 UTF-8——跨地区一致的最稳方案。
_CHCP_UTF8 = "chcp 65001 >nul 2>&1"


def _resolve_path(path: str) -> pathlib.Path:
    p = pathlib.Path(path).expanduser()
    if not p.is_absolute(): p = pathlib.Path.cwd() / p
    return p.resolve()


# ── 二进制文件检测 ──────────────────────────────────────────
# POSIX 启发式：文本文件不含 NUL 字节，且可打印字符占绝大多数。
# 仅靠扩展名黑名单会漏掉 .git/index 这类无扩展名的二进制文件；
# 仅靠 encoding="utf-8" + errors="replace" 又会让任意字节序列都"通过"。
_BINARY_EXT = {
    ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".flac", ".ogg", }
# .git/ 下的关键文件全是二进制
_GIT_BINARY_NAMES = {"index", "pack", "lock", "shallow", "modules"}


def _is_text_file(path: pathlib.Path) -> bool:
    """判断文件是否可作为文本读取。

    启发式规则：
    1. 已知二进制扩展名 → False
    2. .git/ 下的关键文件 → False
    3. 含 NUL 字节 → False（NUL 在 UTF-8/GBK 文本中几乎不存在）
    4. 非可打印字符比例 > 30% → False
    """
    if path.suffix.lower() in _BINARY_EXT:
        return False
    if path.name in _GIT_BINARY_NAMES and any(p.name == ".git" for p in path.parents):
        return False
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
    except OSError:
        return False
    if not chunk:
        return True  # 空文件视为文本
    if b"\x00" in chunk:
        return False
    text_chars = {9, 10, 13, *range(32, 127), *range(128, 256)}
    non_text = sum(1 for b in chunk if b not in text_chars)
    return non_text / len(chunk) <= 0.30


def _sanitize_binary_line(line: str) -> str | None:
    """单行二进制过滤。

    含 NUL 或过多 ASCII 控制字符的整行视为二进制，返回 None（由调用方跳过）。
    中文等高码点字符不在白名单/黑名单里——它们都是合法文本，
    只有 NUL 和 0x00-0x1F/0x7F 这类 ASCII 控制字符才是二进制行的标志。
    """
    if "\x00" in line: return None
    non_text = 0
    for ch in line:
        o = ord(ch)
        # ASCII 控制字符：除 tab/lf/vt/ff/cr 外视为不可打印
        if o < 0x20 and o not in (9, 10, 11, 12, 13):
            non_text += 1
        elif o == 0x7F:
            non_text += 1  # DEL
    if line and non_text / len(line) > 0.30:  return None
    return line


def execute_command(arguments: dict) -> str:
    from tool.builtin import _format_error

    command = arguments["command"]
    # Windows cmd 默认输出 OEM 代码页（cp936/cp437），用 UTF-8 解码会乱码。
    # 在命令前加 chcp 65001 强制 cmd 输出 UTF-8——跨地区一致的方案。
    # 命令本身已含 chcp 时不再重复加。
    if sys.platform == "win32":
        stripped = command.lstrip().lower()
        if not (stripped.startswith("chcp ") or stripped.startswith("@chcp ")):
            command = f"{_CHCP_UTF8} && {command}"
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace", )
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
        # 图片/二进制文件不应直接读取，提示使用 vision 能力
        img_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".svg"}
        if resolved.suffix.lower() in img_exts:
            return (
                f"[提示] {path} 是图片文件，不要用 read_file 读取。"
                f"如果你有 vision 能力，请直接要求用户发送图片给你看；"
                f"如果没有 vision，请用 update_runtime 切换到 vision 智能基元。")
        # 二进制文件直接拒绝读取（避免误读 .git/index 等生成乱码塞进 history）
        if not _is_text_file(resolved):
            return (
                f"[Error] {path} 是二进制文件，无法用 read_file 读取。"
                f"如需查看文本片段请用 search_in_content 在已知文本文件上搜索。")
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


# 递归遍历的安全边界：条目总数超过此值就截断，避免一次吐爆上下文。
_LIST_DIR_MAX_ENTRIES = 500


def _format_entry(item: pathlib.Path) -> str:
    suffix = "/" if item.is_dir() else ""
    size = ""
    if item.is_file():
        try:
            size = f" ({item.stat().st_size} bytes)"
        except OSError:
            pass
    label = "[DIR]" if item.is_dir() else "[FILE]"
    return f"{label} {item.name}{suffix}{size}"


async def get_directory_tree(
        path: str = ".", depth: int = 1, recursive: bool = False,
        max_entries: int = _LIST_DIR_MAX_ENTRIES, ) -> str:
    """列出目录条目；支持按深度或递归展开。

    参数语义：
        depth = 1（默认）：仅列出一层，与旧版行为一致。
        depth > 1：递归展开到指定层数，tree 风格缩进。
        depth <= 0：按"无限"处理（实际仍受 max_entries 截断）。
        recursive = True：等价为 depth = ∞，但优先级低于 depth——
        即 depth > 1 时即使 recursive = False 也会展开到 depth 层。

    超过 max_entries 时返回前 N 条并在末尾追加提示，便于模型缩小范围。
    """
    from tool.builtin import _format_error

    try:
        resolved = _resolve_path(path)
        if not resolved.exists(): return f"[Error] dir not found: {path}"
        if not resolved.is_dir(): return f"[Error] not a dir: {path}"

        # depth 优先；recursive=True 且 depth=1 时才升级为无限。
        effective_depth = depth
        if depth <= 0 or recursive:
            # 用一个足够大的上限表示"无限"，遍历函数内部按 max_entries 截断。
            effective_depth = 10_000

        # depth=1：维持原有扁平输出（向后兼容）。
        if effective_depth == 1:
            items = [_format_entry(item) for item in sorted(resolved.iterdir())]
            if not items: return "(empty)"
            if len(items) > max_entries:
                head = "\n".join(items[:max_entries])
                return (
                    f"{head}\n... and {len(items) - max_entries} more entries "
                    f"(use depth=N or smaller path)")
            return "\n".join(items)

        # depth > 1：tree 风格输出。
        lines: list[str] = []
        truncated = False
        skipped = 0

        # remaining 表示"还能再往下展开多少层"。
        # depth=2 时根下条目用 prefix="" 打印，子目录还剩 1 层可展开 → depth-1=1。
        def _walk(directory: pathlib.Path, remaining: int, prefix: str) -> None:
            nonlocal truncated, skipped
            if truncated: return
            try:
                children = sorted(directory.iterdir())
            except (PermissionError, OSError):
                lines.append(f"{prefix}[Error] cannot read: {directory.name}")
                return
            for idx, child in enumerate(children):
                if len(lines) >= max_entries:
                    truncated = True
                    skipped += 1
                    continue
                is_last = idx == len(children) - 1
                # 用 ASCII 分支符，跨平台 + Windows 默认终端都不会乱码。
                branch = "`-- " if is_last else "|-- "
                entry = _format_entry(child)
                lines.append(f"{prefix}{branch}{entry}")
                # remaining > 0 表示还能再展开一层。
                if child.is_dir() and remaining > 0:
                    extension = "    " if is_last else "|   "
                    _walk(child, remaining - 1, prefix + extension)

        # 根目录单独打印一行（无缩进），保持和原版一致的 "<name>" 格式。
        root_line = f"{resolved.name}/" if resolved.name else str(resolved)
        lines.append(root_line)
        # effective_depth=2 表示根 + 1 层子 = 2 层；根下的子目录还能再下 1 层。
        _walk(resolved, effective_depth - 1, "")

        if truncated and skipped > 0:
            lines.append(
                f"... and {skipped} more entries (use depth=N or smaller path)"
            )
        return "\n".join(lines) if len(lines) > 1 else "(empty)"

        if truncated and skipped > 0:
            lines.append(
                f"... and {skipped} more entries (use depth=N or smaller path)"
            )
        return "\n".join(lines) if len(lines) > 1 else "(empty)"
    except PermissionError:
        return f"[Error] permission denied: {path}"
    except Exception as e:
        return _format_error(e)


async def search_in_path(pattern: str, path: str = ".") -> str:
    from tool.builtin import _format_error

    try:
        base = _resolve_path(path)
        matches = sorted([str(p.relative_to(base)) for p in base.glob(pattern) if p.is_file()])
        if not matches: return f"[Info] no matches for {pattern}"
        return f"共 {len(matches)} matches:\n" + "\n".join(matches)
    except Exception as e:
        return _format_error(e)


async def search_in_content(pattern: str, path: str = ".", case_insensitive: bool = False,
        max_results: int = 20) -> str:
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
        for i, raw_line in enumerate(lines, 1):
            # 行级二进制过滤：NUL 字节或过多不可打印字符的整行视为二进制，跳过。
            # 这是对 `_is_text_file` 文件级判定的兜底——
            # 文本文件中偶发的二进制长行（拼装出来的脏数据）也会被截断。
            sanitized = _sanitize_binary_line(raw_line)
            if sanitized is None: continue
            if not re.search(pattern, sanitized, flags): continue
            snippet = sanitized.strip()
            if len(snippet) > 120: snippet = snippet[:120] + "..."
            results.append(f"  {file_path.name}:{i}: {snippet}")
            total += 1
            if total >= max_results: break
        if total >= max_results: break
    if not results: return f"[Info] no matches for {pattern}"
    suffix = f"\n... and {total - max_results} more" if total > max_results else ""
    header = f"search_in_content '{pattern}' ({total} matches):\n"
    return header + "\n".join(results[:max_results]) + suffix


async def get_file_metadata(path: str) -> str:
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
    "execute_command": execute_command,
    "read_file": read_file,
    "write_file": write_file,
    "get_directory_tree": get_directory_tree,
    "search_in_path": search_in_path,
    "search_in_content": search_in_content,
    "get_file_metadata": get_file_metadata,
}
