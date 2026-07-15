"""round_state.py — 每轮运行状态（weaver 层的运行时全局变量）。

存放位置说明：
    RoundMeta（领域模型）由 data_shape.py 定义。
    `last_round` 是 weaver 层（`thought_weaver.weave_thought` 和 `lifecycle.py:_run_round`）
    共享的运行时状态——每轮结束后写入，下一轮（构造上下文/处理异常）时读取。

    历史位置：原归 `experience/icp_cost.py`（承载了 build_round_context 业务逻辑）。
              阶段 3 重构后：
                  - build_round_context → `experience/adapter/state.py`
                  - last_round / set_round_meta → 本文件（weaver 运行时全局）
              因为它不是 IO，不是适配业务，而是**运行时的可写入全局**，
              归到 weaver 同包（与 icp_tracker 并列）最自然。
"""
from __future__ import annotations

from data_shape import RoundMeta


last_round: RoundMeta = RoundMeta()


def set_round_meta(elapsed: float, usage: dict | None = None,
        finish_reason: str | None = None, error: str | None = None):
    global last_round
    last_round = RoundMeta(
        api_time=elapsed, usage=usage, finish_reason=finish_reason, error=error)


__all__ = ['last_round', 'set_round_meta']
