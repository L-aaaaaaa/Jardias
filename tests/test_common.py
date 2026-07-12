"""common/ — 通用工具层：
- common.utils：终端输出 / 流式颜色 / 静默模式
- common.cli_style：分隔线
- common.actor_log：日志格式化（不读真端）
- common.context：上下文注入（不依赖网络的话仅测 helper）
"""
from __future__ import annotations

import io
import json
from datetime import datetime
from unittest.mock import patch

import pytest


# ── utils.py ────────────────────────────────────────────────────

class TestSeparatePrint:
    def test_no_silent(self, reset_display, capsys):
        from common import utils
        utils.set_silent(False)
        utils.separate_print("─", title="测试", length=20)
        out = capsys.readouterr().out
        assert "测试" in out

    def test_silent_skips(self, reset_display, capsys):
        from common import utils
        utils.set_silent(True)
        utils.separate_print("─", title="不应打印", length=20)
        assert capsys.readouterr().out == ""

    def test_end_marker(self, reset_display, capsys):
        from common import utils
        utils.set_silent(False)
        utils.separate_print(end=True, length=12)
        assert " -" in capsys.readouterr().out

    def test_empty_label(self, reset_display, capsys):
        from common import utils
        utils.set_silent(False)
        utils.separate_print("─", title="", length=10)
        # 仅分隔符，无标签
        out = capsys.readouterr().out
        assert "─" * 10 in out

    def test_display_name_prepends(self, reset_display, capsys):
        from common import utils
        utils.set_display_name("小明")
        utils.set_silent(False)
        utils.separate_print(title="回复", length=20)
        out = capsys.readouterr().out
        assert "【小明】回复" in out

    def test_thinking_title_adds_yellow(self, reset_display):
        """'思考' 标题应自动切到黄色流式色。"""
        from common import utils
        utils.set_silent(False)
        utils.separate_print(title="思考")
        assert utils._stream_color == "yellow"

    def test_reply_title_resets_color(self, reset_display):
        from common import utils
        utils.set_stream_color("yellow")
        utils.separate_print(title="回复")
        assert utils._stream_color in (None, "blue")


class TestStreamPrint:
    def test_skip_long_blank(self, reset_display, capsys):
        """长度 > 1 的纯空白应被跳过。"""
        from common import utils
        utils.set_silent(False)
        utils.stream_print("\n\n")
        assert capsys.readouterr().out == ""

    def test_keep_single_char(self, reset_display, capsys):
        from common import utils
        utils.set_silent(False)
        utils.stream_print(" ")
        out = capsys.readouterr().out
        # 单空格被保留
        assert " " in out

    def test_color_branch(self, reset_display, capsys):
        from common import utils
        utils.set_silent(False)
        with patch("common.utils._stream_color", "yellow"):
            utils.stream_print("hello")
        out = capsys.readouterr().out
        # 应该包含 ANSI 转义
        assert "\033[" in out

    def test_unicode_safe_fallback(self, reset_display, capsys):
        """非 utf-8 stdout 时不应抛异常，应有降级。"""
        from common import utils
        utils.set_silent(False)

        # 模拟 stdout 编码为 GBK，尝试打印一个 emoji
        bio = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="replace",
                               write_through=True)
        with patch.object(utils.sys, "stdout", bio):
            # 这里不应该抛 UnicodeEncodeError
            utils.stream_print("hi")
        # 关闭流
        bio.detach()


class TestSetDisplayName:
    def test_keeps_state(self, reset_display):
        from common import utils
        utils.set_display_name("a")
        assert utils._display_name == "a"

    def test_overwrite(self, reset_display):
        from common import utils
        utils.set_display_name("a")
        utils.set_display_name("b")
        assert utils._display_name == "b"


class TestSilentMode:
    def test_default_false(self, reset_display):
        from common import utils
        assert utils.get_silent() is False

    def test_set_toggle(self, reset_display):
        from common import utils
        utils.set_silent(True)
        assert utils.get_silent()
        utils.set_silent(False)
        assert not utils.get_silent()


# ── cli_style.py ────────────────────────────────────────────────

class TestSeparatorToTerminal:
    def test_default_output(self, capsys):
        from common.cli_style import separator_to_terminal
        separator_to_terminal("=", 20)
        out = capsys.readouterr().out
        # 应出现分隔符
        assert "=" in out

    def test_with_title(self, capsys):
        from common.cli_style import separator_to_terminal
        separator_to_terminal("─", 30, title="标题")
        out = capsys.readouterr().out
        assert "标题" in out


# ── actor_log.py — 测日志格式（不依赖真实 logger）─────────

class TestActorLogFormat:
    def test_format_api_ok_basic(self):
        from common.actor_log import format_api_ok
        s = format_api_ok(2.0, usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        assert "API OK" in s
        assert "2.0s" in s
        assert "输入 10 智点" in s
        assert "输出 5 智点" in s
        assert "合计 15 智点" in s

    def test_format_api_ok_with_reasoning(self):
        """completion_tokens_details.reasoning_tokens 应区分思考 / 回答。"""
        from common.actor_log import format_api_ok
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 80,
            "completion_tokens_details": {"reasoning_tokens": 50},
            "total_tokens": 180,
        }
        s = format_api_ok(1.0, usage)
        assert "思考 50 智点" in s
        assert "30 智点" in s  # 80 - 50 = 30 答复

    def test_format_api_ok_truncation_warning(self):
        from common.actor_log import format_api_ok
        s = format_api_ok(1.0, finish_reason="length")
        assert "截断" in s

    def test_format_api_ok_empty_usage(self):
        from common.actor_log import format_api_ok
        s = format_api_ok(1.5, None)
        assert "API OK" in s
        assert "1.5s" in s

    def test_format_round_usage_silent(self):
        from common import actor_log
        from common import utils
        utils.set_silent(True)
        try:
            s = actor_log.format_round_usage({"prompt_tokens": 0, "completion_tokens": 0})
            assert s == ""
        finally:
            utils.set_silent(False)

    def test_format_round_usage_basic(self):
        from common.actor_log import format_round_usage
        from common import utils
        utils.set_silent(False)
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "completion_tokens_details": {"reasoning_tokens": 30},
            "total_tokens": 150,
        }
        s = format_round_usage(usage)
        assert "100 智点" in s
        assert "思考" in s

    def test_turn_config_brief_no_thinking(self):
        """IPURuntime.thinking_enabled=False → 输出 No"""
        from common.actor_log import turn_config_brief
        from data_shape import IPURuntime

        rt = IPURuntime(thinking_enabled=False, thinking_mode="auto")
        # 不直接验证 stdout 内容（受 logger 状态影响），只验证它能调用
        try:
            turn_config_brief(rt)
        except Exception:
            pytest.fail("turn_config_brief raised unexpectedly")


# ── logger.py — 标准 logging 接口 ───────────────────────────────

class TestLoggerInterface:
    def test_logger_exists(self):
        from common.logger import logger
        assert logger is not None
        assert hasattr(logger, "info")
        assert hasattr(logger, "warning")
        assert hasattr(logger, "error")
        assert hasattr(logger, "debug")
        assert hasattr(logger, "exception")

    def test_no_loguru_import(self):
        """loguru 已经移除——确保没有残留符号。"""
        from common import logger as logger_mod
        assert not hasattr(logger_mod, "_loguru_logger")
        assert not hasattr(logger_mod, "_LOGURU_AVAILABLE")

    def test_function_markers(self):
        from common.logger import function_start, function_end
        # 仅验证可调用
        function_start()
        function_end()
