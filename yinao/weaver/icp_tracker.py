"""
icp_tracker.py — 智点（ICP）累计 + 各供应商延迟。

负责两件互相关联的事：
- ``cumulative_usage`` 进程内智点累计（按 prompt / completion / total / thinking）。
- ``provider_latency`` 每个供应商最近 N 轮的延迟队列，用于上下文里展示延迟对比。

公开 API：
- ``update_cumulative(usage, provider, elapsed)``：单轮调用结束后写入。
- ``_usage_to_icp(usage)``：把 API 返回的 tokens 字段重命名为 ICP 视角。
- ``_load_cumulative(character_name)``：从 ``character_data/<角色>/_dump_meta.json``
  读取持久化的累计值（跨重启累计）。
"""
from __future__ import annotations

import json
from collections import deque

from character import get_character_dir


# 进程内 ICP 累计字典（重启清零；持久化版在 _dump_meta.json）。
# 键名刻意用 `*_icp` 而非 `*_tokens`，因为这是给 LLM 看的展示层措辞。
cumulative_usage: dict = {
    "prompt_icp": 0, "completion_icp": 0, "total_icp": 0, "thinking_icp": 0}

# 各供应商最近 N 轮延迟（O(1) 追加，自动淘汰旧值）。
provider_latency: dict[str, deque] = {}

_MAX_LATENCY_SAMPLES = 5


def update_cumulative(usage: dict | None, provider: str, elapsed: float):
    """累加本轮 ICP（如果有 usage）+ 记录该 provider 的本轮延迟。"""
    if usage:
        icp = _usage_to_icp(usage)
        cumulative_usage["prompt_icp"] += icp["prompt_icp"]
        cumulative_usage["completion_icp"] += icp["completion_icp"]
        cumulative_usage["total_icp"] += icp["total_icp"]
        cumulative_usage["thinking_icp"] += icp["thinking_icp"]

    if provider not in provider_latency:
        provider_latency[provider] = deque(maxlen=_MAX_LATENCY_SAMPLES)
    provider_latency[provider].append(elapsed)


def _usage_to_icp(usage: dict) -> dict:
    """把 API 返回的 tokens 字段换算成 ICP 视角键名（用于展示与累计）。"""
    if not usage: return {}
    details = usage.get("completion_tokens_details", {}) or {}
    return {
        "prompt_icp": usage.get("prompt_tokens", 0),
        "completion_icp": usage.get("completion_tokens", 0),
        "total_icp": usage.get("total_tokens", 0),
        "thinking_icp": details.get("reasoning_tokens", 0), }


def _load_cumulative(character_name: str) -> dict:
    """从 _dump_meta.json 读取持久化的累计用量。异常返回零字典。"""
    try:
        meta_path = get_character_dir(character_name) / "_dump_meta.json"
        if not meta_path.exists():
            return {"prompt_icp": 0, "completion_icp": 0, "total_icp": 0, "thinking_icp": 0}
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return {
            "prompt_icp": meta.get("prompt_icp", 0),
            "completion_icp": meta.get("completion_icp", 0),
            "total_icp": meta.get("total_icp", 0),
            "thinking_icp": meta.get("thinking_icp", 0), }
    except Exception:
        return {"prompt_icp": 0, "completion_icp": 0, "total_icp": 0, "thinking_icp": 0}


__all__ = [
    'cumulative_usage', 'provider_latency',
    'update_cumulative', '_usage_to_icp', '_load_cumulative',
] 