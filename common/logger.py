"""统一日志配置模块。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from loguru import logger as _loguru_logger
    import loguru

    _LOGURU_AVAILABLE = True
except ImportError:
    _LOGURU_AVAILABLE = False
    _loguru_logger = None  # type: ignore[assignment]

_log_dir: Path | None = None


def _configure() -> None:
    global _log_dir
    if not _LOGURU_AVAILABLE:
        return
    _loguru_logger.remove()  # 移除默认的 sys.stderr 处理器（CLI 场景只用 stdout）

    # 终端输出：当 stdout 被重定向时自动去掉颜色码
    _is_redirected = not sys.stdout.isatty()
    _loguru_logger.add(
        sys.stdout,
        level=os.environ.get("LOGURU_LEVEL", "ERROR"),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "  # 绿色时间戳
            "<level>[{level.name}]</level> "  # 日志级别，颜色跟随等级
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> "  # 青色记录器名称+调用函数名
            "<level>{message}</level>"  # 日志内容主题，颜色跟随等级
        ),
        colorize=True,  # 颜色设置在终端生效
        backtrace=False,  # 是否显式异常是的完整调用栈
        diagnose=True,  # 在异常堆栈中附加每层调用的局部变量值
    )

    # ——— 文件输出（UTF-8，保留 7 天轮转）———
    _log_dir = Path("logs")
    _log_dir.mkdir(exist_ok=True)
    _loguru_logger.add(
        _log_dir / "log_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="7 days",
        compression="zip",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} [{level.name}] {name}:{function} - {message}",
        backtrace=False,
        diagnose=True,
    )


_configure()

if _LOGURU_AVAILABLE:
    logger = _loguru_logger
else:
    import logging as _stdlib_logging
    from logging.handlers import TimedRotatingFileHandler as _TRFH
    from pathlib import Path as _Path

    _LOG_DIR = _Path("logs")
    _LOG_DIR.mkdir(exist_ok=True)


    class _FallbackLogger:
        def __init__(self, name: str = ""):
            self._log = _stdlib_logging.getLogger(name)
            if not self._log.handlers:
                self._log.setLevel(_stdlib_logging.DEBUG)

                # stdout
                sh = _stdlib_logging.StreamHandler(sys.stdout)
                sh.setLevel(_stdlib_logging.INFO)
                sh.setFormatter(_stdlib_logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s:%(funcName)s - %(message)s",
                    datefmt="%H:%M:%S"
                ))
                self._log.addHandler(sh)

                # file — daily rotation
                fh = _TRFH(
                    str(_LOG_DIR / "log"),
                    when="midnight",
                    interval=1,
                    backupCount=7,
                    encoding="utf-8",
                )
                fh.suffix = "%Y-%m-%d.log"
                fh.setLevel(_stdlib_logging.DEBUG)
                fh.setFormatter(_stdlib_logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s:%(funcName)s - %(message)s",
                ))
                self._log.addHandler(fh)

        def debug(self, msg, *args, **kwargs): self._log.debug(msg, *args, **kwargs)

        def info(self, msg, *args, **kwargs): self._log.info(msg, *args, **kwargs)

        def warning(self, msg, *args, **kwargs): self._log.warning(msg, *args, **kwargs)

        def error(self, msg, *args, **kwargs): self._log.error(msg, *args, **kwargs)

        def critical(self, msg, *args, **kwargs): self._log.critical(msg, *args, **kwargs)

        def exception(self, msg, *args, **kwargs): self._log.exception(msg, *args, **kwargs)

        def bind(self, **kwargs): return self

        def catch(self, *args, **kwargs):
            def decorator(fn): return fn

            return decorator


    logger = _FallbackLogger()


def function_start():
    logger.info('▼')


def function_end():
    logger.info('▲')
