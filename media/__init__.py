"""media — 媒体文件处理。"""
from .image import (
    detect_image_url,
    detect_local_image,
    local_image_to_data_url,
    find_vision_ipu,
    auto_switch_for_vision,
)