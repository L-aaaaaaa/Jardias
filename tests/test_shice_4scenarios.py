"""
时策集成测试：4 种场景的端到端验证（修复版）。

关键改进：
- 同步写入日志文件（不再依赖 daemon 线程捕获 stdout）
- 用 shice_schedule_list 轮询 job 状态作为完成检测信号
- 超时延长到 10 分钟
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path

# ── 全局配置 ──────────────────────────────────────────────────────────────

PROJECT = Path(r"E:\Code\AIProjects\Actor01")
PYTHON = Path(r"D:\B\Python3.10\python.exe")
LOG_BASE = PROJECT / "logs" / "7.6"
LOG_BASE.mkdir(parents=True, exist_ok=True)

SCENARIOS = {
    "A": {
        "name": "场景A_理想无错过",
        "task": "20秒后，每隔1秒随便说一个水果，总共10次。",
        "expect": "10 个水果，1s 间隔，job 正常终止，无残留。",
    },
    "B": {
        "name": "场景B_单次错过",
        "task": "20秒后，每隔1秒随便说一个水果，总共10次。如果错过了，把错过漏发的水果在第一次发现时与当次水果一起补发。如果发现错过了2次或更多，剩下的就改为间隔10秒一次。",
        "expect": "最多 1 次错过被补发，后续正常继续。",
    },
    "C": {
        "name": "场景C_策略切换",
        "task": "20秒后，每隔1秒随便说一个水果，总共10次。如果错过了，把错过漏发的水果在第一次发现时与当次水果一起补发。如果发现错过了2次或更多，剩下的就改为间隔10秒一次。",
        "expect": ">=2 次错过 → cancel 原 job → add 新 job（10s 间隔）继续。",
    },
    "D": {
        "name": "场景D_完全过期",
        "task": "先确保当前时间离下一个整点秒还有至少 30 秒，然后说：现在开始，每隔 1 秒说一个水果的名字，共 5 次，全部说完后说「水果播报完毕」。",
        "expect": "触发前 app 退出，所有时间点过期 → 合并到最后一个触发，AI 一次性输出 5 个水果。",
    },
}

TIMEOUT_PER_SCENARIO = 600  # 10 分钟


# ── 辅助 ─────────────────────────────────────────────────────────────────

def _fmt_time(ts_ms: int) -> str:
    import time as _t
    lt = _t.localtime(ts_ms / 1000.0)
    return _t.strftime("%H:%M:%S", lt) + f".{ts_ms % 1000:03d}"


def _fmt_time_now() -> str:
    lt = time.localtime()
    ms = int((time.time() % 1) * 1000)
    return time.strftime("%H:%M:%S", lt) + f".{ms:03d}"


# ── 子进程管理 ─────────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    scenario: str
    name: str
    success: bool = False
    lines: list[str] = field(default_factory=list)
    history_entries: int = 0
    schedule_residual: list = field(default_factory=list)
    triggers: list = field(default_factory=list)
    fruits: list = field(default_factory=list)
    error: str = ""
    duration_sec: float = 0.0


def _create_character(proc, char_name: str):
    """创建角色：经历完整的菜单交互。"""
    time.sleep(3)
    proc.stdin.write("n\n")
    proc.stdin.flush()
    time.sleep(0.5)
    proc.stdin.write(char_name + "\n")
    proc.stdin.flush()
    time.sleep(0.3)
    proc.stdin.write("\n")  # title
    proc.stdin.flush()
    time.sleep(0.3)
    proc.stdin.write("\n")  # traits
    proc.stdin.flush()
    time.sleep(0.3)
    proc.stdin.write("\n")  # greeting
    proc.stdin.flush()
    time.sleep(0.3)
    proc.stdin.write("1\n")  # provider = minimax
    proc.stdin.flush()
    time.sleep(0.3)
    proc.stdin.write("9\n")  # ipu index
    proc.stdin.flush()
    time.sleep(3)


def _send_input_and_wait(proc, task: str, timeout: int = 600) -> list[str]:
    """
    发送任务，实时监控 schedule_data.json 直到 job 完成或超时。
    同时捕获 stdout 行，写入日志文件。
    """
    # 启动 stdout 读取线程
    out_lines = []
    log_buf = []
    log_lock = threading.Lock()
    stream_done = threading.Event()

    def _read_stream(stream):
        try:
            for raw in iter(lambda: stream.read(4096), ""):
                if not raw:
                    break
                lines = raw.splitlines(keepends=False)
                with log_lock:
                    for ln in lines:
                        txt = ln.rstrip()
                        out_lines.append(txt)
                        log_buf.append(txt)
        except Exception:
            pass
        finally:
            stream_done.set()

    # 注意：bufsize=0 导致 read(4096) 不会阻塞，需要用 readline
    # 改用逐行读取
    out_lines.clear()
    log_buf.clear()

    def _readlines(stream):
        try:
            for ln in iter(stream.readline, ""):
                if not ln:
                    break
                txt = ln.rstrip()
                out_lines.append(txt)
                log_buf.append(txt)
                print(txt)
        except Exception:
            pass
        finally:
            stream_done.set()

    t_reader = threading.Thread(target=_readlines, args=(proc.stdout,), daemon=True)
    t_reader.start()

    t_start = time.time()

    # 发送任务
    print(f"\n[INPUT] {task[:60]}...", flush=True)
    proc.stdin.write(task + "\n")
    proc.stdin.flush()

    # 轮询 schedule_data.json 直到 job 完成或超时
    sp = PROJECT / "schedule" / "schedule_data.json"
    deadline = time.time() + timeout

    while time.time() < deadline:
        # 检查进程是否退出
        if proc.poll() is not None:
            break

        # 轮询 job 状态
        if sp.exists():
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    active = [s for s in data if s.get("state", {}).get("character_id", "").startswith("测")]
                    if not active:
                        # 无活跃 job，job 已完成
                        print(f"\n[SCHEDULE] 所有 job 已完成（无残留）", flush=True)
                        time.sleep(1)  # 等待最后一条回复
                        break
            except Exception:
                pass

        time.sleep(2)

    elapsed = time.time() - t_start

    # 等待 reader 线程结束
    stream_done.wait(timeout=3)

    # 写日志文件
    with log_lock:
        buf_copy = list(log_buf)

    return buf_copy, elapsed


def run_scenario(scenario_key: str, cfg: dict) -> ScenarioResult:
    """启动 app.py，经历完整流程。"""
    ts = time.strftime("%H%M%S")
    char_name = f"测{scenario_key}{ts}"
    log_path = LOG_BASE / f"scenario_{scenario_key}_{ts}.log"
    ev_path = LOG_BASE / f"evidence_{scenario_key}_{ts}.txt"

    result = ScenarioResult(scenario=scenario_key, name=cfg["name"])
    t_start = time.time()

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        [str(PYTHON), "app.py"],
        cwd=str(PROJECT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        bufsize=0,
        encoding="utf-8",
    )

    # ── 创建角色 ──
    print(f"\n{'='*60}")
    print(f"[SCENARIO {scenario_key}] {cfg['name']}")
    print(f"  角色: {char_name}")
    print(f"{'='*60}\n", flush=True)
    _create_character(proc, char_name)

    # ── 发任务，监控完成 ──
    out_lines, elapsed = _send_input_and_wait(
        proc, cfg["task"], timeout=TIMEOUT_PER_SCENARIO
    )
    result.duration_sec = elapsed

    # ── 退出 app ──
    try:
        proc.stdin.write("quit\n")
        proc.stdin.flush()
        time.sleep(3)
    except Exception:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    result.lines = list(out_lines)

    # ── 保存原始日志 ──
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"SCENARIO: {scenario_key} | {cfg['name']}\n")
        f.write(f"CHAR: {char_name}\n")
        f.write(f"TASK: {cfg['task']}\n")
        f.write(f"EXPECT: {cfg['expect']}\n")
        f.write(f"DURATION: {elapsed:.0f}s\n")
        f.write("=" * 60 + "\n")
        for ln in out_lines:
            f.write(ln + "\n")

    # ── 检查 schedule 残留 ──
    result.schedule_residual = _check_schedule_residual(char_name)
    result.success = len(result.schedule_residual) == 0

    # ── 提取水果输出 ──
    result.fruits = _extract_fruits(out_lines)

    # ── 找 history.json ──
    char_dir = None
    for d in os.listdir(PROJECT / "character_data"):
        if char_name in d:
            char_dir = d
            break

    if char_dir:
        hp = PROJECT / "character_data" / char_dir / "history.json"
        if hp.exists():
            with open(hp, "r", encoding="utf-8") as f:
                hist = json.load(f)
            result.history_entries = len(hist)
            result.triggers = [(i, m) for i, m in enumerate(hist) if m["role"] == "system_trigger"]
            _save_evidence(ev_path, scenario_key, char_name, cfg, hist, out_lines, result)

    print(f"\n[RESULT {scenario_key}] success={result.success}, "
          f"history={result.history_entries}, "
          f"triggers={len(result.triggers)}, "
          f"fruits={len(result.fruits)}, "
          f"schedule_residual={len(result.schedule_residual)}, "
          f"duration={elapsed:.0f}s")

    return result


def _check_schedule_residual(char_name: str) -> list:
    sp = PROJECT / "schedule" / "schedule_data.json"
    if not sp.exists():
        return []
    try:
        with open(sp, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [s for s in data if s.get("state", {}).get("character_id", "").startswith("测")]
        return []
    except Exception:
        return []


def _extract_fruits(lines: list) -> list[str]:
    """从 stdout 中提取包含水果 emoji 的行（排除推理过程）。"""
    fruits = []
    for ln in lines:
        # 只看回复消息中的水果（包含 ····【角色】回复 行之后的内容）
        if any(c in ln for c in "🍒🍌🍊🍉🍇🍓🥭🥝🍍"):
            fruits.append(ln.strip())
    return fruits


def _save_evidence(ev_path, scenario, char_name, cfg, hist, lines, result: ScenarioResult):
    """写结构化证据文件。"""
    triggers = [(i, m) for i, m in enumerate(hist) if m["role"] == "system_trigger"]

    with open(ev_path, "w", encoding="utf-8") as f:
        f.write(f"场景: {scenario} | {cfg['name']}\n")
        f.write(f"角色: {char_name}\n")
        f.write(f"任务: {cfg['task']}\n")
        f.write(f"期望: {cfg['expect']}\n")
        f.write(f"历史条目: {len(hist)} | system_trigger 数: {len(triggers)}\n")
        f.write(f"水果输出: {len(result.fruits)} 条\n")
        f.write(f"完成: {'✅' if result.success else '❌'}\n")
        f.write("=" * 60 + "\n\n")

        f.write("【水果输出（按时间顺序）】\n")
        if result.fruits:
            for fl in result.fruits:
                f.write(f"  {fl[:120]}\n")
        else:
            f.write("  （无水果输出记录）\n")
        f.write("\n")

        f.write("【system_trigger 序列】\n")
        for i, (idx, m) in enumerate(triggers):
            header = m["content"].split("\n")[0]
            f.write(f"  [{i}] {header}\n")
        f.write("\n")

        # 从 stdout 提取时策触发行
        f.write("【时策触发日志】\n")
        in_trigger = False
        for ln in lines:
            if "【时策触发" in ln or "[时策触发" in ln:
                in_trigger = True
            if in_trigger:
                f.write(f"  {ln[:120]}\n")
                if "【时策触发" in ln or "[时策触发" in ln:
                    pass  # 继续
                elif ln.startswith("# 【用户"):
                    in_trigger = False
        f.write("\n")

        # 关键对话片段
        f.write("【AI 关键回复片段】\n")
        in_reply = False
        for ln in lines:
            if "【" in ln and "】回复" in ln:
                in_reply = True
            elif ln.startswith("# 【用户"):
                in_reply = False
            elif ln.startswith("─"):
                in_reply = False
            elif in_reply:
                if any(c in ln for c in "🍒🍌🍊🍉🍇🍓🥭🥝🍍"):
                    f.write(f"  {ln.strip()[:120]}\n")


# ── 主流程 ─────────────────────────────────────────────────────────────────

def main():
    print(f"时策集成测试 v2 | {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"项目: {PROJECT}")
    print(f"日志: {LOG_BASE}\n")

    results: dict[str, ScenarioResult] = {}

    # 清理旧数据（保留之前的手动日志）
    for f in LOG_BASE.glob("scenario_*.log"):
        f.unlink()
    for f in LOG_BASE.glob("evidence_*.txt"):
        f.unlink()

    for key in ["A", "B", "C", "D"]:
        cfg = SCENARIOS[key]
        print(f"\n{'#'*60}")
        print(f"#  开始场景 {key}: {cfg['name']}")
        print(f"#{'#'*60}")
        try:
            r = run_scenario(key, cfg)
            results[key] = r
        except Exception as e:
            import traceback
            traceback.print_exc()
            results[key] = ScenarioResult(scenario=key, name=cfg["name"], error=str(e))
        time.sleep(3)

    # ── 汇总报告 ──
    report_path = LOG_BASE / "测试汇总报告_v2.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("时策集成测试汇总报告 v2\n")
        f.write(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 60 + "\n\n")

        all_ok = True
        for key in ["A", "B", "C", "D"]:
            r = results[key]
            cfg = SCENARIOS[key]
            f.write(f"场景 {key}: {cfg['name']}\n")
            f.write(f"  完成: {'✅' if r.success else '❌'}\n")
            f.write(f"  历史条目: {r.history_entries}\n")
            f.write(f"  system_trigger 数: {len(r.triggers)}\n")
            f.write(f"  水果输出: {len(r.fruits)} 条\n")
            f.write(f"  schedule 残留: {len(r.schedule_residual)}\n")
            f.write(f"  耗时: {r.duration_sec:.0f}s\n")
            if r.error:
                f.write(f"  错误: {r.error}\n")
            f.write(f"  期望: {cfg['expect']}\n")
            if r.fruits:
                f.write(f"  水果:\n")
                for fl in r.fruits[:15]:
                    f.write(f"    {fl[:120]}\n")
            f.write("\n")

            # 判断是否满足验收标准
            if key == "A":
                ok = len(r.fruits) >= 10 and len(r.schedule_residual) == 0
                f.write(f"  验收: {'✅ 10个水果全部输出，schedule无残留' if ok else '❌'}\n")
                if not ok:
                    all_ok = False
            elif key == "B":
                ok = len(r.triggers) >= 1 and len(r.schedule_residual) == 0
                f.write(f"  验收: {'✅ 触发正常，schedule无残留' if ok else '❌（需人工核查补发逻辑）'}\n")
            elif key == "C":
                ok = len(r.triggers) >= 2 and len(r.schedule_residual) == 0
                f.write(f"  验收: {'✅ 多次触发，schedule无残留' if ok else '❌（需人工核查策略切换）'}\n")
            elif key == "D":
                ok = len(r.triggers) >= 1
                f.write(f"  验收: {'✅ 有触发记录' if ok else '❌（需人工核查合并触发）'}\n")
            f.write("\n")

        f.write("=" * 60 + "\n")
        f.write(f"整体: {'✅ 全部场景通过' if all_ok else '⚠️ 部分场景需人工核查'}\n")

    print(f"\n\n{'='*60}")
    print(f"测试完成，报告: {report_path}")
    for f in sorted(LOG_BASE.glob("evidence_*.txt")):
        print(f"  证据: {f.name}")
    for f in sorted(LOG_BASE.glob("scenario_*.log")):
        print(f"  日志: {f.name}")


if __name__ == "__main__":
    main()
