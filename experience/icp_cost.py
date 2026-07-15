"""icp_cost.py — 每轮运行状态 + 成本追踪模块。

提供 last_round 元数据和 round context 构建。
"""
from __future__ import annotations

from data_shape import RoundMeta


# ── 每轮元数据（_run_round 写入 → conversation_loop 读取）──

last_round: RoundMeta = RoundMeta()


def set_round_meta(elapsed: float, usage: dict | None = None,
        finish_reason: str | None = None, error: str | None = None):
    global last_round
    last_round = RoundMeta(
        api_time=elapsed, usage=usage, finish_reason=finish_reason, error=error)


# ── LLM 注入文本 ─────────────────────────────────────────────

def build_round_context(character_name: str | None = None) -> str:
    """从 RoundMeta + 累计数据构建注入上下文的元信息块（ICP 视角）。

    累计数据优先从 _dump_meta.json 读取（持久化，跨重启累计），
    未传 character_name 时退回到进程内 cumulative_usage（向后兼容）。
    """
    from yinao.weaver.icp_tracker import (
        cumulative_usage, provider_latency,
        _usage_to_icp, _load_cumulative, )

    parts: list[str] = ["# 状态"]

    # 1. ICP 用量（用户视角自然句，与终端本轮消耗同款措辞）
    if last_round.usage:
        icp = _usage_to_icp(last_round.usage)
        sentence = [f"**上轮消耗**: 本轮输入 {icp.get('prompt_icp', 0)} 智点"]
        reason_icp = icp.get("thinking_icp", 0)
        comp_icp = icp.get("completion_icp", 0)
        if reason_icp:
            sentence.append(f"输出 {reason_icp} 智点的思考，{comp_icp - reason_icp} 智点的回答")
        elif comp_icp:
            sentence.append(f"输出 {comp_icp} 智点的回答")
        if icp.get("total_icp"):
            sentence.append(f"合计 {icp['total_icp']} 智点")
        parts.append("，".join(sentence) + "。")

        cu = _load_cumulative(character_name) if character_name else cumulative_usage
        if cu.get("total_icp", 0) > 0:
            cu_sentence = [f"**累计消耗**: 累计输入 {cu.get('prompt_icp', 0)} 智点"]
            cu_reason = cu.get("thinking_icp", 0)
            cu_comp = cu.get("completion_icp", 0)
            if cu_reason:
                cu_sentence.append(f"含 {cu_reason} 智点的思考和 {cu_comp - cu_reason} 智点的回答")
            elif cu_comp:
                cu_sentence.append(f"含 {cu_comp} 智点的回答")
            cu_sentence.append(f"累计合计 {cu.get('total_icp', 0)} 智点")
            parts.append("，".join(cu_sentence) + "。")

    # 2. 截断通知
    if last_round.finish_reason == "length":
        parts.append(
            "⚠️ **上轮回复被截断**（达到 max_icp 限制）。"
            "你可能需要调用 update_runtime 放宽 max_icp，"
            "或在后续回复中更精简。")

    # 3. 错误通知
    if last_round.error: parts.append(f"⚠️ **上轮调用异常**: {last_round.error}")

    # 4. 延迟对比
    if len(provider_latency) > 1:
        lat_lines: list[str] = []
        for prov, q in sorted(provider_latency.items(),
                key=lambda x: sum(x[1]) / len(x[1]) if x[1] else 999):
            if q:
                avg = sum(q) / len(q)
                lat_lines.append(f"{prov} {avg:.1f}s")
        if lat_lines: parts.append("**各供应商延迟** (最近 {n} 轮均值): " + " / ".join(lat_lines))

    return "\n".join(parts)


__all__ = ['last_round', 'set_round_meta', 'build_round_context']
