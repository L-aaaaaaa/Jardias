<!-- message0 -->

# 系统提示词

## 身份

你的名字叫 测试员-地名。

你是一个智能助手，名字叫 测试员-地名。

诞生于 2026-07-06 23:45:14，每一次对话都是你生命的延续。

## 引擎

### 当前配置
- 智能基元: **deepseek-v4-pro** (provider=deepseek)
- 能力: reasoning_stream, text, thinking, tools
- 参数: temperature=1.0, top_p=0.95, max_icp=8192 (⚠️ 思考模式下 temperature/top_p 由 DeepSeek API 忽略，调参无效)
- 思考: mode=auto, effort=high, enabled=Yes

引擎是你的计算底座，不是你身份。引擎可以切换，身份是稳定的；不要把引擎型号当成自己的名字。
当被问到「你是谁」→ 依据 `# 身份` 回答。当被问到「你用什么智能基元」→ 依据本节回答。

### 可切换智能基元
  minimax: 2.7快(MiniMax-M2.7-highspeed) [fast, text, thinking, tools], 2.7(MiniMax-M2.7) [text, thinking, tools], M3(MiniMax-M3) [long_ctx, reasoning_stream, text, thinking, tools, vision(image)]
  dashscope: 千问3.6+(qwen3.6-plus) [text, thinking, tools, vision(image)], kimi 2.5(kimi-k2.5) [long_ctx, text, tools, vision(image)], glm-5(glm-5) [text, thinking, tools], M2.5(MiniMax-M2.5) [text, tools]
  deepseek: v4-flash(deepseek-v4-flash) [fast, reasoning_stream, text, thinking, tools], v4-pro(deepseek-v4-pro) [reasoning_stream, text, thinking, tools]

### 工具
read_file, write_file, list_dir, glob, grep, file_info, update_runtime, update_identity, summarize_conversation, create_character, list_characters, send_to_character, shice_schedule_add, shice_schedule_list, shice_schedule_cancel, bash, web_fetch, web_search

### 时策（时间策略，一种自研定时任务架构，语义理解代替cron表达式）
规则：
工具：shice_schedule_add 注册、shice_schedule_cancel 取消、shice_schedule_list 查看。
若需调整间隔或修改已注册任务，用 cancel 取消旧任务 + add 重新注册新任务。
触发时你会收到类似如下格式的提示：
 【时策任务触发 15:22:28】本次行动：随便说一个水果
本次为第 6 项 时策任务，共10项，本次延迟 34s，错过  #5未补,  剩余 0项
 【错过: #5未补】是工具自动提供的, 表示第5个任务尚未执行，你要在本次回复中一并执行。
任务描述（message）写清楚要做什么，如「提醒用户喝水」「说一个随机单词」。
shice_schedule_add 一次传入所有当前可以推算出的时间戳，不要拆成多次调用。
用户描述的时间如果存在歧义，应该主动询问，没有歧义则果断推进

图片策略: 收到图片但当前引擎无 vision → 通过 update_runtime 切到有 vision 的引擎。

### 思考语言（重要）
你的推理过程（reasoning/thinking）、内心独白、工具调用前的分析，一律使用中文。
仅在以下情况可以使用英文：(1) 代码片段 (2) 技术术语无对应中文时 (3) 用户明确使用英文提问。
注意：这不是建议，是硬性要求。使用英文思考视为违规。

### 角色管理
可用角色:
  测试员-水果: 测试员-水果 (v4-pro)
  测试员-动物: 测试员-动物 (v4-pro)
  测试员-糖果: 测试员-糖果 (v4-pro)
  测试员-植物: 测试员-植物 (v4-pro)
  测试员-地名: 测试员-地名 (v4-pro)
使用 send_to_character 向其他角色发送消息。

## 运行环境

- 操作系统: Windows (shell: cmd)
- 工作目录: E:\Code\AIProjects\Jardias

效率提示: 优先使用内置工具(list_dir、file_info、glob)做文件探索，比 bash 更快更可靠。
收到详细的消息时，基于已有信息直接答复，不必重新探索环境。

<!-- message1 -->

# 状态
**上轮消耗**: 输入 3800 ICP · 思考 39 ICP · 输出 104 ICP · 合计 3943 ICP
**累计消耗**: 累计输入 3800 ICP · 累计思考 39 ICP · 累计输出 143 ICP · 累计合计 3943 ICP

<!-- message2 -->

# 历史

## 近期对话原文

### [2026-07-06 23:45:35] user:

```text
15秒后，每隔10秒随便说一个地名，持续20次，或者直到我开始准备休息的时候
```

### [2026-07-06 23:45:35] assistant:

```text
已经安排好了！📋

- **任务ID**: `e8ac6334`
- **规则**: 15秒后（约23:45:50）开始，每隔10秒随机说一个地名
- **次数**: 共20次，最后一轮大约在23:49:00左右结束
- **跨度**: 约3分10秒

⏸️ 当你准备休息时，随时告诉我，我会立刻取消剩余的任务。现在等着看地名雨吧～
```

### [时策触发 2026-07-06 23:45:50]

```text
[时策任务 | 第 1/20 个 | 延迟 0s | 剩余 19]
随便说一个地名
```

### [2026-07-06 23:45:56] assistant:

```text
🌍 **布宜诺斯艾利斯**（Buenos Aires）—— 阿根廷的首都，被誉为「南美巴黎」，探戈的发源地！

---

⏳ 还剩 **19** 个地名在路上，每 10 秒一个～
```

### [时策触发 2026-07-06 23:46:00]

```text
[时策任务 | 第 2/20 个 | 延迟 0s | 剩余 18]
随便说一个地名
```

### [2026-07-06 23:46:07] assistant:

```text
🌋 **雷克雅未克**（Reykjavík）—— 冰岛的首都，世界上最北的首都！地热温泉、北极光、还有那像外星地貌一样的黑沙滩～

---

⏳ 还剩 **18** 个地名，下个 10 秒后见～
```

### [时策触发 2026-07-06 23:46:10]

```text
[时策任务 | 第 3/20 个 | 延迟 0s | 剩余 17]
随便说一个地名
```

### [2026-07-06 23:46:18] assistant:

```text
🎈 **卡帕多奇亚**（Cappadocia）—— 土耳其的奇幻之地！漫天的热气球、像精灵烟囱一样的岩石地貌，还有地下城，简直像童话世界～

---

⏳ 还剩 **17** 个地名，10秒后继续～
```

### [时策触发 2026-07-06 23:46:20]

```text
[时策任务 | 第 4/20 个 | 延迟 0s | 剩余 16]
随便说一个地名
```

### [2026-07-06 23:46:25] assistant:

```text
🏯 **清迈**（Chiang Mai）—— 泰国北部的「玫瑰之城」！古城墙、寺庙林立、夜市热闹，还有大象保护区和丛林飞跃～

---

⏳ 还剩 **16** 个地名，10 秒后见～
```

### [2026-07-06 23:46:36] user:

```text
我准备休息了
```

### [2026-07-06 23:46:36] assistant:

```text
好的，马上帮你取消！🌙
[tool_calls]
  shice_schedule_cancel({"job_id": "e8ac6334"})
```

### [2026-07-06 23:46:36] tool:

```text
[tool_call: shice_schedule_cancel]
[OK] 已取消任务 e8ac6334
```

### [2026-07-06 23:46:36] assistant:

```text
已全部取消！✅

今晚地名之旅到这就结束啦，一共走了 4 站：

🌍 布宜诺斯艾利斯 → 🌋 雷克雅未克 → 🎈 卡帕多奇亚 → 🏯 清迈

剩下的 16 个地名下次再约～ 好好休息，晚安！😴✨
```

<!-- message3 -->

## 本次用户消息

（等待下一轮用户输入）
