"""adapter/state.py — 触发原因：动态状态块（块1）的业务逻辑。

职责：
    - build_round_context(character_name)：从 RoundMeta + 累计数据构建块1 字符串
      （ICP 用量、截断通知、错误通知、延迟对比）

调用方：
    - common/lifecycle.py:_collect_round_meta（每轮结束后调用，结果传给下一轮 form_full_context）

为什么放在适配层：
    - 块1 内容是"展示规则"——什么 ICP 用量展示什么措辞、什么错误怎么提示——
      是业务决策，不是 IO。
    - writer 层只负责"写入字符串"，不关心字符串从哪来。
"""
from __future__ import annotations

from experience.io.writer import write_block1, read_all
from yinao.weaver.icp_tracker import (
    cumulative_usage, provider_latency,
    _usage_to_icp, _load_cumulative,
)
from yinao.weaver.round_state import last_round  # noqa: F401  旧导入保留兼容
import yinao.weaver.round_state as _round_state


def build_round_context(character_name: str | None = None) -> str:
    """从 RoundMeta + 累计数据构建块1 字符串（ICP 视角）。

    累计数据优先从 _dump_meta.json 读取（持久化，跨重启累计），
    未传 character_name 时退回到进程内 cumulative_usage（向后兼容）。

    last_round 始终通过模块属性访问（_round_state.last_round），
    避免 ``from X import Y`` 把 last_round 绑定到首次导入时的旧实例。
    """
    last_round = _round_state.last_round
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
            parts.append(f"**各供应商延迟** (最近 {len(provider_latency)} 轮均值): " + " / ".join(lat_lines))

    return "\n".join(parts)


def on_state_update(character_name: str, round_context: str) -> None:
    """块1 更新：每轮把 round_context 写入 experience.md 的块1。

    触发层（dump_experience）调用此函数而不是直接调 writer，
    确保"什么内容写块1"是 adapter 层职责，IO 层只负责"写"。

    行为：
        - 如果 round_context 为空或等于占位符 "# 状态"，跳过（不污染块1）
        - 如果块1 内容已经是 round_context，跳过（避免无意义写）
    """
    if not round_context or round_context.strip() == "# 状态":
        return

    blocks = read_all(character_name)
    if blocks[1] == round_context:
        return

    write_block1(character_name, round_context)


__all__ = ["build_round_context", "on_state_update"]