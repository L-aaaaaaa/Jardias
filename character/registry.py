"""
registry.py — 角色注册表，管理所有角色生命周期。
"""
import json
from pathlib import Path

from data_shape import ActorConfig
from . import (
    ensure_dirs, list_characters, get_config_path, get_character_dir, get_history_path,
)


class CharacterRegistry:
    def scan(self) -> list[str]:
        return list_characters()

    def exists(self, name: str) -> bool:
        return name in self.scan()

    def create(self, name: str, config: ActorConfig):
        """创建角色并立即持久化所有文件骨架。

        之前只有 config.json 落盘，history.json 要等首次对话才创建，
        experience.md 要等首次 LLM 调用才写入。
        现在 create 后目录里所有文件全部可见，且 experience.md 是完整的 4 块骨架
        （块0=系统提示、块1=状态占位、块2=历史骨架、块3=等待用户输入），
        而不是占位符。
        """
        if self.exists(name):
            raise ValueError(f"角色 {name} 已存在")
        ensure_dirs(name)
        path = get_config_path(name)
        from character.config_io import save_config
        save_config(config, name)
        _ensure_skeleton_files(name)
        # 真正初始化 experience.md 骨架：与 experience.adapter.init.on_register 对齐，
        # 让首次 dump 不需要走 form_full_context 的兜底（send_to_character 等不经过兜底）
        from experience.adapter.init import on_register
        on_register(name, config)

    def delete(self, name: str):
        if name == "default":
            raise ValueError("不能删除 default 角色")
        import shutil
        dir_path = get_character_dir(name)
        if dir_path.exists():
            shutil.rmtree(dir_path)

    def get_config(self, name: str) -> ActorConfig:
        from character.config_io import config_from_dict
        import json
        path = get_config_path(name)
        if not path.exists():
            raise ValueError(f"角色 {name} 不存在")
        with open(path, encoding="utf-8") as f:
            return config_from_dict(json.load(f))

    def get_experience_path(self, name: str) -> Path:
        return get_character_dir(name) / "experience.md"


def _ensure_skeleton_files(name: str) -> None:
    """创建角色的空文件骨架，让「角色文档」创建后立即在磁盘上完整可见。

    - history.json  → JSON 数组 []（下游 json.load 直接可用）
    - experience.md → 占位说明
    - summaries/L1/  → 由 ensure_dirs 已创建
    """
    char_dir = get_character_dir(name)
    history_path = char_dir / "history.json"
    if not history_path.exists():
        history_path.write_text("[]", encoding="utf-8")
    context_md = char_dir / "experience.md"
    if not context_md.exists():
        context_md.write_text(
            f"<!-- 角色 {name} 的最新体验将在首次对话后写入 -->\n",
            encoding="utf-8",
        )


registry = CharacterRegistry()
