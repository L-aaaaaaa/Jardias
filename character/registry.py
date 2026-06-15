"""
registry.py — 角色注册表，管理所有角色生命周期。
"""
from pathlib import Path

from data_shape import ActorConfig
from . import (
    ensure_dirs, list_characters, get_config_path, get_character_dir,
)


class CharacterRegistry:
    def scan(self) -> list[str]:
        return list_characters()

    def exists(self, name: str) -> bool:
        return name in self.scan()

    def create(self, name: str, config: ActorConfig):
        if self.exists(name):
            raise ValueError(f"角色 {name} 已存在")
        ensure_dirs(name)
        path = get_config_path(name)
        from actor_config.config_io import save_config
        save_config(config, name)

    def delete(self, name: str):
        if name == "default":
            raise ValueError("不能删除 default 角色")
        import shutil
        dir_path = get_character_dir(name)
        if dir_path.exists():
            shutil.rmtree(dir_path)

    def get_config(self, name: str) -> ActorConfig:
        from actor_config.config_io import config_from_dict
        import json
        path = get_config_path(name)
        if not path.exists():
            raise ValueError(f"角色 {name} 不存在")
        with open(path, encoding="utf-8") as f:
            return config_from_dict(json.load(f))

    def get_context_latest_path(self, name: str) -> Path:
        return get_character_dir(name) / "context_latest.md"


registry = CharacterRegistry()
