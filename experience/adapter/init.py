"""adapter/init.py — 触发原因：初始化（角色注册 / 引擎切换）。

职责：
    - on_register(character_name, config)：新角色创建时调用，写块0/1/2 骨架
    - on_ipu_switch(character_name, config)：auto-switch 或手动切引擎时调用，只同步块0

调用方：
    - character/registry.py 的注册入口
    - tool/builtin_tools/config.py:update_runtime（ipu 切换后）
    - common/lifecycle.py:auto-fallback 路径
"""
from __future__ import annotations


_DEFAULT_BLOCK1 = "# 状态\n\n（暂无状态数据）"


def _flatten_content(content) -> str:
    """将 message content 规范化为字符串（与原 practice.py:_flatten_content 行为一致）。"""
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content) if content else ""


def _render_block0(config, character_name: str) -> str:
    """渲染块0：复用 conversation.build_system_message（块0 == system 字符串）。"""
    from experience.adapter.conversation import build_system_message
    system_msg = build_system_message(config, character_name)
    return _flatten_content(system_msg["content"])


def _write_skeleton(character_name: str, block0: str) -> None:
    """写入空骨架（块0/1/2/3 各自独立，块2/3 留空）。"""
    from experience.io.writer import _write_experience_file, _resolve_path
    _write_experience_file(_resolve_path(character_name), {
        0: block0,
        1: _DEFAULT_BLOCK1,
        2: "",  # 留空，dump 时自动建骨架
        3: "",
    })


def on_register(character_name: str, config) -> None:
    """新角色注册：写块0（系统）+ 块1（状态占位）+ 块2（空骨架）。

    等价于旧的 experience.practice.init_experience。
    """
    block0 = _render_block0(config, character_name)
    _write_skeleton(character_name, block0)


def on_ipu_switch(character_name: str, config) -> None:
    """引擎切换：只同步块0 的 ## 引擎 段，不动块1/2/3。

    等价于旧的 experience.practice.sync_experience_system_block。
    如果 experience.md 还没创建（角色未初始化），跳过。
    """
    from experience.io.reader import read_all
    from experience.io.writer import write_block0

    try:
        blocks = read_all(character_name)
    except Exception:
        return
    if not blocks[0]:
        return

    write_block0(character_name, _render_block0(config, character_name))


__all__ = ["on_register", "on_ipu_switch"]