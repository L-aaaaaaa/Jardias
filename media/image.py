"""image — 图片检测与视觉智能基元自动切换。"""
import base64
import mimetypes
import os
import re as _re_module

from common.logger import logger
from yinao import IPU_REGISTRY, get_ipu_capabilities

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
        rf'[A-Za-z]:[\\/].+?{_IMG_EXTS}{_IMG_EXT_END}'
        rf'|(?:(?:~|\.)?/.+?){_IMG_EXTS}{_IMG_EXT_END}',
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


def find_vision_ipu() -> tuple[str, str] | None:
    for provider, ipus in IPU_REGISTRY.items():
        for short, full in ipus.items():
            caps = get_ipu_capabilities(provider, short)
            if "vision" in caps:
                return provider, short
    return None


def auto_switch_for_vision(ctx, image_url: str) -> bool:
    """检测到图片但当前智能基元无 vision → 自动切换。返回是否发生了切换。"""
    my_caps = get_ipu_capabilities(ctx.config.runtime.provider, ctx.config.runtime.ipu)
    if "vision" in my_caps:
        return False

    target = find_vision_ipu()
    if not target:
        logger.warning("No vision-capable IPU available")
        return False

    from character.config_io import save_config
    from common.actor_log import model_switch as log_model_switch

    t_prov, t_ipu = target
    old_prov, old_ipu = ctx.config.runtime.provider, ctx.config.runtime.ipu
    old_full = ctx.ipu_config.ipu

    ctx.config.runtime.provider = t_prov
    ctx.config.runtime.ipu = t_ipu
    save_config(ctx.config, ctx.character_name, config_dir=ctx.config_dir)
    from experience.adapter.init import on_ipu_switch
    on_ipu_switch(ctx.character_name, ctx.config)
    from yinao.launcher import reload_after_switch, format_engine_switch_log
    reload_after_switch(ctx)
    new_full = ctx.ipu_config.ipu
    switch_log = format_engine_switch_log(
        old_prov, old_ipu, ctx.provider, ctx.ipu,
        old_full=old_full, new_full=new_full,
        reason="image detected → vision IPU")
    ctx.history.append_system(switch_log)
    ctx.history.save()
    log_model_switch(old_prov, old_ipu, ctx.provider, ctx.ipu, reason="image detected → vision IPU")
    return True
