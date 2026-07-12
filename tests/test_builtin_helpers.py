"""tool/builtin.py 中提取的公共 helper 行为测试。

当前覆盖：
- _format_error：异常→[Error] 字符串的统一格式
"""
from __future__ import annotations

import pytest

from tool.builtin import _format_error


class TestFormatError:
    def test_format_with_simple_exception(self):
        """普通异常 → '[Error] TypeName: msg'。"""
        try:
            raise ValueError("oops")
        except ValueError as e:
            msg = _format_error(e)
        assert msg == "[Error] ValueError: oops"

    def test_format_with_chained_exception(self):
        """多类型异常都能格式化。"""
        for exc in (KeyError("k"), OSError("os"), RuntimeError("rt")):
            assert _format_error(exc).startswith("[Error] ")
            assert type(exc).__name__ in _format_error(exc)

    def test_format_with_empty_message(self):
        """msg 为空时仍能正确格式化（保留 TypeName + ': '）。"""
        exc = ValueError("")
        assert _format_error(exc) == "[Error] ValueError: "

    def test_format_with_multiline_message(self):
        """msg 含换行时仍能完整输出。"""
        exc = RuntimeError("line1\nline2")
        assert _format_error(exc) == "[Error] RuntimeError: line1\nline2"
