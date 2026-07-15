"""io — experience.md 持久化层。

职责：按块的纯读写接口，不感知业务。
    - reader.py：按块读取 experience.md
    - writer.py：按块写入 + summary 合并（块2 字符串模板唯一存放位置）

调用方限制：
    - reader/writer 之间互相 import 是允许的（同层依赖）
    - 业务层（adapter/）只能通过暴露的 API 读写
    - 触发层不应直接 import；只能走 adapter

公开 API（通过子模块）：
    reader: read_block0/1/2/3, read_all, load_experience（兼容别名）
    writer: write_block0/1/2_append/2_rewrite/3, clear_block3
"""
from .reader import (
    read_block0, read_block1, read_block2, read_block3,
    read_all, load_experience,
)
from .writer import (
    write_block0, write_block1, write_block2_append, write_block2_rewrite,
    write_block3, clear_block3,
)

__all__ = [
    # reader
    'read_block0', 'read_block1', 'read_block2', 'read_block3',
    'read_all', 'load_experience',
    # writer
    'write_block0', 'write_block1',
    'write_block2_append', 'write_block2_rewrite',
    'write_block3', 'clear_block3',
]
