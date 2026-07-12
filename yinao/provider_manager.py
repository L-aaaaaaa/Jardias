"""
provider_manager.py — 智能基元注册中心：读写 providers.json + 热重载。

数据形状来自 data_shape.ipu：
  IPUEntry / IPUProviderConfig / IPUConfigFile

旧字段名兼容：providers.json 文件中仍允许使用 "models" 键（自动转为 "ipus"）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Any

from data_shape import IPUEntry, IPUProviderConfig, IPUConfigFile

_DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json")


def _coerce_ipu_entry(v: Any) -> dict:
    """字符串值 → {"id": str}，dict 原样"""
    if isinstance(v, str):
        return {"id": v}
    if isinstance(v, dict):
        return v
    raise ValueError(f"智能基元条目必须为字符串或字典，收到 {type(v)}")


def _coerce_provider_ipus(provider_data: dict) -> dict:
    """规范化 provider 的 ipus 字段（兼容旧 models 键）"""
    if "models" in provider_data and "ipus" not in provider_data:
        provider_data["ipus"] = provider_data.pop("models")
    if "ipus" in provider_data:
        provider_data["ipus"] = {
            k: _coerce_ipu_entry(v) for k, v in provider_data["ipus"].items()
        }
    return provider_data


def _ipu_entries(provider: IPUProviderConfig) -> Dict[str, IPUEntry]:
    """从 IPUProviderConfig 提取类型安全的 IPUEntry 映射"""
    return {k: IPUEntry(**v) for k, v in provider.ipus.items()}


class IPURegistry:
    def __init__(self, config_path: str | None = None):
        self.config_path = Path(config_path or _DEFAULT_CONFIG)
        self._lock = threading.Lock()
        self._providers: Optional[IPUConfigFile] = None

    def load(self) -> IPUConfigFile:
        """加载配置（首次从文件，后续返回缓存）"""
        if self._providers is not None:
            return self._providers
        with self._lock:
            if self._providers is not None:
                return self._providers
            if not self.config_path.exists():
                self._providers = IPUConfigFile()
                self._save_nolock(self._providers)
            else:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                if "providers" in data:
                    data["providers"] = [_coerce_provider_ipus(p) for p in data["providers"]]
                self._providers = IPUConfigFile(**data)
            return self._providers

    def reload(self) -> IPUConfigFile:
        """强制重新从文件读取"""
        with self._lock:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if "providers" in data:
                data["providers"] = [_coerce_provider_ipus(p) for p in data["providers"]]
            self._providers = IPUConfigFile(**data)
            return self._providers

    def _save_nolock(self, config: IPUConfigFile):
        self.config_path.write_text(
            json.dumps(config.model_dump(), indent=2),
            encoding="utf-8")

    def save(self):
        """持久化当前内存配置到文件"""
        with self._lock:
            if self._providers is not None:
                self._save_nolock(self._providers)

    # ---------- Provider 级别操作 ----------
    def add_provider(self, provider: IPUProviderConfig):
        with self._lock:
            cfg = self.load()
            if any(p.name == provider.name for p in cfg.providers):
                raise ValueError(f"供应商 {provider.name} 已存在")
            cfg.providers.append(provider)
            self._save_nolock(cfg)

    def remove_provider(self, name: str):
        with self._lock:
            cfg = self.load()
            cfg.providers = [p for p in cfg.providers if p.name != name]
            self._save_nolock(cfg)

    def get_provider(self, name: str) -> IPUProviderConfig:
        cfg = self.load()
        for p in cfg.providers:
            if p.name == name:
                return p
        raise ValueError(f"供应商 {name} 不存在")

    # ---------- 智能基元级别操作（归属于某个 provider）----------
    def add_ipu(self, provider_name: str, short_name: str, ipu_id: str, caps: list[str] | None = None):
        with self._lock:
            cfg = self.load()
            for p in cfg.providers:
                if p.name == provider_name:
                    if short_name in p.ipus:
                        raise ValueError(f"智能基元简称 {short_name} 已存在")
                    entry = {"id": ipu_id}
                    if caps:
                        entry["caps"] = caps
                    p.ipus[short_name] = entry
                    self._save_nolock(cfg)
                    return
            raise ValueError(f"供应商 {provider_name} 不存在")

    def remove_ipu(self, provider_name: str, short_name: str):
        with self._lock:
            cfg = self.load()
            for p in cfg.providers:
                if p.name == provider_name:
                    p.ipus.pop(short_name, None)
                    self._save_nolock(cfg)
                    return
            raise ValueError(f"供应商 {provider_name} 不存在")

    def update_ipu(self, provider_name: str, short_name: str, new_ipu_id: str, caps: list[str] | None = None):
        with self._lock:
            cfg = self.load()
            for p in cfg.providers:
                if p.name == provider_name:
                    if short_name not in p.ipus:
                        raise ValueError(f"智能基元简称 {short_name} 不存在")
                    entry = {"id": new_ipu_id}
                    existing = p.ipus[short_name]
                    existing_caps = existing.get("caps", []) if isinstance(existing, dict) else []
                    entry["caps"] = caps if caps is not None else existing_caps
                    p.ipus[short_name] = entry
                    self._save_nolock(cfg)
                    return
            raise ValueError(f"供应商 {provider_name} 不存在")

    def start_hot_reload(self, on_reload=None):
        """开启配置文件热重载（后台线程）。"""
        if getattr(self, '_hot_reload_running', False):
            return

        self._hot_reload_running = True
        self._on_reload = on_reload

        from watchfiles import watch
        from common.logger import logger

        def monitor():
            for _ in watch(self.config_path, watch_filter=None):
                time.sleep(0.5)
                try:
                    self.reload()
                    logger.info(f"[热重载] 配置文件已更新: {self.config_path}")
                    if self._on_reload:
                        self._on_reload()
                except Exception as e:
                    logger.info(f"[热重载] 重载失败: {e}")

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()


provider_manager = IPURegistry()
provider_manager.load()  # 确保文件存在并加载
