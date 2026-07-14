"""项目测试 conftest。

职责：
- 把项目根目录加入 sys.path，使 `import character` / `import yinao` 等可直接解析。
- 提供常用 fixture：临时 character 目录、隔离日志目录、模块全局可变状态复位。
- 抑制 jupyter / ipython 类的无关警告。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ——— 把项目根加入 import path ———
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 把 logs/ 也写到临时目录，避免污染（导入 common.logger 时它会立刻创建 logs/）
import tempfile  # noqa: E402

_TMP_LOG_DIR = Path(tempfile.mkdtemp(prefix="jardias_logs_"))
os.environ["_JARDIAS_TEST_LOG_DIR_OVERRIDE"] = str(_TMP_LOG_DIR)


# ——— Fixture：临时工作目录，隔离 character_data / schedule ———
@pytest.fixture
def tmp_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时工作目录 + 重新指向 character.CHAR_ROOT，隔离已有角色数据。"""
    char_root = tmp_path / "character_data"
    char_root.mkdir()
    monkeypatch.chdir(tmp_path)

    # character 模块的 CHAR_ROOT 是模块级常量，必须 monkeypatch
    from character import CHAR_ROOT as _CHAR_ROOT_REAL
    monkeypatch.setattr("character.CHAR_ROOT", str(char_root))

    # 同时修补其它可能缓存了绝对路径的模块
    from character import ensure_dirs, list_characters, get_character_dir
    monkeypatch.setattr("character.ensure_dirs", ensure_dirs)
    monkeypatch.setattr("character.list_characters", list_characters)

    return tmp_path


@pytest.fixture
def temp_character(tmp_workdir: Path) -> Path:
    """返回 ``character_data/{name}`` 路径，含 summaries/L1 子目录。"""
    from character import ensure_dirs

    char_dir = ensure_dirs("alice")
    return char_dir


# ——— Fixture：复位全局可变状态 ———
@pytest.fixture
def reset_actor() -> None:
    """复位 tool.builtin._current_actor。"""
    from tool import builtin

    builtin._current_actor = "default"
    builtin.clear_pending_switch()


@pytest.fixture
def reset_display() -> None:
    """复位 common.cli_output 的全局显示名 / 静默 / 流色。"""
    from common import cli_output

    cli_output.set_display_name("")
    cli_output.set_silent(False)
    cli_output.set_stream_color(None)


@pytest.fixture
def reset_circuit_breakers() -> None:
    """复位 yinao.ipu_client 中的熔断器 + ipu_context/icp_tracker 全局状态。"""
    from yinao.ipu_client import circuit_breaker, icp_tracker, ipu_switch

    circuit_breaker._circuit_breakers.clear()
    ipu_switch.switch_request = None
    ipu_switch._actual_provider = ""
    ipu_switch._actual_ipu = ""
    icp_tracker.cumulative_usage.update({"prompt_icp": 0, "completion_icp": 0,
                                          "total_icp": 0, "thinking_icp": 0})
    icp_tracker.provider_latency.clear()


@pytest.fixture
def reset_log_levels() -> None:
    """把 common.logger 的 stdout handler 调到 WARNING，减少测试输出噪音。"""
    import logging

    for h in logging.getLogger().handlers:
        if hasattr(h, "stream") and h.stream in (sys.stdout, sys.stderr):
            h.setLevel(logging.WARNING)
    yield
    # 测试结束恢复：避免影响其他模块的日志级别预期
    for h in logging.getLogger().handlers:
        if hasattr(h, "stream") and h.stream in (sys.stdout, sys.stderr):
            h.setLevel(logging.ERROR)
