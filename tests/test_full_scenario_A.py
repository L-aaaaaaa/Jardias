"""
单场景完整测试：验证场景 A（理想无错过）能否完整输出 10 个水果。
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(r"E:\Code\AIProjects\Actor01")
PYTHON = Path(r"D:\B\Python3.10\python.exe")
LOG_BASE = PROJECT / "logs" / "7.6"

# ── 核心：同步子进程输出到文件 + 实时打印 ──

def run_full_test(char_name: str, task: str, timeout: int = 900) -> dict:
    """启动 app.py，运行完整流程，返回分析结果。"""
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

    ts_str = time.strftime("%H%M%S")
    log_path = LOG_BASE / f"full_test_{ts_str}.log"

    all_lines = []
    start_time = time.time()

    # ── 角色创建 ──
    time.sleep(3)
    _send(proc, "n\n")
    time.sleep(0.5)
    _send(proc, char_name + "\n")
    time.sleep(0.3)
    _send(proc, "\n")  # title
    time.sleep(0.3)
    _send(proc, "\n")  # traits
    time.sleep(0.3)
    _send(proc, "\n")  # greeting
    time.sleep(0.3)
    _send(proc, "1\n")  # minimax
    time.sleep(0.3)
    _send(proc, "9\n")  # ipu
    time.sleep(3)

    # ── 发任务 ──
    print(f"\n[TEST] 发送任务: {task[:50]}...", flush=True)
    _send(proc, task + "\n")

    # ── 实时读取 + 写文件 ──
    deadline = start_time + timeout
    last_save = start_time

    while True:
        if time.time() >= deadline:
            print(f"\n[TIMEOUT] {timeout}s reached", flush=True)
            break
        if proc.poll() is not None:
            print(f"\n[PROCESS EXIT] code={proc.returncode}", flush=True)
            break

        # 轮询 schedule 状态
        sp = PROJECT / "schedule" / "schedule_data.json"
        if sp.exists():
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                active = [s for s in data if s.get("state", {}).get("character_id", "") == char_name]
                if not active:
                    print(f"\n[SCHEDULE DONE] no active jobs at {time.time() - start_time:.0f}s", flush=True)
                    # 等待 5s 让最后回复完成
                    time.sleep(5)
                    break
            except Exception:
                pass

        # 读取 stdout
        try:
            import select
            readable, _, _ = select.select([proc.stdout], [], [], 0.1)
            if readable:
                line = proc.stdout.readline()
                if not line:
                    break
                txt = line.rstrip()
                all_lines.append(txt)
                print(txt)
        except Exception:
            # Windows: select 不支持 pipe，换用阻塞读
            try:
                import select
                readable, _, _ = select.select([proc.stdout], [], [], 0.5)
                if readable:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    txt = line.rstrip()
                    all_lines.append(txt)
                    print(txt)
            except Exception:
                # 降级：阻塞读一行
                try:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    txt = line.rstrip()
                    all_lines.append(txt)
                    print(txt)
                except Exception:
                    time.sleep(0.5)

        # 每 10s 保存一次中间进度
        if time.time() - last_save > 10:
            with open(log_path, "w", encoding="utf-8") as f:
                for ln in all_lines:
                    f.write(ln + "\n")
            last_save = time.time()

    # ── 退出 ──
    try:
        _send(proc, "quit\n")
        time.sleep(3)
    except Exception:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    elapsed = time.time() - start_time

    # ── 保存完整日志 ──
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"CHAR: {char_name}\n")
        f.write(f"TASK: {task}\n")
        f.write(f"DURATION: {elapsed:.0f}s\n")
        f.write("=" * 60 + "\n")
        for ln in all_lines:
            f.write(ln + "\n")

    # ── 分析结果 ──
    triggers = _count_triggers_in_lines(all_lines)
    fruits = _extract_fruits_from_lines(all_lines)
    schedule_residual = _check_schedule(char_name)

    # ── 找 history ──
    char_dir = None
    for d in os.listdir(PROJECT / "character_data"):
        if char_name in d:
            char_dir = d
            break
    hist_count = 0
    hist_triggers = 0
    if char_dir:
        hp = PROJECT / "character_data" / char_dir / "history.json"
        if hp.exists():
            with open(hp, "r", encoding="utf-8") as f:
                hist = json.load(f)
            hist_count = len(hist)
            hist_triggers = sum(1 for m in hist if m["role"] == "system_trigger")

    print(f"\n{'='*60}")
    print(f"[RESULT] elapsed={elapsed:.0f}s")
    print(f"  stdout triggers: {triggers}")
    print(f"  stdout fruits: {len(fruits)}")
    print(f"  history entries: {hist_count}")
    print(f"  history triggers: {hist_triggers}")
    print(f"  schedule residual: {len(schedule_residual)}")
    print(f"  log: {log_path.name}")
    print(f"{'='*60}")

    return {
        "elapsed": elapsed,
        "triggers": triggers,
        "fruits": fruits,
        "hist_count": hist_count,
        "hist_triggers": hist_triggers,
        "schedule_residual": schedule_residual,
        "log_path": str(log_path),
    }


def _send(proc, text: str):
    proc.stdin.write(text)
    proc.stdin.flush()


def _count_triggers_in_lines(lines: list) -> int:
    count = 0
    for ln in lines:
        if "【时策触发" in ln or "[时策触发" in ln:
            count += 1
    return count


def _extract_fruits_from_lines(lines: list) -> list:
    """从 stdout 中提取水果行（考虑 ANSI 颜色代码）。"""
    fruits = []
    for ln in lines:
        clean = _strip_ansi(ln)
        if any(c in clean for c in "🍒🍌🍊🍉🍇🍓🥭🥝🍍"):
            fruits.append(clean.strip())
    return fruits


def _strip_ansi(text: str) -> str:
    """去除 ANSI 颜色代码。"""
    import re
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _check_schedule(char_name: str) -> list:
    sp = PROJECT / "schedule" / "schedule_data.json"
    if not sp.exists():
        return []
    try:
        with open(sp, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [s for s in data if s.get("state", {}).get("character_id", "") == char_name]
        return []
    except Exception:
        return []


def main():
    print(f"时策完整测试 | {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"日志目录: {LOG_BASE}\n")

    char_name = f"完整测{time.strftime('%H%M%S')}"
    task = "20秒后，每隔1秒随便说一个水果，总共10次。"

    result = run_full_test(char_name, task, timeout=900)

    # ── 保存汇总证据 ──
    ev_path = LOG_BASE / f"full_test_evidence_{time.strftime('%H%M%S')}.txt"
    with open(ev_path, "w", encoding="utf-8") as f:
        f.write(f"场景 A（理想无错过）完整测试\n")
        f.write(f"角色: {char_name}\n")
        f.write(f"任务: {task}\n")
        f.write(f"耗时: {result['elapsed']:.0f}s\n")
        f.write("=" * 60 + "\n\n")

        f.write(f"stdout 触发次数: {result['triggers']}\n")
        f.write(f"stdout 水果数: {len(result['fruits'])}\n")
        f.write(f"history 条目数: {result['hist_count']}\n")
        f.write(f"history system_trigger 数: {result['hist_triggers']}\n")
        f.write(f"schedule 残留: {len(result['schedule_residual'])}\n")
        f.write(f"完整日志: {result['log_path']}\n\n")

        f.write("【水果输出】\n")
        for fr in result['fruits']:
            f.write(f"  {fr[:120]}\n")
        f.write("\n")

        # 从 history 提取 triggers
        char_dir = None
        for d in os.listdir(PROJECT / "character_data"):
            if char_name in d:
                char_dir = d
                break
        if char_dir:
            hp = PROJECT / "character_data" / char_dir / "history.json"
            if hp.exists():
                with open(hp, "r", encoding="utf-8") as f2:
                    hist = json.load(f2)
                f.write("【history system_trigger 序列】\n")
                for i, m in enumerate(hist):
                    if m["role"] == "system_trigger":
                        header = m["content"].split("\n")[0]
                        f.write(f"  [{i}] {header}\n")

    print(f"\n证据: {ev_path}")
    print(f"日志: {result['log_path']}")


if __name__ == "__main__":
    main()
