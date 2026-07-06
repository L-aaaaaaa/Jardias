"""
快速验证：测试时策合并逻辑。
用 Python 直接调用 TemporalScheduler，模拟 LLM 的行为：
1. 注册 10 个时间戳
2. 在第一个触发后，重新注册 9 个剩余时间戳
3. 验证合并后只触发 10 次（无重复）
"""
import asyncio
import sys
import os
import tempfile
import json
from pathlib import Path

# 将项目根目录加入路径
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from schedule.shice import TemporalScheduler
from schedule.strategies import wall_ms

async def main():
    # 用临时文件作为仓库
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    store_path = tmp.name
    tmp.close()

    fired = []

    async def on_fire(ctx):
        fired.append({
            "job_id": ctx.job_id,
            "idx": ctx.fire_index,
            "pos": ctx.fire_index + 1,
            "total": len(ctx.timestamps),
            "ts": ctx.timestamps[ctx.fire_index] if ctx.fire_index < len(ctx.timestamps) else -1,
            "skipped": list(ctx.skipped_indices),
        })
        print(f"  🔔 触发 #{ctx.fire_index + 1}/{len(ctx.timestamps)} "
              f"(job={ctx.job_id}, missed={ctx.skipped_indices})")

    scheduler = TemporalScheduler(store_path, on_job_fire=on_fire, concurrency=10)
    await scheduler.start()

    # 等待调度器启动
    await asyncio.sleep(0.5)

    now = wall_ms()
    # 注册 10 个时间戳，间隔 100ms
    base = now + 500  # 0.5s 后开始
    timestamps_1 = [base + i * 100 for i in range(10)]

    print(f"第 1 步：注册 10 个时间戳 @ {[hex(t) for t in timestamps_1]}")
    jid1 = scheduler.add_recurring(
        name="测试水果",
        message="水果",
        timestamps=timestamps_1,
        character_id="测试员",
    )
    print(f"  job_id = {jid1}, 共 {len(timestamps_1)} 个\n")

    # 等待前 2 个触发
    print("等待 2 个触发（约 0.2s）...")
    await asyncio.sleep(0.8)

    fired_before = len(fired)
    print(f"  已触发 {fired_before} 次\n")

    # 第 2 步：模拟 LLM 的行为——重新注册剩余 8 个时间戳
    remaining = [base + i * 100 for i in range(2, 10)]  # #3-#10
    print(f"第 2 步：模拟 LLM 重新注册剩余时间戳 @ {[hex(t) for t in remaining]}")
    jid2 = scheduler.add_recurring(
        name="水果（续）",
        message="水果",
        timestamps=remaining,
        character_id="测试员",
    )
    print(f"  返回的 job_id = {jid2}")
    print(f"  是否与原 job 相同: {jid2 == jid1}")
    print(f"  当前 fired = {len(fired)} 次\n")

    # 等待所有触发完成（最多 5s）
    print("等待剩余触发完成（最多 5s）...")
    await asyncio.sleep(5.0)

    print(f"\n{'='*50}")
    print(f"总共触发次数: {len(fired)} 次")
    print(f"期望次数: 10 次")
    print(f"结果: {'✅ PASS' if len(fired) == 10 else f'❌ FAIL (多了 {len(fired) - 10} 次)'}")
    if fired:
        print(f"\n触发详情:")
        for i, f in enumerate(fired):
            print(f"  #{i+1}: job={f['job_id']}, idx={f['idx']}, pos={f['pos']}, missed={f['skipped']}")

    scheduler.stop()

    # 清理
    os.unlink(store_path)
    return len(fired)


if __name__ == "__main__":
    count = asyncio.run(main())
    sys.exit(0 if count == 10 else 1)
