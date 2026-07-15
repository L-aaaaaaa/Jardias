"""experience — 对话经验管理模块。

三层架构（重构后）：

    1. IO 层（io/）
       - reader.py / writer.py  按块的纯读写接口，不感知业务
       - 块2 字符串模板唯一存放位置

    2. 适配层（adapter/）
       - 按触发原因组织业务逻辑：init / conversation / archive_recall / state
       - 业务逻辑（如 summary 合并、covered 过滤、message 渲染）只允许在这里出现
       - 业务调用方不应越过 adapter 直接使用 io.*

    3. 触发层（common/ / character/ / tool/）
       - 知道"何时调用"适配层，不直接调 IO 层

公开 API：
    - load_experience：读 4 块（read_all 的别名，兼容旧调用）
    - build_context_from_experience：从经验构建 messages（来自 adapter.conversation）
    - on_user_input / on_inject_context / on_round_complete：对话触发
    - on_archive / on_recall：归档触发
    - on_register / on_ipu_switch：初始化触发
    - on_state_update / build_round_context：状态块触发（来自 adapter.state）

新增业务功能请走 adapter/，不要直接 import io.*。
"""
from .io import (
    load_experience, read_all, read_block0, read_block1,
    read_block2, read_block3,
    write_block0, write_block1, write_block2_append, write_block2_rewrite,
    write_block3, clear_block3,
)
from .adapter.conversation import (
    on_user_input, on_inject_context, form_full_context,
    on_round_complete, dump_experience,
    build_context_from_experience,
    _extract_pure_text, _render_single_message,
)
from .adapter.archive_recall import on_archive, on_recall
from .adapter.init import on_register as init_experience, on_ipu_switch as sync_experience_system_block
from .adapter.state import on_state_update, build_round_context

__all__ = [
    # IO（reader）
    'load_experience', 'read_all',
    'read_block0', 'read_block1', 'read_block2', 'read_block3',
    # IO（writer）
    'write_block0', 'write_block1',
    'write_block2_append', 'write_block2_rewrite',
    'write_block3', 'clear_block3',
    # 适配层：触发原因 = 对话
    'on_user_input', 'on_inject_context', 'form_full_context',
    'on_round_complete', 'dump_experience',
    'build_context_from_experience',
    '_extract_pure_text', '_render_single_message',
    # 适配层：触发原因 = 归档/召回
    'on_archive', 'on_recall',
    # 适配层：触发原因 = 初始化/引擎切换
    'init_experience', 'sync_experience_system_block',
    # 适配层：触发原因 = 状态块更新
    'on_state_update', 'build_round_context',
]  # fmt: skip
