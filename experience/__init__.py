"""experience — 对话经验管理模块。

提供 experience.md 的读写、消息渲染、上下文构建等功能。
"""
from .reader import load_experience
from .writer import update_experience, _write_experience_file
from .formatter import (
    _render_single_message, _render_messages_to_recent_section,
    _choose_fence, _extract_pure_text, _count_recent_entries
)
from .practice import build_context_from_experience, init_experience, sync_experience_system_block
from .icp_cost import last_round, set_round_meta, build_round_context

__all__ = [
    # reader
    'load_experience',
    # writer
    'update_experience', '_write_experience_file',
    # formatter
    '_render_single_message', '_render_messages_to_recent_section',
    '_choose_fence', '_extract_pure_text', '_count_recent_entries',
    # practice
    'build_context_from_experience', 'init_experience', 'sync_experience_system_block',
    # icp_cost
    'last_round', 'set_round_meta', 'build_round_context',
]
