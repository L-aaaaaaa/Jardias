"""yinao/ipu_client/circuit_breaker.py — 熔断器状态机。

语义硬约束（防止回归的核心）：
- 连续失败 < threshold → 不熔断
- 连续失败 ≥ threshold → 熔断
- 熔断期内所有 is_open() 为 True
- 经过 reset_after 后下次 is_open() 应进入半开（下次失败才会重新熔断）
- 任意一次成功立刻重置
"""
from __future__ import annotations

import pytest

from yinao.ipu_client.circuit_breaker import (
    CircuitBreaker, EXHAUSTED_KEYWORDS, is_exhausted_error,
)


class TestCircuitBreakerStateMachine:
    def test_starts_closed(self):
        cb = CircuitBreaker(threshold=2, reset_after=300.0)
        assert cb.is_open() is False
        assert cb.reset_remaining() == 0.0

    def test_below_threshold_still_closed(self):
        cb = CircuitBreaker(threshold=3, reset_after=300.0)
        cb.record_failure("e1")
        cb.record_failure("e2")
        assert cb.is_open() is False

    def test_at_threshold_opens(self):
        cb = CircuitBreaker(threshold=3, reset_after=300.0)
        for i in range(3):
            cb.record_failure(f"e{i}")
        assert cb.is_open() is True

    def test_remain_positive_when_open(self):
        cb = CircuitBreaker(threshold=1, reset_after=300.0)
        cb.record_failure("boom")
        assert cb.is_open()
        r = cb.reset_remaining()
        assert 0.0 < r <= 300.0

    def test_success_closes_again(self):
        cb = CircuitBreaker(threshold=2, reset_after=300.0)
        cb.record_failure("a")
        cb.record_failure("b")
        assert cb.is_open()
        cb.record_success()
        assert cb.is_open() is False
        assert cb._failures == 0

    def test_half_open_after_timeout(self):
        """reset_after 后 is_open() 自动半开（返回 False），记录器清零。"""
        cb = CircuitBreaker(threshold=1, reset_after=10.0)
        cb.record_failure("a")
        assert cb.is_open()

        # 直接把 _opened_at 改成 11s 之前
        cb._opened_at -= 11.0
        assert cb.is_open() is False
        assert cb._failures == 0


class TestToDictSnapshot:
    def test_to_dict_clean(self):
        cb = CircuitBreaker()
        snap = cb.to_dict()
        assert snap == {"available": True, "failures": 0,
                        "reset_remaining_sec": 0, "last_error": ""}

    def test_to_dict_while_open(self):
        cb = CircuitBreaker(threshold=1, reset_after=200.0)
        cb.record_failure("rate-limited")
        snap = cb.to_dict()
        assert snap["available"] is False
        assert snap["failures"] == 1
        assert snap["last_error"] == "rate-limited"
        assert snap["reset_remaining_sec"] > 0

    def test_to_dict_truncates_long_error(self):
        cb = CircuitBreaker(threshold=1)
        cb.record_failure("x" * 500)
        snap = cb.to_dict()
        assert len(snap["last_error"]) <= 120


class TestExhaustedDetection:
    """is_exhausted_error：是 429/quota/rate-limit 还是别的 error（应该触发熔断）。"""

    @pytest.mark.parametrize("msg", [
        "Error 429 Too Many Requests",
        "quota exceeded for user",
        "rate limit reached",
        "out of quota",
        "billing exhausted",
        "insufficient_quota",
        "access to model denied",
        "AccessDenied for billing",
        "token plan not active",
        "model unpurchased on account",
    ])
    def test_keyword_match(self, msg):
        e = Exception(msg)
        assert is_exhausted_error(e) is True

    @pytest.mark.parametrize("msg", [
        "Connection timeout",
        "Internal Server Error",
        "Bad Request: missing field",
    ])
    def test_non_exhausted(self, msg):
        e = Exception(msg)
        assert is_exhausted_error(e) is False

    def test_http_status_attribute(self):
        """当异常有 .response.status_code == 429 → 视为耗尽。"""

        class FakeResp:
            status_code = 429

        class E(Exception):
            response = FakeResp()

        assert is_exhausted_error(E()) is True

    def test_http_403(self):
        class FakeResp:
            status_code = 403

        class E(Exception):
            response = FakeResp()

        assert is_exhausted_error(E()) is True

    def test_http_500_not_exhausted(self):
        class FakeResp:
            status_code = 500

        class E(Exception):
            response = FakeResp()

        assert is_exhausted_error(E()) is False


class TestExhaustedKeywordsFrozen:
    def test_set_is_frozen(self):
        """EXHAUSTED_KEYWORDS 必须是 frozenset（不可变），否则可能在某个地方被改坏。"""
        assert isinstance(EXHAUSTED_KEYWORDS, frozenset)
