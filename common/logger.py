"""统一日志配置模块 — 基于标准 logging 实现。"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)

# 抑制 httpx / httpcore 的噪声日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _configure_root_logger() -> None:
    """仅配置一次：stdout（按 LOG_LEVEL 过滤，缺省 ERROR）+ 文件（全量 DEBUG，每日轮转 7 天）。

    文件命名：logs/log.YYYY-MM-DD.log（TimedRotatingFileHandler 自动加 suffix）。
    """
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG)

    stdout_level = logging.getLevelName(os.environ.get("LOG_LEVEL", "ERROR"))
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(stdout_level)
    sh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s:%(funcName)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(sh)

    fh = TimedRotatingFileHandler(
        str(_LOG_DIR / "log"),
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    fh.suffix = "%Y-%m-%d.log"
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s:%(funcName)s - %(message)s",
    ))
    root.addHandler(fh)


_configure_root_logger()

# 对外暴露：调用方代码用 logger.info / logger.warning / logger.exception 等
logger = logging.getLogger("jardias")


def function_start():
    logger.info('▼')


def function_end():
    logger.info('▲')
