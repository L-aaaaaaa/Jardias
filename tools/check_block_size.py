"""tools/check_block_size.py — 查看各角色块2/块3 体积，判断是否需要块2 性能优化。"""
import json
from pathlib import Path

for f in sorted(Path('baseline').glob('after_*.json')):
    data = json.loads(f.read_text(encoding='utf-8'))
    char = data['character']
    b2 = data['block_sizes']['2']
    b3 = data['block_sizes']['3']
    print(f"{char:40s} 块2: {b2:>7d} bytes  块3: {b3:>7d} bytes")