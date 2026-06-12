"""
character — 角色管理逻辑（数据在 character_data/ 下）

目录结构:
  character_data/{YYYYMMDDHHMM-name}/
    config.json       — AgentConfig（identity + runtime）
    history.json      — 对话历史（原始，不可删改）
    summaries/
      L1/             — L1 层摘要文件（每条一次压缩事件）
      L2.json         — L2 层摘要（10 条 L1 → 1 条 L2）
      L3.json         — L3 层摘要（10 条 L2 → 1 条 L3）

命名规则：文件夹名 = 创建时间戳 + 角色名
  例: 202605011252-小明
  default 角色无时间戳前缀（保留向后兼容）
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

# ── 角色数据根目录 — 逻辑与数据分离 ──
CHAR_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "character_data")

# 文件夹名格式: YYYYMMDDHHMM-角色名  或  纯角色名（default 兼容）
_DIR_NAME_RE = re.compile(r'^(\d{12})-(.+)$')


def _make_dir_name(name: str) -> str:
    """生成带时间戳的文件夹名。"""
    ts = datetime.now().strftime("%Y%m%d%H%M")
    return f"{ts}-{name}"


def get_display_name(folder_name: str) -> str:
    """从文件夹名提取显示名。'202605011252-小明' → '小明'，'default' → 'default'"""
    m = _DIR_NAME_RE.match(folder_name)
    return m.group(2) if m else folder_name


def _resolve_dir(name: str) -> str | None:
    """给定显示名，找到实际的文件夹名。返回 None 表示不存在。"""
    root = Path(CHAR_ROOT)
    if not root.exists():
        return None
    for d in root.iterdir():
        if not d.is_dir():
            continue
        dn = get_display_name(d.name)
        if dn == name:
            return d.name
    return None


def _resolve_dir_or_raise(name: str) -> str:
    dir_name = _resolve_dir(name)
    if dir_name is None:
        raise FileNotFoundError(f"角色 {name} 不存在")
    return dir_name


def get_character_dir(name: str) -> Path:
    """角色子文件夹路径。内部解析时间戳前缀。"""
    dir_name = _resolve_dir(name) or name
    return Path(CHAR_ROOT) / dir_name


def get_config_path(name: str) -> Path:
    """角色配置文件路径。"""
    return get_character_dir(name) / "config.json"


def get_history_path(name: str) -> Path:
    """角色对话历史路径。"""
    return get_character_dir(name) / "history.json"


def get_summaries_dir(name: str) -> Path:
    """L1 摘要目录路径。"""
    return get_character_dir(name) / "summaries" / "L1"


def get_l2_path(name: str) -> Path:
    """L2 摘要文件路径。"""
    return get_character_dir(name) / "summaries" / "L2.json"


def get_l3_path(name: str) -> Path:
    """L3 摘要文件路径。"""
    return get_character_dir(name) / "summaries" / "L3.json"


def ensure_dirs(name: str) -> Path:
    """创建并返回角色目录（含 summaries/L1）。如果角色不存在则用时间戳命名新目录。"""
    existing = _resolve_dir(name)
    if existing:
        char_dir = Path(CHAR_ROOT) / existing
    else:
        dir_name = _make_dir_name(name)
        char_dir = Path(CHAR_ROOT) / dir_name
    char_dir.mkdir(parents=True, exist_ok=True)
    (char_dir / "summaries" / "L1").mkdir(parents=True, exist_ok=True)
    return char_dir


def list_characters() -> list[str]:
    """列出所有已有角色（显示名）。"""
    root = Path(CHAR_ROOT)
    if not root.exists():
        return []
    return [get_display_name(d.name) for d in root.iterdir() if d.is_dir()]
