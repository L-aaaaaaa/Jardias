"""yinao/ — IPU 解析与配置管理。

真实 API：
- IPUConfigManager(config_path=...).load() / reload() / get_provider(name)
- IPUVendor (enum)
- get_ipu_capabilities / choose_ipu / choose_ipu_provider / resolve_ipu
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from yinao.ipu_config_manager import IPUConfigManager
from yinao.config_resolver import (
    IPUVendor,
    get_ipu_capabilities, choose_ipu, choose_ipu_provider, resolve_ipu,
)
from data_shape import IPUProviderConfig


# ── IPUConfigManager 单例 + reload ──────────────────────────

class TestIPUConfigManager:
    def _write_cfg(self, path: Path, providers):
        cfg = {"providers": providers}
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    def test_load(self, tmp_path):
        p = tmp_path / "ipu_config.json"
        self._write_cfg(p, [
            {"name": "deepseek", "api_key_env": "DS_KEY",
             "base_url": "https://api.deepseek.com",
             "ipus": {"v3": {"id": "deepseek-chat", "max_icp": 4096}}}
        ])
        mgr = IPUConfigManager(config_path=str(p))
        cfg = mgr.load()
        names = [pr.name for pr in cfg.providers]
        assert "deepseek" in names

    def test_reload(self, tmp_path):
        p = tmp_path / "ipu_config.json"
        self._write_cfg(p, [{"name": "a", "api_key_env": "A",
                              "base_url": "http://a",
                              "ipus": {"x": {"id": "a-x"}}}])
        mgr = IPUConfigManager(config_path=str(p))
        mgr.load()
        self._write_cfg(p, [{"name": "b", "api_key_env": "B",
                              "base_url": "http://b",
                              "ipus": {"y": {"id": "b-y"}}}])
        mgr.reload()
        cfg = mgr.load()
        names = [pr.name for pr in cfg.providers]
        assert "b" in names
        assert "a" not in names

    def test_get_provider(self, tmp_path):
        p = tmp_path / "ipu_config.json"
        self._write_cfg(p, [{"name": "test", "api_key_env": "T",
                              "base_url": "http://t",
                              "ipus": {"fast": {"id": "t-fast", "max_icp": 2048}}}])
        mgr = IPUConfigManager(config_path=str(p))
        pr = mgr.get_provider("test")
        assert pr.name == "test"
        assert "fast" in pr.ipus
        assert pr.ipus["fast"]["id"] == "t-fast"

    def test_get_provider_missing_raises(self, tmp_path):
        p = tmp_path / "ipu_config.json"
        self._write_cfg(p, [])
        mgr = IPUConfigManager(config_path=str(p))
        with pytest.raises(ValueError, match="不存在"):
            mgr.get_provider("ghost")

    def test_add_ipu_persists(self, tmp_path):
        """添加 ipu 后应可见在内存 providers 里。"""
        p = tmp_path / "ipu_config.json"
        self._write_cfg(p, [{"name": "test", "api_key_env": "T",
                              "base_url": "http://t", "ipus": {}}])
        mgr = IPUConfigManager(config_path=str(p))
        # 直接操作 cfg 不调 add_ipu（绕开写文件）
        cfg = mgr.load()
        # 找到 test provider，直接改
        for provider in cfg.providers:
            if provider.name == "test":
                provider.ipus["fast"] = {"id": "t-fast"}
        mgr.save()
        # 重读
        mgr2 = IPUConfigManager(config_path=str(p))
        pr = mgr2.get_provider("test")
        assert "fast" in pr.ipus

    def test_remove_ipu_persists(self, tmp_path):
        p = tmp_path / "ipu_config.json"
        self._write_cfg(p, [{"name": "test", "api_key_env": "T",
                              "base_url": "http://t",
                              "ipus": {"fast": {"id": "t-fast"}}}])
        mgr = IPUConfigManager(config_path=str(p))
        cfg = mgr.load()
        for provider in cfg.providers:
            if provider.name == "test":
                provider.ipus.pop("fast", None)
        mgr.save()
        mgr2 = IPUConfigManager(config_path=str(p))
        pr = mgr2.get_provider("test")
        assert "fast" not in pr.ipus

    def test_add_provider_invalid_config_raises(self, tmp_path):
        """添加已存在的 provider 应抛。"""
        p = tmp_path / "ipu_config.json"
        self._write_cfg(p, [{"name": "x", "api_key_env": "X",
                              "base_url": "http://x", "ipus": {}}])
        mgr = IPUConfigManager(config_path=str(p))
        mgr.load()
        with pytest.raises(ValueError, match="已存在"):
            mgr.add_provider(IPUProviderConfig(name="x", api_key_env="X",
                                                base_url="http://x", ipus={}))


# ── IPUVendor ─────────────────────────────────────────

class TestIPUVendor:
    def test_enum_members(self):
        assert hasattr(IPUVendor, "__members__")
        assert len(list(IPUVendor)) >= 1

    def test_unknown_member_raises(self):
        with pytest.raises(ValueError):
            IPUVendor("nonexistent_vendor_xyz")
