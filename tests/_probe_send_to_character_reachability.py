"""
验证 tool/builtin_tools/characters.py send_to_character 的控制流结构：

  try: ...        ← 内含 break（仅跳出 inner while，不跳出 outer try）
  finally:
      set_actor(sender_name)
      set_display_name(sender_name)
  if not reply.strip(): reply = "(未生成回复)"        ← 行 208
  if reply.startswith("[Error]"): return reply       ← 行 213
  if sender_name != recipient: ... sender_history... ← 行 227
  return ("🔔 ... 共 {len(reply)} 字)...")           ← 行 239

Pylance 报 "Unreachable code" / "Local variable value is not used" 在行 208/213/221/227/233。
本脚本用同形结构跑一遍，证明这些代码实际可达 + 变量实际被使用。
"""
import asyncio
import sys

# 强制 UTF-8 输出（Windows cmd 默认 GBK 会让 🔔 报错）
sys.stdout.reconfigure(encoding="utf-8")


async def replica(recipient: str, sender_name: str, reply: str,
                simulate_error: bool = False) -> str:
    """复刻 send_to_character 的核心 try/finally/return 结构（无业务依赖）。"""
    engine_fallback_note = ""

    try:
        while True:
            # 模拟行 178: 写 reply
            if simulate_error:
                reply = f"[Error] 调用 {recipient} 的 LLM 失败 (mock/test)"
                # 模拟行 188: break（仅跳出 inner while）
                break
            reply = "你好，我收到了。"
            # 模拟行 180: 成功 break（仅跳出 inner while）
            break
    finally:
        # 模拟 set_actor / set_display_name —— 业务里有副作用，本脚本只看可达性
        pass

    # 行 208 - if not reply.strip()
    if not reply.strip():
        reply = "(未生成回复)"

    # 行 213 - if reply.startswith("[Error]")
    if reply.startswith("[Error]"):
        return reply  # 提前返回路径

    # 行 221 - if sender_name != recipient
    if sender_name != recipient:
        sender_history_written = True
    else:
        sender_history_written = False

    # 行 227 - 接收者历史补填
    recipient_history_patched = True

    # 行 233-239 - 终极 return 模板（带 🔔 前缀 + 字数 + fallback_note）
    return (
        f"🔔 {recipient} 无法看到你的普通回复——继续对话请调用 send_to_character\n\n"
        f"[来自 {recipient} 的回复]\n\n{reply}\n\n"
        f"(共 {len(reply)} 字)"
        f"{engine_fallback_note}\n"
        f"[sender_history_written={sender_history_written}, "
        f"recipient_history_patched={recipient_history_patched}]"
    )


def main():
    # 路径 A: 正常路径（走到行 239 终极 return 模板）
    result = asyncio.run(replica(recipient="alice", sender_name="default", reply=""))
    print("[路径 A: 正常路径]")
    print(result)
    print("=" * 60)

    # 路径 B: [Error] 提前返回（行 213-221）
    result_b = asyncio.run(replica(recipient="bob", sender_name="default",
                                   reply="", simulate_error=True))
    print("\n[路径 B: [Error] 提前返回]")
    print(repr(result_b))
    print("=" * 60)

    # 路径 C: sender_name == recipient（行 227 跳过 sender_history 写）
    result_c = asyncio.run(replica(recipient="default", sender_name="default", reply=""))
    print("\n[路径 C: sender==recipient]")
    print(result_c)


if __name__ == "__main__":
    main()