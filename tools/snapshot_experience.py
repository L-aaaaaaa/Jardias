"""tools/snapshot_experience.py — 把 experience.md 拍快照成 JSON。

用法：
    python tools/snapshot_experience.py <character_name> > before.json
    python tools/snapshot_experience.py <character_name> > after.json
    diff before.json after.json

目的：
    重构前后对比 experience.md 的 4 个块是否一致，作为回归基线。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def snapshot(character_name: str) -> dict:
    """读取 experience.md 的 4 个块 + 附带的 _dump_meta.json。"""
    from experience import load_experience
    from character import get_character_dir

    char_dir = get_character_dir(character_name)
    exp_path = char_dir / "experience.md"
    if not exp_path.exists():
        return {"error": "experience.md not found", "character": character_name}

    blocks = load_experience(character_name)
    out = {
        "character": character_name,
        "blocks": {i: blocks.get(i, "") for i in range(4)},
        "block_sizes": {i: len(blocks.get(i, "")) for i in range(4)},
    }

    meta_path = char_dir / "_dump_meta.json"
    if meta_path.exists():
        try:
            out["dump_meta"] = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            out["dump_meta_error"] = str(e)

    return out


def main():
    if len(sys.argv) < 2:
        print("用法: python tools/snapshot_experience.py <character_name> [--out path.json]", file=sys.stderr)
        sys.exit(1)
    out_path = None
    args = sys.argv[1:]
    if "--out" in args:
        idx = args.index("--out")
        out_path = Path(args[idx + 1])
        args = args[:idx] + args[idx + 2:]
    character_name = args[0]
    data = snapshot(character_name)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if out_path:
        out_path.write_text(text, encoding="utf-8")
        print(f"已写入 {out_path}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()