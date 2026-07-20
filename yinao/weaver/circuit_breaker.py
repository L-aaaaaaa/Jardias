"""
circuit_breaker.py — 熔断器：单实例 + 跨 provider 共享字典。

- ``CircuitBreaker``：单 provider 的状态机（连续失败 → 熔断 → reset_after 秒后自愈）。
- ``is_exhausted_error``：判断错误是否属于耗尽/欠费类（这类错才触发熔断）。
- 模块级 ``_circuit_breakers``：跨 ipu 共享（同一 provider 的所有 ipu 共享一个熔断器）。
- ``record_ipu_*`` / ``is_provider_available`` / ``get_circuit_status``：面向调用的便捷 API。

线程不安全（Jardias 单线程运行），无需加锁。
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

# 耗尽/欠费的错误关键词（参考 Jardias0 yinao/model_strategy.py）
EXHAUSTED_KEYWORDS = frozenset((
    "429", "quota", "exhausted", "limit",
    "out of quota", "rate limit", "403",
    "insufficient_quota", "billing exhausted",
    "access to model denied", "accessdenied",
    "token plan", "unpurchased",))


class CircuitBreaker:
    """
    熔断器：连续失败 >= threshold 次 → 熔断，reset_after 秒后自动恢复。
    """

    def __init__(self, threshold: int = 2, reset_after: float = 300.0):
        self._threshold = threshold
        self._reset_after = reset_after
        self._failures: int = 0
        self._opened_at: float | None = None
        self._last_error: str = ""

    def record_success(self) -> None:
        """记录成功，重置计数器。"""
        self._failures = 0
        self._opened_at = None
        self._last_error = ""

    def record_failure(self, error_msg: str = "") -> None:
        """记录一次失败。达到阈值时自动熔断。"""
        self._failures += 1
        self._last_error = error_msg or self._last_error
        if self._failures >= self._threshold: self._opened_at = time.time()

    def is_open(self) -> bool:
        """当前是否已熔断（不可用）。"""
        if self._opened_at is None: return False
        elapsed = time.time() - self._opened_at
        if elapsed >= self._reset_after:
            self._opened_at = None  # 过了 reset_after，自动半开（允许下一次尝试）
            self._failures = 0
            return False
        return True

    def reset_remaining(self) -> float:
        """返回熔断剩余秒数（0 = 未熔断）。"""
        if self._opened_at is None: return 0.0
        remaining = self._reset_after - (time.time() - self._opened_at)
        return max(0.0, remaining)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": not self.is_open(),
            "failures": self._failures,
            "reset_remaining_sec": int(self.reset_remaining() or 0),
            "last_error": self._last_error[:120] if self._last_error else "", }


def is_exhausted_error(error: Exception) -> bool:
    """判断是否是资源耗尽/欠费类错误（需要熔断的不是普通超时）。"""
    # 检查 HTTP status code
    response = getattr(error, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status in (429, 403): return True
    # 检查错误消息关键词
    msg = str(error).lower()
    return any(kw.lower() in msg for kw in EXHAUSTED_KEYWORDS)


# ── 跨 ipu 共享：每 provider 一个熔断器 ──

_circuit_breakers: dict[str, CircuitBreaker] = defaultdict(CircuitBreaker)


def record_ipu_success(provider: str):
    """记录智能基元调用成功（重置熔断计数器）。"""
    _circuit_breakers[provider].record_success()


def record_ipu_failure(provider: str, error: Exception):
    """记录智能基元调用失败（累计，达到阈值后熔断）。"""
    cb = _circuit_breakers[provider]
    cb.record_failure(f"{type(error).__name__}: {error}")
    if cb.is_open():
        from common.logger import logger
        remaining = int(cb.reset_remaining() or 0)
        logger.warning(
            f"[CIRCUIT] 供应商 {provider} 已熔断（{cb._failures}次失败）— {remaining}s 后自动恢复")


def is_provider_available(provider: str) -> bool:
    """检查供应商是否可用（未被熔断）。"""
    cb = _circuit_breakers.get(provider)
    if cb is None: return True
    return not cb.is_open()


def get_circuit_status() -> dict:
    """获取所有供应商的熔断状态快照（LLM 友好格式）。"""
    from yinao.launcher import IPU_REGISTRY
    result = {}
    for provider in set(list(IPU_REGISTRY.keys()) + list(_circuit_breakers.keys())):
        cb = _circuit_breakers.get(provider)
        if cb and cb._failures > 0:
            result[provider] = cb.to_dict()
        else:
            result[provider] = {"available": True, "failures": 0, "reset_remaining_sec": 0, "last_error": ""}
    return result


__all__ = [
    'EXHAUSTED_KEYWORDS',
    'CircuitBreaker',
    'is_exhausted_error',
    'record_ipu_success', 'record_ipu_failure',
    'is_provider_available', 'get_circuit_status',
]