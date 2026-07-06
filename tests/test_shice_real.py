"""真实 CLI 测试：时策错过处理。轮询 stdout 直到全部完成。"""
import subprocess
import threading
import time
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

PROJECT = r"E:\Code\AIProjects\Actor01"
PYTHON = r"D:\B\Python3.10\python.exe"

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"

proc = subprocess.Popen(
    [PYTHON, "app.py"],
    cwd=PROJECT,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    env=env,
    bufsize=0,
    encoding="utf-8",
)

output_lines = []
triggers_done = 0
_all_done = threading.Event()

def read_output():
    global triggers_done
    for line in iter(proc.stdout.readline, ""):
        text = line.rstrip()
        output_lines.append(text)
        print(text, flush=True)

        # 检测"全部完成"信号（AI 的收官回复里会有）
        if "全部" in text and ("完成" in text or "结束" in text or "收官" in text):
            triggers_done += 1
            if triggers_done >= 1:
                _all_done.set()

reader = threading.Thread(target=read_output, daemon=True)
reader.start()

# ── 阶段 1：创建新角色 ──
char_name = f"测试{time.strftime('%H%M%S')}"
print(f"\n{'='*60}")
print(f"[TEST] 阶段 1: 创建 {char_name}")
print(f"{'='*60}\n", flush=True)

time.sleep(4)

proc.stdin.write("n\n")
proc.stdin.flush()
time.sleep(1)
proc.stdin.write(char_name + "\n")
proc.stdin.flush()
time.sleep(0.5)
proc.stdin.write("\n")
proc.stdin.flush()
time.sleep(0.5)
proc.stdin.write("\n")
proc.stdin.flush()
time.sleep(0.5)
proc.stdin.write("1\n")
proc.stdin.flush()
time.sleep(0.5)
proc.stdin.write("9\n")
proc.stdin.flush()
time.sleep(2)

# ── 阶段 2：发测试 ──
test_input = "20秒后，每隔1秒随便说一个水果，总共10次。如果错过了，把错过漏发的水果在第一次发现时与当次水果一起补发。如果发现错过了2次或更多，剩下的就改为间隔10秒一次。"
print(f"\n{'='*60}")
print(f"[TEST] 阶段 2: 发送测试")
print(f"{'='*60}\n", flush=True)

proc.stdin.write(test_input + "\n")
proc.stdin.flush()

# ── 等待完成，最多 10 分钟 ──
print(f"\n[TEST] 等待完成（最长 600s）...\n", flush=True)
if not _all_done.wait(timeout=600):
    print("\n[TEST] 超时，强制退出\n", flush=True)

proc.stdin.write("quit\n")
proc.stdin.flush()
time.sleep(5)

proc.terminate()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()

# 保存 stdout 日志
log_dir = os.path.join(PROJECT, "logs")
os.makedirs(log_dir, exist_ok=True)
log_path = os.path.join(log_dir, f"test_shice_v3_{time.strftime('%m%d_%H%M%S')}.log")
with open(log_path, "w", encoding="utf-8") as f:
    f.write("\n".join(output_lines))

# ── 找到刚创建的角色的 history.json ──
char_dir = None
for d in os.listdir(os.path.join(PROJECT, "character_data")):
    if char_name in d:
        char_dir = os.path.join(PROJECT, "character_data", d)
        break

import json
if char_dir:
    hp = os.path.join(char_dir, "history.json")
    if os.path.exists(hp):
        with open(hp, "r", encoding="utf-8") as f:
            hist = json.load(f)
        
        evidence_path = os.path.join(char_dir, "_evidence.txt")
        with open(evidence_path, "w", encoding="utf-8") as f:
            f.write(f"Character: {os.path.basename(char_dir)}\n")
            f.write(f"History entries: {len(hist)}\n\n")
            for i, e in enumerate(hist):
                r = e["role"]
                c = e["content"]
                if r == "system_trigger":
                    header = c.split("\n")[0]
                    f.write(f"[{i}] system_trigger:\n  {header}\n\n")
                else:
                    f.write(f"[{i}] {r}: {c[:300]}\n\n")

        print(f"\n[TEST] evidence: {evidence_path}")
        print(f"[TEST] history entries: {len(hist)}")
else:
    print(f"\n[TEST] 找不到角色目录: {char_name}")

print(f"\n[TEST] 日志: {log_path}")
print(f"[TEST] 共 {len(output_lines)} 行输出")
