"""
ipu_context.py — 共享状态：智能基元切换 + 配置变更 + 每轮元数据
"""
from __future__ import annotations

from collections import deque

from yinao import IPU_REGISTRY


class IPUSwitched(Exception):
    """切换智能基元时抛出，携带 (provider, ipu) 供外层捕获并重建 client。"""

    def __init__(self, provider: str, ipu: str):
        self.provider = provider
        self.ipu = ipu
        super().__init__(f"switch to {provider}/{ipu}")


from data_shape import IPUSwitch

# ── 智能基元切换共享状态 ──

switch_request: IPUSwitch | None = None

# 实际运行中的 provider/ipu（fallback 后可能与 config 文件不同）
_actual_provider: str = ""
_actual_ipu: str = ""

# 供应商熔断器（per-provider，记录连续故障，达到阈值后切断）
from .circuit_breaker import CircuitBreaker
from collections import defaultdict

_circuit_breakers: dict[str, CircuitBreaker] = defaultdict(CircuitBreaker)


def set_active_ipu(provider: str, ipu: str):
    """记录当前实际运行的智能基元（fallback/bootstrap 调用）。"""
    global _actual_provider, _actual_ipu
    _actual_provider = provider
    _actual_ipu = ipu


def get_active_ipu() -> str:
    """获取当前实际运行的智能基元简称。"""
    return _actual_ipu


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
    from yinao import IPU_REGISTRY
    result = {}
    for provider in set(list(IPU_REGISTRY.keys()) + list(_circuit_breakers.keys())):
        cb = _circuit_breakers.get(provider)
        if cb and cb._failures > 0:
            result[provider] = cb.to_dict()
        else:
            result[provider] = {"available": True, "failures": 0, "reset_remaining_sec": 0, "last_error": ""}
    return result


def request_switch(provider: str, ipu: str):
    """请求切换智能基元（写入共享状态）。"""
    global switch_request
    if provider not in IPU_REGISTRY:
        raise ValueError(f"未知供应商: {provider}，可用: {list(IPU_REGISTRY.keys())}")
    available = list(IPU_REGISTRY[provider].keys())
    if ipu not in IPU_REGISTRY[provider]:
        raise ValueError(f"未知智能基元: {ipu}，{provider} 可用: {available}")
    switch_request = IPUSwitch(provider=provider, ipu=ipu)


def pop_switch() -> IPUSwitch | None:
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


# ── 累计用量（API 原字段仍是 prompt_tokens/completion_tokens/...；此处只做展示命名）──

cumulative_usage: dict = {"prompt_icp": 0, "completion_icp": 0,
                          "total_icp": 0, "thinking_icp": 0}
provider_latency: dict[str, deque] = {}

_MAX_LATENCY_SAMPLES = 5


def _usage_to_icp(usage: dict) -> dict:
    """把 API 返回的 tokens 字段换算成 ICP 视角键名（用于展示与累计）。"""
    if not usage:
        return {}
    details = usage.get("completion_tokens_details", {}) or {}
    return {
        "prompt_icp": usage.get("prompt_tokens", 0),
        "completion_icp": usage.get("completion_tokens", 0),
        "total_icp": usage.get("total_tokens", 0),
        "thinking_icp": details.get("reasoning_tokens", 0),
    }


def update_cumulative(usage: dict | None, provider: str, elapsed: float):
    if usage:
        icp = _usage_to_icp(usage)
        cumulative_usage["prompt_icp"] += icp["prompt_icp"]
        cumulative_usage["completion_icp"] += icp["completion_icp"]
        cumulative_usage["total_icp"] += icp["total_icp"]
        cumulative_usage["thinking_icp"] += icp["thinking_icp"]

    if provider not in provider_latency:
        provider_latency[provider] = deque(maxlen=_MAX_LATENCY_SAMPLES)
    provider_latency[provider].append(elapsed)


def build_round_context() -> str:
    """从 RoundMeta + 累计数据构建注入上下文的元信息块（ICP 视角）。"""
    parts: list[str] = ["# 状态"]

    # 1. ICP 用量
    if last_round.usage:
        icp = _usage_to_icp(last_round.usage)
        tokens: list[str] = []
        if icp.get("prompt_icp"):
            tokens.append(f"输入 {icp['prompt_icp']} ICP")
        reason_tok = icp.get("thinking_icp", 0)
        comp_tok = icp.get("completion_icp", 0)
        if reason_tok:
            tokens.append(f"思考 {reason_tok} ICP")
            tokens.append(f"输出 {comp_tok - reason_tok} ICP")
        elif comp_tok:
            tokens.append(f"输出 {comp_tok} ICP")
        if icp.get("total_icp"):
            tokens.append(f"合计 {icp['total_icp']} ICP")
        parts.append("**上轮消耗**: " + " · ".join(tokens))

        cu = cumulative_usage
        if cu["total_icp"] > 0:
            cost = []
            cost.append(f"累计输入 {cu['prompt_icp']} ICP")
            if cu["thinking_icp"]:
                cost.append(f"累计思考 {cu['thinking_icp']} ICP")
            cost.append(f"累计输出 {cu['completion_icp']} ICP")
            cost.append(f"累计合计 {cu['total_icp']} ICP")
            parts.append("**累计消耗**: " + " · ".join(cost))

    # 2. 截断通知
    if last_round.finish_reason == "length":
        parts.append(
            "⚠️ **上轮回复被截断**（达到 max_icp 限制）。"
            "你可能需要调用 update_runtime 放宽 max_icp，"
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

def resolve_ipu_provider(ipu_name: str) -> str | None:
    """根据智能基元短名反向查 provider。"""
    for provider, ipus in IPU_REGISTRY.items():
        if ipu_name in ipus:
            return provider
    if ipu_name in IPU_REGISTRY:
        return ipu_name
    return None


def list_ipu_providers() -> list[str]:
    return list(IPU_REGISTRY.keys())


def list_ipus(provider: str) -> list[str]:
    return list(IPU_REGISTRY.get(provider, {}).keys())