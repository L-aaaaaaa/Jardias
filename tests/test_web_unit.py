"""Unit tests for tool/builtin_tools/web (web_fetch / web_search)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

# 重要：先 import tool.builtin 触发 _BUILTIN_HANDLERS 填充，
# 再 import tool.builtin_tools.web 避免循环。
from tool.builtin import ToolRegistry  # noqa: F401
from tool.builtin_tools import web as web_tool


def _async_iter(items):
    """构造一个 fake 的 streaming response（OpenAI 客户端返回迭代器）。"""
    async def gen():
        for x in items:
            yield x
    return gen()


class _FakeHTTPResponse:
    """最小可读 urlopen 返回对象（read + headers.get_content_charset）。"""

    def __init__(self, body: bytes = b"", charset: str | None = "utf-8"):
        self._body = body
        self._charset = charset

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @property
    def headers(self):
        class _Headers:
            def get_content_charset(self_inner):
                return self._charset
        return _Headers()


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ────────────────────────────────────────────────────────────────────
# web_fetch — 纯文本路径
# ────────────────────────────────────────────────────────────────────


def test_web_fetch_strips_script_style_and_tags():
    html = (
        "<html><head>"
        "<script>alert('x')</script>"
        "<style>body{}</style>"
        "</head><body>"
        "<h1>Title</h1><p>Hello, <b>world</b>!</p>"
        "</body></html>"
    )
    fake_resp = _FakeHTTPResponse(body=html.encode("utf-8"))
    with patch.object(web_tool, "urlopen", return_value=fake_resp):
        result = _run(web_tool.web_fetch({"url": "https://example.test/"}))

    assert "Title" in result
    assert "Hello" in result
    assert "world" in result
    assert "<script" not in result
    assert "<style" not in result
    assert "alert" not in result


def test_web_fetch_collapses_excess_whitespace():
    html = "<p>a</p>\n\n\n\n\n<p>b</p><p>c</p>"
    fake_resp = _FakeHTTPResponse(body=html.encode("utf-8"))
    with patch.object(web_tool, "urlopen", return_value=fake_resp):
        result = _run(web_tool.web_fetch({"url": "https://example.test/"}))

    # 多个连续换行被收敛到 2（即至多 \n\n）
    assert "\n\n\n" not in result
    assert "a" in result and "b" in result and "c" in result


def test_web_fetch_truncates_long_page():
    body = "x" * 5000
    fake_resp = _FakeHTTPResponse(body=body.encode("utf-8"))
    with patch.object(web_tool, "urlopen", return_value=fake_resp):
        result = _run(web_tool.web_fetch({"url": "https://example.test/"}))

    assert len(result) < 4500  # 截断到 4000 + 提示
    assert "截断" in result


def test_web_fetch_returns_placeholder_when_no_text():
    fake_resp = _FakeHTTPResponse(body=b"<html></html>".decode().encode())
    with patch.object(web_tool, "urlopen", return_value=fake_resp):
        result = _run(web_tool.web_fetch({"url": "https://example.test/"}))
    assert "无文字内容" in result


def test_web_fetch_returns_error_when_url_fails():
    with patch.object(web_tool, "urlopen", side_effect=ConnectionError("dns")):
        result = _run(web_tool.web_fetch({"url": "https://nope.test/"}))
    assert "[Error]" in result
    assert "ConnectionError" in result


def test_web_fetch_uses_charset_hint_from_headers():
    body = "<p>你好</p>".encode("utf-8")
    fake_resp = _FakeHTTPResponse(body=body, charset="utf-8")
    with patch.object(web_tool, "urlopen", return_value=fake_resp):
        result = _run(web_tool.web_fetch({"url": "https://example.test/"}))
    assert "你好" in result


# ────────────────────────────────────────────────────────────────────
# web_search — DuckDuckGo HTML 解析（mock HTML）
# ────────────────────────────────────────────────────────────────────


def _ddg_html(titles=None, snippets=None, links=None):
    """构造一段伪造的 DuckDuckGo HTML 结果。

    DuckDuckGo 真实结构：<a class="result__url">URL</a> + <h2 class="result__title"><a>Title</a></h2> +
    <a class="result__snippet">Snippet</a>。这里压缩成扁平结构以匹配代码中的正则。
    """
    titles = titles or ["Result One", "Result Two"]
    snippets = snippets or ["first <b>match</b>", "second hit"]
    links = links or ["https://one.test/", "https://two.test/"]
    parts = []
    for t, s, l in zip(titles, snippets, links):
        parts.append(
            f'<a class="result__url">{l}</a>'
            f'<h2 class="result__title"><a href="/url">{t}</a></h2>'
            f'<a class="result__snippet">{s}</a>'
        )
    return "<html><body>" + "\n".join(parts) + "</body></html>"


def test_web_search_parses_titles_snippets_and_links():
    fake_resp = _FakeHTTPResponse(body=_ddg_html().encode("utf-8"))
    with patch.object(web_tool, "urlopen", return_value=fake_resp):
        result = _run(web_tool.web_search({"query": "测试"}))

    assert "Result One" in result
    assert "Result Two" in result
    assert "first match" in result
    assert "https://one.test/" in result
    assert "搜索 \"测试\" (共 2 条)" in result


def test_web_search_respects_max_results():
    fake_resp = _FakeHTTPResponse(body=_ddg_html().encode("utf-8"))
    with patch.object(web_tool, "urlopen", return_value=fake_resp):
        result = _run(web_tool.web_search({"query": "x", "max_results": 1}))

    assert "Result One" in result
    assert "Result Two" not in result


def test_web_search_returns_empty_info_when_no_results():
    fake_resp = _FakeHTTPResponse(body=b"<html></html>")
    with patch.object(web_tool, "urlopen", return_value=fake_resp):
        result = _run(web_tool.web_search({"query": "noresult"}))
    assert "搜索结果为空" in result


def test_web_search_returns_error_when_request_fails():
    with patch.object(web_tool, "urlopen", side_effect=TimeoutError("slow")):
        result = _run(web_tool.web_search({"query": "x"}))
    assert "[Error]" in result
    assert "TimeoutError" in result
