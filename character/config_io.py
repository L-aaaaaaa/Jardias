"""
config_io.py — actor 配置的 JSON 读写（数据在 character_data/ 目录下）
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from character import get_config_path, ensure_dirs
from data_shape import ActorConfig, RoleConfig, IPURuntime


# ── 序列化辅助（行为不归属 data_shape）──

def _dataclass_to_dict(obj) -> dict:
    return asdict(obj)


def _dataclass_from_dict(cls, d: dict):
    # 兼容旧字段名：
    #   身份：role → title, description → traits
    #   运行时（IPURuntime）：model → ipu, max_tokens → max_icp
    d = dict(d)
    _RENAME_MAP = {
        "role": "title",
        "description": "traits",
        "model": "ipu",
        "max_tokens": "max_icp",
    }
    for old, new in _RENAME_MAP.items():
        if old in d and new not in d:
            d[new] = d.pop(old)
    return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def config_to_dict(config: ActorConfig) -> dict:
    return {
        "identity": _dataclass_to_dict(config.identity),
        "runtime": _dataclass_to_dict(config.runtime),
    }


def config_from_dict(d: dict) -> ActorConfig:
    return ActorConfig(
        identity=_dataclass_from_dict(RoleConfig, d.get("identity", {})),
        runtime=_dataclass_from_dict(IPURuntime, d.get("runtime", {})),
    )


def get_config_path_legacy(actor_name: str, config_dir: str | None = None) -> Path:
    return Path(config_dir or "config") / f"{actor_name}.json"


def load_config(actor_name: str, config_dir: str | None = None) -> ActorConfig:
    """加载角色配置。"""
    path = get_config_path(actor_name)
    if not path.exists():
        legacy = get_config_path_legacy(actor_name, config_dir)
        if legacy.exists():
            _migrate_config(actor_name, legacy)
            path = get_config_path(actor_name)

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return config_from_dict(json.load(f))
        except (json.JSONDecodeError, KeyError):
            pass
    return ActorConfig()


def save_config(config: ActorConfig, actor_name: str, config_dir: str | None = None):
    """保存角色配置到 character_data/{name}/config.json。"""
    ensure_dirs(actor_name)
    path = get_config_path(actor_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config_to_dict(config), f, ensure_ascii=False, indent=2)


def init_config(actor_name: str, identity: dict | None = None, runtime: dict | None = None,
        config_dir: str | None = None):
    """初始化角色配置（仅当不存在时创建），返回 ActorConfig。"""
    path = get_config_path(actor_name)
    if path.exists():
        return load_config(actor_name, config_dir)
    config = ActorConfig(
        identity=RoleConfig(**(identity or {})),
        runtime=IPURuntime(**(runtime or {})),
    )
    save_config(config, actor_name, config_dir)
    return config


def _migrate_config(actor_name: str, legacy_path: Path):
    ensure_dirs(actor_name)
    new_path = get_config_path(actor_name)
    from shutil import copy2
    copy2(legacy_path, new_path)
    from common.logger import logger
    logger.info(f"  📁 配置迁移 | {legacy_path} → {new_path}")