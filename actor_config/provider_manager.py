import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Any

import json5

from data_shape import ModelEntry, ProviderConfig, ConfigFile

_DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json5")


def _coerce_model_entry(v: Any) -> dict:
    """字符串值 → {"id": str}，dict 原样"""
    if isinstance(v, str):
        return {"id": v}
    if isinstance(v, dict):
        return v
    raise ValueError(f"模型条目必须为字符串或字典，收到 {type(v)}")


def _coerce_provider_models(provider_data: dict) -> dict:
    """规范化 provider 的 models 字段"""
    if "models" in provider_data:
        provider_data["models"] = {
            k: _coerce_model_entry(v) for k, v in provider_data["models"].items()
        }
    return provider_data


def _model_entries(provider: ProviderConfig) -> Dict[str, ModelEntry]:
    """从 ProviderConfig 提取类型安全的 ModelEntry 映射"""
    return {k: ModelEntry(**v) for k, v in provider.models.items()}


class ProviderManager:
    def __init__(self, config_path: str | None = None):
        self.config_path = Path(config_path or _DEFAULT_CONFIG)
        self._lock = threading.Lock()
        self._providers: Optional[ConfigFile] = None

    def load(self) -> ConfigFile:
        """加载配置（首次从文件，后续返回缓存）"""
        if self._providers is not None:
            return self._providers
        with self._lock:
            if self._providers is not None:
                return self._providers
            if not self.config_path.exists():
                # 默认空配置
                self._providers = ConfigFile()
                self._save_nolock(self._providers)
            else:
                data = json5.loads(self.config_path.read_text(encoding="utf-8"))
                if "providers" in data:
                    data["providers"] = [_coerce_provider_models(p) for p in data["providers"]]
                self._providers = ConfigFile(**data)
            return self._providers

    def reload(self) -> ConfigFile:
        """强制重新从文件读取"""
        with self._lock:
            data = json5.loads(self.config_path.read_text(encoding="utf-8"))
            if "providers" in data:
                data["providers"] = [_coerce_provider_models(p) for p in data["providers"]]
            self._providers = ConfigFile(**data)
            return self._providers

    def _save_nolock(self, config: ConfigFile):
        self.config_path.write_text(
            json.dumps(config.model_dump(), indent=2),
            encoding="utf-8")

    def save(self):
        """持久化当前内存配置到文件"""
        with self._lock:
            if self._providers is not None:
                self._save_nolock(self._providers)

    # ---------- Provider 级别操作 ----------
    def add_provider(self, provider: ProviderConfig):
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

    def get_provider(self, name: str) -> ProviderConfig:
        cfg = self.load()
        for p in cfg.providers:
            if p.name == name:
                return p
        raise ValueError(f"供应商 {name} 不存在")

    # ---------- 模型级别操作（归属于某个 provider） ----------
    def add_model(self, provider_name: str, short_name: str, model_id: str, caps: list[str] | None = None):
        with self._lock:
            cfg = self.load()
            for p in cfg.providers:
                if p.name == provider_name:
                    if short_name in p.models:
                        raise ValueError(f"模型简称 {short_name} 已存在")
                    entry = {"id": model_id}
                    if caps:
                        entry["caps"] = caps
                    p.models[short_name] = entry
                    self._save_nolock(cfg)
                    return
            raise ValueError(f"供应商 {provider_name} 不存在")

    def remove_model(self, provider_name: str, short_name: str):
        with self._lock:
            cfg = self.load()
            for p in cfg.providers:
                if p.name == provider_name:
                    p.models.pop(short_name, None)
                    self._save_nolock(cfg)
                    return
            raise ValueError(f"供应商 {provider_name} 不存在")

    def update_model(self, provider_name: str, short_name: str, new_model_id: str, caps: list[str] | None = None):
        with self._lock:
            cfg = self.load()
            for p in cfg.providers:
                if p.name == provider_name:
                    if short_name not in p.models:
                        raise ValueError(f"模型简称 {short_name} 不存在")
                    entry = {"id": new_model_id}
                    # 保留现有 caps 或更新
                    existing = p.models[short_name]
                    existing_caps = existing.get("caps", []) if isinstance(existing, dict) else []
                    entry["caps"] = caps if caps is not None else existing_caps
                    p.models[short_name] = entry
                    self._save_nolock(cfg)
                    return
            raise ValueError(f"供应商 {provider_name} 不存在")

    def start_hot_reload(self, on_reload=None):
        """
        开启配置文件热重载（后台线程）。
        on_reload: 可选回调函数，在重载成功后调用，无参数。
        """
        if getattr(self, '_hot_reload_running', False): return

        self._hot_reload_running = True
        self._on_reload = on_reload

        # 延迟导入，避免循环依赖
        from watchfiles import watch
        from common.logger import logger

        def monitor():
            # 只监听配置文件本身的变化，且不递归
            for _ in watch(self.config_path, watch_filter=None):
                time.sleep(0.5)  # 简单防抖：等 0.5 秒，确保文件完全写入
                try:
                    self.reload()
                    logger.info(f"[热重载] 配置文件已更新: {self.config_path}")
                    if self._on_reload:
                        self._on_reload()
                except Exception as e:
                    logger.info(f"[热重载] 重载失败: {e}")

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()


provider_manager = ProviderManager()
provider_manager.load()  # 确保文件存在并加载
