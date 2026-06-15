"""
model_context.py — 共享状态：模型切换 + 配置变更 + 每轮元数据
"""
from __future__ import annotations

from collections import deque

from actor_config import MODEL_NAMES


class ModelSwitched(Exception):
    """切换模型时抛出，携带 (provider, model) 供外层捕获并重建 client。"""

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        super().__init__(f"switch to {provider}/{model}")


from data_shape import ModelSwitch

# ── 模型切换共享状态 ──

switch_request: ModelSwitch | None = None

# 实际运行中的 provider/model（fallback 后可能与 config 文件不同）
_actual_provider: str = ""
_actual_model: str = ""

# 供应商熔断器（per-provider，记录连续故障，达到阈值后切断）
from .circuit_breaker import CircuitBreaker
from collections import defaultdict

_circuit_breakers: dict[str, CircuitBreaker] = defaultdict(CircuitBreaker)


def set_actual_model(provider: str, model: str):
    """记录当前实际运行的引擎（fallback/bootstrap 调用）。"""
    global _actual_provider, _actual_model
    _actual_provider = provider
    _actual_model = model


def get_actual_model() -> str:
    """获取当前实际运行的模型简称。"""
    return _actual_model


def record_model_success(provider: str):
    """记录引擎调用成功（重置熔断计数器）。"""
    _circuit_breakers[provider].record_success()


def record_model_failure(provider: str, error: Exception):
    """记录引擎调用失败（累计，达到阈值后熔断）。"""
    cb = _circuit_breakers[provider]
    cb.record_failure(f"{type(error).__name__}: {error}")
    if cb.is_open():
        from common.logger import logger
        remaining = int(cb.reset_remaining() or 0)
        logger.warning(
            f"[CIRCUIT] 供应商 {provider} 已熔断（{cb._failures}次失败）— {remaining}s 后自动恢复"
        )


def is_provider_available(provider: str) -> bool:
    """检查供应商是否可用（未被熔断）。"""
    cb = _circuit_breakers.get(provider)
    if cb is None:
        return True
    return not cb.is_open()


def get_circuit_status() -> dict:
    """获取所有供应商的熔断状态快照（LLM 友好格式）。"""
    from actor_config import MODEL_NAMES
    result = {}
    for provider in set(list(MODEL_NAMES.keys()) + list(_circuit_breakers.keys())):
        cb = _circuit_breakers.get(provider)
        if cb and cb._failures > 0:
            result[provider] = cb.to_dict()
        else:
            result[provider] = {"available": True, "failures": 0, "reset_remaining_sec": 0, "last_error": ""}
    return result


def request_switch(provider: str, model: str):
    """请求切换模型（写入共享状态）。"""
    global switch_request
    if provider not in MODEL_NAMES:
        raise ValueError(f"未知供应商: {provider}，可用: {list(MODEL_NAMES.keys())}")
    available = list(MODEL_NAMES[provider].keys())
    if model not in MODEL_NAMES[provider]:
        raise ValueError(f"未知模型: {model}，{provider} 可用: {available}")
    switch_request = ModelSwitch(provider=provider, model=model)


def pop_switch() -> ModelSwitch | None:
    """读取并清除切换请求。"""
    global switch_request
    req = switch_request
    switch_request = None
    return req


# ── 每轮元数据（_run_round 写入 → conversation_loop 读取）──

from data_shape import RoundMeta

last_round: RoundMeta = RoundMeta()


def set_round_meta(elapsed: float, usage: dict | None = None,
        finish_reason: str | None = None, error: str | None = None):
    global last_round
    last_round = RoundMeta(api_time=elapsed, usage=usage,
        finish_reason=finish_reason, error=error)


# ── 累计用量 ──

cumulative_usage: dict = {"prompt_tokens": 0, "completion_tokens": 0,
                          "total_tokens": 0, "reasoning_tokens": 0}
provider_latency: dict[str, deque] = {}  # 供应商 → 最近 N 轮耗时

_MAX_LATENCY_SAMPLES = 5


def update_cumulative(usage: dict | None, provider: str, elapsed: float):
    if usage:
        cumulative_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        cumulative_usage["completion_tokens"] += usage.get("completion_tokens", 0)
        cumulative_usage["total_tokens"] += usage.get("total_tokens", 0)
        details = usage.get("completion_tokens_details", {}) or {}
        cumulative_usage["reasoning_tokens"] += details.get("reasoning_tokens", 0)

    if provider not in provider_latency:
        provider_latency[provider] = deque(maxlen=_MAX_LATENCY_SAMPLES)
    provider_latency[provider].append(elapsed)


def build_round_context() -> str:
    """从 RoundMeta + 累计数据构建注入上下文的元信息块"""
    parts: list[str] = ["# 状态"]

    # 1. Token 用量
    if last_round.usage:
        u = last_round.usage
        tokens: list[str] = []
        if u.get("prompt_tokens"):
            tokens.append(f"输入 {u['prompt_tokens']}")
        details = u.get("completion_tokens_details", {}) or {}
        reason_tok = details.get("reasoning_tokens", 0)
        comp_tok = u.get("completion_tokens", 0)
        if reason_tok:
            tokens.append(f"思考 {reason_tok}")
            tokens.append(f"输出 {comp_tok - reason_tok}")
        elif comp_tok:
            tokens.append(f"输出 {comp_tok}")
        if u.get("total_tokens"):
            tokens.append(f"合计 {u['total_tokens']}")
        parts.append("**上轮消耗**: " + " · ".join(tokens))

        # 累计
        cu = cumulative_usage
        if cu["total_tokens"] > 0:
            cost = []
            cost.append(f"累计输入 {cu['prompt_tokens']}")
            if cu["reasoning_tokens"]:
                cost.append(f"累计思考 {cu['reasoning_tokens']}")
            cost.append(f"累计输出 {cu['completion_tokens']}")
            cost.append(f"累计合计 {cu['total_tokens']}")
            parts.append("**累计消耗**: " + " · ".join(cost))

    # 2. 截断通知
    if last_round.finish_reason == "length":
        parts.append(
            "⚠️ **上轮回复被截断**（达到 max_tokens 限制）。"
            "你可能需要调用 update_runtime 放宽 max_tokens，"
            "或在后续回复中更精简。"
        )

    # 3. 错误通知
    if last_round.error:
        parts.append(f"⚠️ **上轮调用异常**: {last_round.error}")

    # 4. 延迟对比
    if len(provider_latency) > 1:
        lat_lines: list[str] = []
        for prov, q in sorted(provider_latency.items(),
                key=lambda x: sum(x[1]) / len(x[1]) if x[1] else 999):
            if q:
                avg = sum(q) / len(q)
                lat_lines.append(f"{prov} {avg:.1f}s")
        if lat_lines:
            parts.append("**各供应商延迟** (最近 {n} 轮均值): " + " / ".join(lat_lines))

    return "\n".join(parts)


# ── 供应商工具函数 ──

def resolve_provider(model_name: str) -> str | None:
    """根据模型短名反向查 provider。"""
    for provider, models in MODEL_NAMES.items():
        if model_name in models:
            return provider
    if model_name in MODEL_NAMES:
        return model_name
    return None


def list_providers() -> list[str]:
    return list(MODEL_NAMES.keys())


def list_models(provider: str) -> list[str]:
    return list(MODEL_NAMES.get(provider, {}).keys())
