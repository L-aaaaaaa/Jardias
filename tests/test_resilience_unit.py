"""Unit tests for provider resilience and ICP accounting."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from yinao.weaver import circuit_breaker, icp_tracker
from yinao.weaver.circuit_breaker import CircuitBreaker, is_exhausted_error
from yinao.weaver.icp_tracker import _usage_to_icp, update_cumulative


def test_circuit_breaker_opens_at_threshold_and_success_resets():
    breaker = CircuitBreaker(threshold=2, reset_after=60)

    breaker.record_failure("first")
    assert not breaker.is_open()
    breaker.record_failure("second")
    assert breaker.is_open()

    breaker.record_success()
    assert not breaker.is_open()
    assert breaker.to_dict()["failures"] == 0


def test_circuit_breaker_recovers_after_reset_window():
    breaker = CircuitBreaker(threshold=1, reset_after=5)
    breaker.record_failure("temporary")
    breaker._opened_at -= 6

    assert not breaker.is_open()
    assert breaker.to_dict()["available"] is True


@pytest.mark.parametrize("message", [
    "429 too many requests",
    "quota exceeded",
    "rate limit reached",
    "insufficient_quota",
])
def test_exhausted_error_matches_billing_and_rate_limit_messages(message):
    assert is_exhausted_error(RuntimeError(message))


def test_exhausted_error_checks_http_status():
    error = RuntimeError("server error")
    error.response = SimpleNamespace(status_code=429)

    assert is_exhausted_error(error)


def test_exhausted_error_does_not_match_generic_failure():
    assert not is_exhausted_error(TimeoutError("connection timeout"))


def test_usage_to_icp_maps_completion_reasoning_tokens():
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 8,
        "total_tokens": 18,
        "completion_tokens_details": {"reasoning_tokens": 5},
    }

    assert _usage_to_icp(usage) == {
        "prompt_icp": 10,
        "completion_icp": 8,
        "total_icp": 18,
        "thinking_icp": 5,
    }


def test_update_cumulative_tracks_usage_and_recent_latency(reset_global_state):
    update_cumulative({
        "prompt_tokens": 10,
        "completion_tokens": 8,
        "total_tokens": 18,
    }, "provider-a", 1.25)

    assert icp_tracker.cumulative_usage["total_icp"] == 18
    assert list(icp_tracker.provider_latency["provider-a"]) == [1.25]


def test_update_cumulative_latency_queue_is_bounded(reset_global_state):
    for value in range(10):
        update_cumulative(None, "provider-a", float(value))

    assert list(icp_tracker.provider_latency["provider-a"]) == [
        5.0, 6.0, 7.0, 8.0, 9.0,
    ]


def test_round_state_replaces_last_round_metadata():
    from yinao.weaver import round_state

    round_state.set_round_meta(
        1.5, {"total_tokens": 2}, finish_reason="length", error="oops")

    assert round_state.last_round.api_time == 1.5
    assert round_state.last_round.usage == {"total_tokens": 2}
    assert round_state.last_round.finish_reason == "length"
    assert round_state.last_round.error == "oops"


def test_round_state_can_be_reset_with_zero():
    from yinao.weaver import round_state

    round_state.set_round_meta(3.0, {"total_tokens": 7}, finish_reason="stop")
    round_state.set_round_meta(0.0)

    assert round_state.last_round.api_time == 0.0
    assert round_state.last_round.usage is None
    assert round_state.last_round.finish_reason is None
