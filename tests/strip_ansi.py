"""
清理 Jardias 对话日志中的 ANSI 颜色码。

用法：
    python tests/strip_ansi.py logs/output_xxx.txt
    python tests/strip_ansi.py logs/*.txt --inplace
"""
import re
import sys
from pathlib import Path

# 强制 stdout 使用 UTF-8，避免 Windows GBK 终端打印中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 匹配所有 ANSI CSI 序列：ESC[...m 形式（含颜色、光标控制等）
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip(text: str) -> str:
    return _ANSI_RE.sub("", text)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    inplace = "--inplace" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--inplace"]

    for pattern in args:
        for path in sorted(Path().glob(pattern) if any(c in pattern for c in "*?[") else [Path(pattern)]):
            if not path.is_file():
                print(f"[跳过] {path} 不存在或不是文件")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            cleaned = strip(text)
            count = len(text) - len(cleaned)
            if inplace:
                path.write_text(cleaned, encoding="utf-8")
                print(f"[已写入] {path}（去除 {count} 字符）")
            else:
                sys.stdout.write(cleaned)


if __name__ == "__main__":
    main()