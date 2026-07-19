"""Unit tests for image detection and localization helpers."""
from __future__ import annotations

import base64

from media.image import (
    detect_image_url,
    detect_local_image,
    local_image_to_data_url,
)


def test_detect_image_url_accepts_query_string_and_is_case_insensitive():
    assert detect_image_url("look https://example.test/photo.JPG?size=small now") == (
        "https://example.test/photo.JPG?size=small"
    )
    assert detect_image_url("no image here") is None


def test_detect_local_image_resolves_relative_paths(isolated_workspace):
    image = isolated_workspace / "photo.png"
    image.write_bytes(b"png")

    detected = detect_local_image("please inspect ./photo.png")
    assert detected == str(image.resolve())
    assert detect_local_image("nothing") is None


def test_local_image_to_data_url_encodes_existing_image(isolated_workspace):
    image = isolated_workspace / "photo.png"
    raw = b"not really a png, but test bytes"
    image.write_bytes(raw)

    data_url = local_image_to_data_url(str(image))
    assert data_url.startswith("data:image/png;base64,")
    assert base64.b64decode(data_url.rsplit(",", 1)[1]) == raw
    assert local_image_to_data_url(str(isolated_workspace / "missing.png")) is None


def test_i18n_switches_language_and_formats_values(reset_global_state):
    from common.i18n import get_lang, set_lang, t, toggle_lang

    assert get_lang() == "zh"
    assert "5" in t("invalid_choice", n=5)
    set_lang("en")
    assert get_lang() == "en"
    assert "Invalid" in t("invalid_choice", n=5)
    assert toggle_lang() == "zh"
