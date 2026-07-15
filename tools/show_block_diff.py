"""tools/show_block_diff.py — 把快照按块打印对比。"""
import json
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("用法: python tools/show_block_diff.py <snapshot.json>")
    sys.exit(1)

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
char = data["character"]
print(f"=== {char} ===")
for i in range(4):
    content = data["blocks"][str(i)] or ""
    size = data["block_sizes"][str(i)]
    print(f"\n--- 块{i} ({size} bytes) ---")
    if not content:
        print("  (空)")
    else:
        # 只打印前 600 字符避免刷屏
        if len(content) > 600:
            print(content[:600] + f"\n... (共 {len(content)} 字符，已截断)")
        else:
            print(content)