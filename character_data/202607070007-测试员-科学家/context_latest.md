<!-- message0 -->

# 系统提示词

## 身份

你的名字叫 测试员-科学家。

你是一个智能助手，名字叫 测试员-科学家。

诞生于 2026-07-07 00:07:26，每一次对话都是你生命的延续。

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
用户描述的时间如果存在【歧义/边界不清】，应该【主动询问】，没有则果断推进

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
  测试员-科学家: 测试员-科学家 (v4-pro)
使用 send_to_character 向其他角色发送消息。

## 运行环境

- 操作系统: Windows (shell: cmd)
- 工作目录: E:\Code\AIProjects\Jardias

效率提示: 优先使用内置工具(list_dir、file_info、glob)做文件探索，比 bash 更快更可靠。
收到详细的消息时，基于已有信息直接答复，不必重新探索环境。

<!-- message1 -->

# 状态

（首轮对话，暂无消耗数据）

<!-- message2 -->

# 历史

## 近期对话原文

### [2026-07-07 00:07:27] user:

```text
1分钟后每15秒发送一个科学家名字给我
```

### [2026-07-07 00:07:27] assistant:

```text
好的！不过有一个小问题需要确认：**你想让我发送多少次科学家的名字呢？** 比如：

- 总共发 5 次？
- 发 10 次？
- 还是持续发直到你叫停？

请告诉我你的偏好，我马上安排！
```

### [2026-07-07 00:07:46] user:

```text
持续3次
```

### [2026-07-07 00:07:46] assistant:

```text
已经安排好了！📋

**任务详情：**
- 🕐 **首次触发**：00:08:46（约54秒后）
- ⏱️ **间隔**：每15秒一次
- 🔢 **次数**：共3次
- 🆔 **任务ID**：`5e3c5b72`

每次我会随机挑选一位著名科学家（如爱因斯坦、居里夫人、霍金等）并附上一句简短介绍。到时候见！🧪
```

### [时策触发 2026-07-07 00:08:46]

```text
[时策任务 | 第 1/3 个 | 剩余 2]
向用户发送一个著名科学家的名字（如爱因斯坦、牛顿、居里夫人、达尔文、霍金、特斯拉、费曼等），每次选择不同的科学家，附上简短的一句介绍（5-10字）。
```

### [2026-07-07 00:08:52] assistant:

```text
🧪 **第 1/3 位科学家：**

> **尼古拉·特斯拉** —— 交流电之父，用电流点亮了现代世界。⚡

（下一次将在约 15 秒后送达，敬请期待！）
```

### [时策触发 2026-07-07 00:09:01]

```text
[时策任务 | 第 2/3 个 | 延迟 0s | 剩余 1]
向用户发送一个著名科学家的名字（如爱因斯坦、牛顿、居里夫人、达尔文、霍金、特斯拉、费曼等），每次选择不同的科学家，附上简短的一句介绍（5-10字）。
```

### [2026-07-07 00:09:05] assistant:

```text
🧪 **第 2/3 位科学家：**

> **查尔斯·达尔文** —— 用自然选择揭开了万物起源的奥秘。🐢🌿

（最后一位即将在约 15 秒后抵达！）
```

### [时策触发 2026-07-07 00:09:16]

```text
[时策任务 | 第 3/3 个 | 剩余 0]
向用户发送一个著名科学家的名字（如爱因斯坦、牛顿、居里夫人、达尔文、霍金、特斯拉、费曼等），每次选择不同的科学家，附上简短的一句介绍（5-10字）。
```

### [2026-07-07 00:09:20] user:

```text
【时策任务触发 00:09:16】本次行动：向用户发送一个著名科学家的名字（如爱因斯坦、牛顿、居里夫人、达尔文、霍金、特斯拉、费曼等），每次选择不同的科学家，附上简短的一句介绍（5-10字）。
本次为第 3 项 时策任务，共3项，本次延迟 0s,  剩余 0项
```

### [2026-07-07 00:09:20] assistant:

```text
🧪 **第 3/3 位科学家：**

> **玛丽·居里** —— 放射性研究的先驱，两获诺奖的传奇女性。☢️🥇

---

🎉 三位科学家已全部送达！任务完成。如果还想继续探索更多伟大科学家的故事，随时告诉我！
```

<!-- message3 -->

## 本次用户消息

（等待下一轮用户输入）
