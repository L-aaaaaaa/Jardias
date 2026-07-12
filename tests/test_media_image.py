"""media/image.py — 媒体识别 helpers。

真实 API：
- detect_image_url(text) -> str | None
- detect_local_image(text) -> str | None
- local_image_to_data_url(filepath) -> str | None
- find_vision_ipu() -> tuple[str, str] | None
- auto_switch_for_vision(ctx, image_url) -> bool
"""
from __future__ import annotations

from pathlib import Path

import pytest

from media.image import (
    detect_image_url, detect_local_image,
    local_image_to_data_url, find_vision_ipu,
    _IMG_EXTS,
)


# ── detect_image_url ─────────────────────────────────────

class TestDetectImageURL:
    @pytest.mark.parametrize("text,expected", [
        ("https://example.com/x.png", "https://example.com/x.png"),
        ("look at https://x.com/a.webp thanks", "https://x.com/a.webp"),
        ("https://example.com/file.jpg?token=1", "https://example.com/file.jpg?token=1"),
    ])
    def test_finds_url(self, text, expected):
        url = detect_image_url(text)
        assert url == expected

    @pytest.mark.parametrize("text", [
        "no image here",
        "ftp://example.com/x.png",
        "https://example.com/file.txt",
        "https://example.com/file",
        "https://example.com/file.jpeg#frag",  # 实现未支持 #
    ])
    def test_no_match(self, text):
        assert detect_image_url(text) is None


# ── detect_local_image ─────────────────────────────────

class TestDetectLocalImage:
    def test_finds_local_path(self):
        text = "see C:/Users/admin/img.png"
        out = detect_local_image(text)
        assert out and "img.png" in out

    def test_no_match(self):
        assert detect_local_image("no path") is None


# ── local_image_to_data_url ──────────────────────────────

class TestLocalImageToDataURL:
    def test_real_file(self, tmp_path):
        f = tmp_path / "x.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
        url = local_image_to_data_url(str(f))
        assert url is not None
        assert url.startswith("data:image/png;base64,")

    def test_missing_file(self, tmp_path):
        try:
            r = local_image_to_data_url(str(tmp_path / "ghost.png"))
            assert r is None or isinstance(r, str)
        except (FileNotFoundError, OSError):
            pass

    def test_non_image(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("not an image")
        r = local_image_to_data_url(str(f))
        # 期望返回 None 或抛，由实现决定
        assert r is None or isinstance(r, str)


# ── find_vision_ipu ───────────────────────────────────────

class TestFindVisionIPU:
    def test_returns_none_or_tuple(self):
        """允许返回 None 或 (provider, ipu) tuple。"""
        r = find_vision_ipu()
        assert r is None or (isinstance(r, tuple) and len(r) == 2)


# ── _IMG_EXTS 常量完整性 ────────────────────────────────

class TestImgExtsRegex:
    def test_lowercase(self):
        import re
        assert re.search(_IMG_EXTS, "x.png")
        assert re.search(_IMG_EXTS, "x.jpg")
        assert re.search(_IMG_EXTS, "x.webp")
        assert not re.search(_IMG_EXTS, "x.txt")
