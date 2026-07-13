"""builtin_tools/web — web_fetch / web_search 工具。"""
from __future__ import annotations

import re
from urllib.parse import quote
from urllib.request import Request, urlopen


async def web_fetch(arguments: dict) -> str:
    url = arguments["url"]
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Agent01/1.0)"})
        with urlopen(req, timeout=15) as resp:
            content = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = content.decode(charset, errors="replace")
    except Exception as e:
        return f"[Error] 获取失败: {type(e).__name__}: {e}"

    # 简单 HTML 剥离
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.S | re.I)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)

    max_chars = 4000
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n... (截断，原文共 {len(text)} 字)"
    return text.strip() or "(页面无文字内容)"


async def web_search(arguments: dict) -> str:
    query = arguments["query"]
    max_results = int(arguments.get("max_results", 5))

    search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    try:
        req = Request(search_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"[Error] 搜索失败: {type(e).__name__}: {e}"

    results = []
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    links = re.findall(r'class="result__url"[^>]*>(.*?)</a>', html, re.S)
    titles = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', html, re.S)

    for i in range(min(len(titles), len(snippets), max_results)):
        title = re.sub(r'<[^>]+>', '', titles[i]).strip()
        snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
        link = re.sub(r'<[^>]+>', '', links[i]).strip() if i < len(links) else ""
        results.append(f"{i + 1}. {title}\n   {snippet}\n   [{link}]")

    if not results: return f"[Info] 搜索结果为空。搜索词: {query}"
    return f"搜索 \"{query}\" (共 {len(results)} 条):\n\n" + "\n\n".join(results)


HANDLERS: dict[str, callable] = {
    "web_fetch": web_fetch,
    "web_search": web_search,
}
