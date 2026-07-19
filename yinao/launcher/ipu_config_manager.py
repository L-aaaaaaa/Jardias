"""
读写 ipu_config.json + 热重载。

旧字段名兼容：ipu_config.json 文件中仍允许使用 "models" 键（自动转为 "ipus"）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable
from typing import Optional

from data_shape import IPUProviderConfig, IPUConfigFile, AddIPURequest, UpdateIPURequest, RemoveIPURequest

_DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ipu_config.json")


class IPUConfigManager:

    def __init__(self, config_path: str | None = None):
        self.config_path = Path(config_path or _DEFAULT_CONFIG)
        self._lock = threading.Lock()
        self._providers: Optional[IPUConfigFile] = None

    # ———————————————— 加载 / 持久化 ————————————————

    def load(self) -> IPUConfigFile:
        """加载配置（首次从文件，后续返回缓存）"""
        if self._providers is not None:
            return self._providers
        with self._lock:
            if self._providers is not None:
                return self._providers
            self._providers = self._load_uncached()
            return self._providers

    def reload(self) -> IPUConfigFile:
        """强制重新从文件读取"""
        with self._lock:
            self._providers = self._load_uncached()
            return self._providers

    def save(self) -> None:
        """持久化当前内存配置到文件"""
        with self._lock:
            if self._providers is None:
                raise RuntimeError("配置尚未加载，请先调用 load()")
            self._save_nolock(self._providers)

    # ———————————————— Provider 级别操作 ————————————————

    def add_provider(self, provider: IPUProviderConfig) -> None:
        def op(cfg: IPUConfigFile) -> None:
            if any(p.name == provider.name for p in cfg.providers):
                raise ValueError(f"供应商 {provider.name} 已存在")
            cfg.providers.append(provider)

        self._mutate(op)

    def remove_provider(self, name: str) -> None:
        def op(cfg: IPUConfigFile) -> None:
            cfg.providers = [p for p in cfg.providers if p.name != name]

        self._mutate(op)

    def get_provider(self, name: str) -> IPUProviderConfig:
        """纯读操作，无需加锁（load 已保证缓存一致性）"""
        return self._find_provider(self.load(), name)

    # —————————— 智能基元级别操作（归属于某个 provider）——————————

    def add_ipu(self, req: "AddIPURequest") -> None:
        def op(cfg: IPUConfigFile) -> None:
            p = self._find_provider(cfg, req.provider_name)
            if req.short_name in p.ipus:
                raise ValueError(f"智能基元简称 {req.short_name} 已存在")
            p.ipus[req.short_name] = self._make_entry(req.ipu_id, req.caps)

        self._mutate(op)

    def remove_ipu(self, req: "RemoveIPURequest") -> None:
        def op(cfg: IPUConfigFile) -> None:
            self._find_provider(cfg, req.provider_name).ipus.pop(req.short_name, None)

        self._mutate(op)

    def update_ipu(self, req: "UpdateIPURequest") -> None:
        def op(cfg: IPUConfigFile) -> None:
            p = self._find_provider(cfg, req.provider_name)
            if req.short_name not in p.ipus:
                raise ValueError(f"智能基元简称 {req.short_name} 不存在")
            existing = p.ipus[req.short_name]
            existing_caps = existing.get("caps", []) if isinstance(existing, dict) else []
            p.ipus[req.short_name] = self._make_entry(
                req.new_ipu_id, req.caps if req.caps is not None else existing_caps)

        self._mutate(op)

    # ———————————————— 热重载 ————————————————

    def start_hot_reload(self, on_reload: Optional[Callable[[], None]] = None) -> None:
        """开启配置文件热重载（后台线程）"""
        if getattr(self, '_hot_reload_running', False): return
        self._hot_reload_running = True
        self._on_reload = on_reload

        from watchfiles import watch
        from common.logger import logger

        def monitor() -> None:
            for _ in watch(self.config_path, watch_filter=None):
                time.sleep(0.5)
                try:
                    self.reload()
                    logger.info(f"[热重载] 配置文件已更新: {self.config_path}")
                    if self._on_reload: self._on_reload()
                except Exception as e:
                    logger.info(f"[热重载] 重载失败: {e}")

        threading.Thread(target=monitor, daemon=True).start()

    # —————————————— 内部辅助 ——————————————

    @staticmethod
    def _find_provider(cfg: IPUConfigFile, name: str) -> IPUProviderConfig:
        for p in cfg.providers:
            if p.name == name: return p
        raise ValueError(f"供应商 {name} 不存在")

    @staticmethod
    def _make_entry(ipu_id: str, caps: Optional[list[str]]) -> dict:
        entry = {"id": ipu_id}
        if caps is not None: entry["caps"] = caps
        return entry

    def _mutate(self, fn: Callable[[IPUConfigFile], None]) -> None:
        """锁内执行变更并自动持久化"""
        with self._lock:
            cfg = self._load_uncached()
            fn(cfg)
            self._save_nolock(cfg)
            self._providers = cfg

    def _load_uncached(self) -> IPUConfigFile:
        """纯加载，不触碰 self._providers（由调用方负责原子替换）"""
        if not self.config_path.exists():
            cfg = IPUConfigFile()
            self._save_nolock(cfg)
        else:
            cfg = IPUConfigFile(**self._read_raw())
        return cfg

    def _save_nolock(self, config: IPUConfigFile) -> None:
        self.config_path.write_text(json.dumps(config.model_dump(), indent=2), encoding="utf-8")

    # ———————— 原始数据规范化（在 Pydantic 校验之前执行）————————

    def _read_raw(self) -> dict:
        """读取并规范化配置文件原始数据"""
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        if "providers" in data:
            data["providers"] = [self._coerce_provider_ipus(p) for p in data["providers"]]
        return data

    @classmethod
    def _coerce_provider_ipus(cls, provider_data: dict) -> dict:
        """规范化 provider 的 ipus 字段（兼容旧 models 键）"""
        if "models" in provider_data and "ipus" not in provider_data:
            provider_data["ipus"] = provider_data.pop("models")
        if "ipus" in provider_data:
            provider_data["ipus"] = {k: cls._coerce_ipu_entry(v) for k, v in provider_data["ipus"].items()}
        return provider_data

    @staticmethod
    def _coerce_ipu_entry(v: Any) -> dict:
        """字符串值 → {"id": str}，dict 原样"""
        if isinstance(v, str): return {"id": v}
        if isinstance(v, dict): return v
        raise ValueError(f"智能基元条目必须为字符串或字典，收到 {type(v)}")

ipu_config_manager = IPUConfigManager()
ipu_config_manager.load()  # 确保文件存在并加载
