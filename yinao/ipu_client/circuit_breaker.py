"""
熔断器 — 供应商故障自动熔断，防止反复回切。
"""
from __future__ import annotations

import time
from typing import Any

# 耗尽/欠费的错误关键词（参考 Jarvis0 yinao/model_strategy.py）
EXHAUSTED_KEYWORDS = frozenset((
    "429", "quota", "exhausted", "limit",
    "out of quota", "rate limit", "403",
    "insufficient_quota", "billing exhausted",
    "access to model denied", "accessdenied",
    "token plan", "unpurchased",))


class CircuitBreaker:
    """
    熔断器：连续失败 >= threshold 次 → 熔断，reset_after 秒后自动恢复。

    线程不安全（Jardias 单线程运行），无需加锁。
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
