"""builtin_tools/scheduler — 时策（定时任务）工具。

依赖 ``tool.builtin._scheduler``（由 bootstrap 注入），通过函数体内
``from tool.builtin import _scheduler`` 延迟访问，避免循环引用。
"""
from __future__ import annotations

import time


def schedule_add(arguments: dict) -> str:
    """时策工具：LLM 传入绝对时间戳列表，注册定时任务。"""
    from tool.builtin import _current_actor, _scheduler
    from schedule.strategies import wall_ms
    if _scheduler is None: return "[Error] 时策调度器未初始化"
    timestamps = arguments.get("timestamps", [])
    message = arguments.get("message", "")
    if not timestamps or not message: return "[Error] timestamps 和 message 为必填参数"
    now = wall_ms()
    valid = [t for t in timestamps if t >= now - 60_000]  # 过滤已过期（延迟 ≤ 60s 保留，让 missed handler 处理）
    if not valid: return "[Error] 所有时间戳均已过期超过 60 秒"
    job_id = _scheduler.add_recurring(
        name=f"时策-{message[:20]}", message=message,
        timestamps=valid, character_id=_current_actor, )
    dropped = len(timestamps) - len(valid)
    info = f"已注册 {len(valid)} 个时间点"
    if dropped: info += f"（{dropped} 个已过期被忽略）"
    t0 = valid[0];
    delay = (t0 - now) / 1000.0
    due_str = time.strftime("%H:%M:%S", time.localtime(t0 / 1000.0))
    return f"[OK] {info}\n  首次触发: {due_str}（{delay:.1f}秒后）\n  job_id: {job_id}"


def schedule_list() -> str:
    """列出所有活跃的时策任务。"""
    from tool.builtin import _scheduler
    if _scheduler is None: return "[OK] 时策调度器未初始化，无活跃任务"
    jobs = _scheduler.list_jobs()
    if not jobs: return "[OK] 无活跃的时策任务"
    header = f"共 {len(jobs)} 个活跃任务:"
    lines = [f"  [{j['job_id']}] {j['name']} | 已触发 {j['fired']}/{j['total']} | 剩余 {j['remaining']}" for j in jobs]
    return "\n".join([header] + lines)


def schedule_cancel(arguments: dict) -> str:
    """取消时策任务。"""
    from tool.builtin import _scheduler
    if _scheduler is None: return "[Error] 时策调度器未初始化"
    job_id = arguments.get("job_id", "")
    if not job_id: return "[Error] 需要 job_id 参数（可通过 shice_schedule_list 获取）"
    ok = _scheduler.remove_remaining(job_id)
    return f"[OK] 已取消任务 {job_id}" if ok else f"[Error] 任务 {job_id} 不存在或已结束"


HANDLERS: dict[str, callable] = {
    "shice_schedule_add": schedule_add,
    "shice_schedule_list": schedule_list,
    "shice_schedule_cancel": schedule_cancel,
}
