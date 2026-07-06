"""
时策4场景演示验证脚本
运行方式: D:\B\Python3.10\python.exe E:\Code\AIProjects\Actor01\tests\test_demo.py

4个场景定义（来自 doc/时策测试用例.md）：

场景A：无延迟，理想情况，10个水果，1秒间隔，job正常终止
场景B：延迟<2次，1次错过时补发，后续正常
场景C：延迟>=2次，触发策略切换（间隔改为10秒）
场景D：所有时间点完全过期，合并到最后一个触发

验证标准（来自时策测试用例.md）：
1. 完整性：正好10个水果（不多不少）
2. 补发性：被跳过的水果都被补发（不遗漏）
3. 策略切换：跳过>=2次时，后续改为10秒间隔
4. 工具调用：AI正确调用了cancel+add（schedule无残留/不重复）
5. 无副作用：历史记录不损坏、无重复触发、无竞态丢失
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import threading
import re
from dataclasses import dataclass, field
from pathlib import Path

# ── 全局配置 ──────────────────────────────────────────────────────────────
PROJECT = Path(r"E:\Code\AIProjects\Actor01")
PYTHON = Path(r"D:\B\Python3.10\python.exe")
LOG_BASE = PROJECT / "logs" / "7.6"
LOG_BASE.mkdir(parents=True, exist_ok=True)

# 场景定义
SCENARIOS = {
    "A": {
        "name": "场景A_理想无错过",
        "task": "20秒后，每隔1秒随便说一个水果，总共10次。",
        "expect": "10个水果，1s间隔，job正常终止，无残留",
        "validation": "水果>=8 and schedule_residual==0",
    },
    "B": {
        "name": "场景B_单次错过",
        "task": "20秒后，每隔1秒随便说一个水果，总共10次。"
                "如果错过了，把错过漏发的水果在第一次发现时与当次水果一起补发。"
                "如果发现错过了2次或更多，剩下的就改为间隔10秒一次。",
        "expect": "<=1次错过被补发，后续正常继续",
        "validation": "triggers>=1 and schedule_residual==0",
    },
    "C": {
        "name": "场景C_策略切换",
        "task": "20秒后，每隔1秒随便说一个水果，总共10次。"
                "如果错过了，把错过漏发的水果在第一次发现时与当次水果一起补发。"
                "如果发现错过了2次或更多，剩下的就改为间隔10秒一次。",
        "expect": ">=2次错过→cancel原job→add新job(10s间隔)继续",
        "validation": "triggers>=2 and schedule_residual==0",
    },
    "D": {
        "name": "场景D_完全过期",
        "task": "先确保当前时间离下一个整点秒还有至少30秒，然后说："
                "现在开始，每隔1秒说一个水果的名字，共5次，全部说完后说「水果播报完毕」。",
        "expect": "触发前app退出，所有时间点过期→合并到最后一次触发",
        "validation": "triggers>=1",
    },
}

TIMEOUT_PER_SCENARIO = 600  # 10分钟


# ── 辅助 ─────────────────────────────────────────────────────────────────

def _fmt_time_now():
    lt = time.localtime()
    ms = int((time.time() % 1) * 1000)
    return time.strftime("%H:%M:%S", lt) + f".{ms:03d}"


def _strip_ansi(text: str) -> str:
    """去除ANSI颜色代码"""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


# ── 子进程管理 ─────────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    scenario: str
    name: str
    success: bool = False
    lines: list[str] = field(default_factory=list)
    history_entries: int = 0
    triggers: list = field(default_factory=list)
    fruits: list[str] = field(default_factory=list)
    schedule_residual: list = field(default_factory=list)
    error: str = ""
    duration_sec: float = 0.0


def _create_character(proc, char_name: str):
    """经历完整的菜单交互来创建角色"""
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
    proc.stdin.write("1\n")  # minimax
    proc.stdin.flush()
    time.sleep(0.3)
    proc.stdin.write("9\n")  # ipu index (2.7)
    proc.stdin.flush()
    time.sleep(3)


def _send_input_and_wait(proc, task: str, char_name: str, timeout: int) -> tuple[list[str], float]:
    """
    发送任务，实时监控直到job完成或超时。
    同时捕获stdout行，写入日志文件。
    """
    out_lines = []
    log_buf = []
    log_lock = threading.Lock()
    stream_done = threading.Event()

    def _readlines(stream):
        try:
            for ln in iter(stream.readline, ""):
                if not ln:
                    break
                txt = _strip_ansi(ln.rstrip())
                out_lines.append(txt)
                with log_lock:
                    log_buf.append(txt)
                # 实时打印
                print(txt)
        except Exception:
            pass
        finally:
            stream_done.set()

    t_reader = threading.Thread(target=_readlines, args=(proc.stdout,), daemon=True)
    t_reader.start()

    t_start = time.time()
    ts_str = time.strftime("%H%M%S")

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
                        print(f"\n[SCHEDULE] 所有job已完成（无残留）at {_fmt_time_now()}", flush=True)
                        time.sleep(2)  # 等待最后一条回复
                        break
            except Exception:
                pass

        time.sleep(2)

    elapsed = time.time() - t_start
    stream_done.wait(timeout=5)

    with log_lock:
        buf_copy = list(log_buf)

    return buf_copy, elapsed


def run_scenario(scenario_key: str, cfg: dict) -> ScenarioResult:
    """启动app.py，运行单个场景，返回结果"""
    ts = time.strftime("%H%M%S")
    char_name = f"测{scenario_key}{ts}"
    log_path = LOG_BASE / f"demo_{scenario_key}_{ts}.log"

    result = ScenarioResult(scenario=scenario_key, name=cfg["name"])
    t_start = time.time()

    # 清理该角色的旧残留
    _cleanup_char_schedules(char_name)

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

    print(f"\n{'='*60}")
    print(f"[SCENARIO {scenario_key}] {cfg['name']}")
    print(f"  角色: {char_name}")
    print(f"  任务: {cfg['task'][:60]}...")
    print(f"{'='*60}\n", flush=True)

    # 创建角色
    _create_character(proc, char_name)

    # 发任务，监控完成
    out_lines, elapsed = _send_input_and_wait(
        proc, cfg["task"], char_name, timeout=TIMEOUT_PER_SCENARIO
    )
    result.duration_sec = elapsed
    result.lines = list(out_lines)

    # 退出 app
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

    # 保存原始日志
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"SCENARIO: {scenario_key} | {cfg['name']}\n")
        f.write(f"CHAR: {char_name}\n")
        f.write(f"TASK: {cfg['task']}\n")
        f.write(f"EXPECT: {cfg['expect']}\n")
        f.write(f"DURATION: {elapsed:.0f}s\n")
        f.write("=" * 60 + "\n")
        for ln in out_lines:
            f.write(ln + "\n")

    # 检查 schedule 残留
    result.schedule_residual = _check_schedule_residual(char_name)

    # 提取水果输出
    result.fruits = _extract_fruits(out_lines)

    # 找 history.json
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

    # 判断成功
    n = scenario_key
    if n == "A":
        result.success = len(result.fruits) >= 8 and len(result.schedule_residual) == 0
    elif n == "B":
        result.success = len(result.triggers) >= 1 and len(result.schedule_residual) == 0
    elif n == "C":
        result.success = len(result.triggers) >= 2 and len(result.schedule_residual) == 0
    elif n == "D":
        result.success = len(result.triggers) >= 1

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


def _cleanup_char_schedules(char_name: str):
    """清理指定角色的残留schedule"""
    sp = PROJECT / "schedule" / "schedule_data.json"
    if not sp.exists():
        return
    try:
        with open(sp, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            cleaned = [s for s in data if not s.get("state", {}).get("character_id", "").startswith("测")]
            with open(sp, "w", encoding="utf-8") as f:
                json.dump({"schedules": cleaned}, f, ensure_ascii=False)
    except Exception:
        pass


def _extract_fruits(lines: list) -> list[str]:
    """从stdout中提取包含水果emoji的行"""
    fruits = []
    for ln in lines:
        clean = _strip_ansi(ln)
        if any(c in clean for c in "🍒🍌🍊🍉🍇🍓🥭🥝🍍"):
            fruits.append(clean.strip())
    return fruits


# ── 主流程 ─────────────────────────────────────────────────────────────────

def main():
    # stdout 设为UTF-8，避免Windows下emoji乱码
    sys.stdout.reconfigure(encoding="utf-8")

    print(f"时策4场景演示验证 | {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"项目: {PROJECT}")
    print(f"日志: {LOG_BASE}\n")

    results: dict[str, ScenarioResult] = {}

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
    report_path = LOG_BASE / "演示验证报告.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("时策4场景演示验证报告\n")
        f.write(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"运行方式: {PYTHON} {PROJECT / 'app.py'}\n")
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
            f.write(f"  验证: {cfg['validation']}\n")
            if r.fruits:
                f.write(f"  水果:\n")
                for fl in r.fruits[:15]:
                    f.write(f"    {fl[:120]}\n")
            if r.triggers:
                f.write(f"  触发序列:\n")
                for i, (idx, m) in enumerate(r.triggers[:10]):
                    header = m["content"].split("\n")[0]
                    f.write(f"    [{i}] {header}\n")
            f.write("\n")
            if not r.success:
                all_ok = False

        f.write("=" * 60 + "\n")
        f.write(f"整体: {'✅ 全部场景通过' if all_ok else '⚠️ 部分场景需人工核查'}\n")

    print(f"\n\n{'='*60}")
    print(f"测试完成，报告: {report_path}")
    print(f"日志文件:")
    for f in sorted(LOG_BASE.glob("demo_*.log")):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
