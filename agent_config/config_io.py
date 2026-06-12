"""
config_io.py — Agent 配置的 JSON 读写（数据在 character_data/ 目录下）
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from data_shape import AgentConfig, IdentityConfig, RuntimeConfig
from character import get_config_path, ensure_dirs


# ── 序列化辅助（行为不归属 data_shape）──

def _dataclass_to_dict(obj) -> dict:
    return asdict(obj)


def _dataclass_from_dict(cls, d: dict):
    # 兼容旧字段名：role → title, description → traits
    d = dict(d)
    for old, new in [("role", "title"), ("description", "traits")]:
        if old in d and new not in d:
            d[new] = d.pop(old)
    return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def config_to_dict(config: AgentConfig) -> dict:
    return {
        "identity": _dataclass_to_dict(config.identity),
        "runtime": _dataclass_to_dict(config.runtime),
    }


def config_from_dict(d: dict) -> AgentConfig:
    return AgentConfig(
        identity=_dataclass_from_dict(IdentityConfig, d.get("identity", {})),
        runtime=_dataclass_from_dict(RuntimeConfig, d.get("runtime", {})),
    )


def get_config_path_legacy(agent_name: str, config_dir: str | None = None) -> Path:
    return Path(config_dir or "config") / f"{agent_name}.json"


def load_config(agent_name: str, config_dir: str | None = None) -> AgentConfig:
    """加载角色配置。"""
    path = get_config_path(agent_name)
    if not path.exists():
        legacy = get_config_path_legacy(agent_name, config_dir)
        if legacy.exists():
            _migrate_config(agent_name, legacy)
            path = get_config_path(agent_name)

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return config_from_dict(json.load(f))
        except (json.JSONDecodeError, KeyError):
            pass
    return AgentConfig()


def save_config(config: AgentConfig, agent_name: str, config_dir: str | None = None):
    """保存角色配置到 character_data/{name}/config.json。"""
    ensure_dirs(agent_name)
    path = get_config_path(agent_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config_to_dict(config), f, ensure_ascii=False, indent=2)


def init_config(agent_name: str, identity: dict | None = None, runtime: dict | None = None, config_dir: str | None = None):
    """初始化角色配置（仅当不存在时创建），返回 AgentConfig。"""
    path = get_config_path(agent_name)
    if path.exists():
        return load_config(agent_name, config_dir)
    config = AgentConfig(
        identity=IdentityConfig(**(identity or {})),
        runtime=RuntimeConfig(**(runtime or {})),
    )
    save_config(config, agent_name, config_dir)
    return config


def _migrate_config(agent_name: str, legacy_path: Path):
    ensure_dirs(agent_name)
    new_path = get_config_path(agent_name)
    from shutil import copy2
    copy2(legacy_path, new_path)
    from common.logger import logger
    logger.info(f"  📁 配置迁移 | {legacy_path} → {new_path}")
