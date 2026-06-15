"""image — 图片检测与 vision 模型自动切换。"""
import base64
import mimetypes
import os
import re as _re_module

from actor_config import MODEL_NAMES, get_model_capabilities
from common.logger import logger

_IMG_EXTS = r"\.(?:png|jpg|jpeg|webp|gif|bmp)"
_IMG_EXT_END = r"(?:\?[^\s]*)?(?:\s|$)"


def detect_image_url(user_input: str) -> str | None:
    m = _re_module.search(
        rf'https?://[^\s]+{_IMG_EXTS}{_IMG_EXT_END}',
        user_input, _re_module.IGNORECASE
    )
    if not m:
        return None
    return m.group(0).rstrip()


def detect_local_image(user_input: str) -> str | None:
    m = _re_module.search(
        rf'[A-Za-z]:[\\/][^\s]+{_IMG_EXTS}{_IMG_EXT_END}'
        rf'|(?:(?:~|\.)?/[^\s]+){_IMG_EXTS}{_IMG_EXT_END}',
        user_input, _re_module.IGNORECASE
    )
    if not m:
        return None
    path = m.group(0).rstrip()
    if path.startswith("./") or path.startswith(".\\"):
        path = os.path.join(os.getcwd(), path[2:])
    elif path.startswith("~"):
        path = os.path.expanduser(path)
    return os.path.abspath(path)


def local_image_to_data_url(filepath: str) -> str | None:
    if not os.path.isfile(filepath):
        return None
    mime, _ = mimetypes.guess_type(filepath)
    if not mime or not mime.startswith("image/"):
        return None
    try:
        with open(filepath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


def find_vision_model() -> tuple[str, str] | None:
    for provider, models in MODEL_NAMES.items():
        for short, full in models.items():
            caps = get_model_capabilities(provider, short)
            if "vision" in caps:
                return provider, short
    return None


def auto_switch_for_vision(ctx, image_url: str) -> bool:
    """检测到图片但当前模型无 vision → 自动切换。返回是否发生了切换。"""
    my_caps = get_model_capabilities(ctx.config.runtime.provider, ctx.config.runtime.model)
    if "vision" in my_caps:
        return False

    target = find_vision_model()
    if not target:
        logger.warning("No vision-capable model available")
        return False

    from actor_config import save_config
    from common.actor_log import model_switch as log_model_switch

    t_prov, t_model = target
    old_prov, old_model = ctx.config.runtime.provider, ctx.config.runtime.model
    old_full = ctx.model_config.model

    ctx.config.runtime.provider = t_prov
    ctx.config.runtime.model = t_model
    save_config(ctx.config, ctx.character_name, config_dir=ctx.config_dir)

    from model_client.switch import reload_after_switch
    reload_after_switch(ctx)
    log_model_switch(old_prov, old_model, ctx.provider, ctx.model, reason="image detected → vision model")
    return True
