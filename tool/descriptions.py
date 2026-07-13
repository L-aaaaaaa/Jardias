"""工具描述集中存放。

设计原则：
- 此文件只放常量，不允许放任何动态逻辑（运行时拼接由 metadata.py 负责）。
- 描述面向 LLM：中文 / Markdown / 包含使用场景与反例，**不要**写内部实现细节。
- 长描述按"使用场景 / 效果 / 模式 / 反例"分段，让 LLM 容易检索。
- 短描述写"功能一句话"，由长描述的第一段派生。

参数描述（每个工具一份）的 schema 在 metadata.py 里注入 ToolDef.parameters[*][description]。
"""

# ── 通用：文件中读取相关的工具短描述（同族共用）─────────────
# 这些工具共享"相对路径以当前 cwd 为基准"约定。


# ── 文件工具 ───────────────────────────────────────────────

READ_FILE = "读取文件内容。"
READ_FILE_PARAMS = {
    "path": "文件路径（相对路径以当前工作目录为基准）。",
    "line_range": "可选的行列范围，格式 `start,end`（从 1 开始，含头含尾）。",
}

WRITE_FILE = "写入内容到文件。"
WRITE_FILE_PARAMS = {
    "path": "文件路径（相对路径以当前工作目录为基准）。",
    "content": "要写入的完整字符串。",
    "mode": "`w` 为覆盖（默认），`a` 为追加。",
}

GET_DIRECTORY_TREE = "列出目录下的文件和子目录；支持按深度展开或完全递归。"
GET_DIRECTORY_TREE_PARAMS = {
    "path": "目录路径，默认 `.`。",
    "depth": "展开层数，默认 `1`（仅当前层）。`0` 或负数视为无限递归。",
    "recursive": "是否完全递归展开所有层，默认 `false`。`depth > 1` 时此参数被忽略。",
    "max_entries": "最多返回多少条目，默认 `500`；超出时截断并提示缩小范围。",
}

SEARCH_IN_PATH = "按 glob 模式匹配文件路径。"
SEARCH_IN_PATH_PARAMS = {
    "pattern": "glob 模式，例如 `**/*.py`。",
    "path": "搜索根目录，默认 `.`。",
}

SEARCH_IN_CONTENT = "在文件中按正则表达式搜索。"
SEARCH_IN_CONTENT_PARAMS = {
    "pattern": "正则表达式字符串。",
    "path": "搜索根（文件直接搜；目录则递归），默认 `.`。",
    "case_insensitive": "是否忽略大小写，默认 `false`。",
    "max_results": "最大返回条目数，默认 20。",
}

GET_FILE_METADATA = "获取文件或目录的元信息（绝对路径、修改时间、字节数）。"
GET_FILE_METADATA_PARAMS = {
    "path": "文件或目录路径。",
}


# ── 自手术工具 ─────────────────────────────────────────────

UPDATE_RUNTIME = (
    "更新运行时参数：ipu / temperature / top_p / max_icp / thinking_mode / "
    "reasoning_effort / thinking_enabled 等。\n\n"
    "修改后下一轮生效。如果 ipu 变了，会自动切换引擎并可能重建 LLM client。\n\n"
    "**参数互斥**：`reasoning_effort` 需 `thinking_enabled=true`；"
    "关闭 thinking 时**自动清除**之前设置的 reasoning_effort。"
)

UPDATE_IDENTITY = (
    "更新身份参数：system_prompt / title / traits / max_iterations。"
    "修改后下一轮生效。"
)

UPDATE_RUNTIME_PARAMS = {
    "ipu": (
        "智能基元**短名**（参考运行时由 metadata 注入的可用列表）。"
        "短名变更会触发 provider 切换；切换前会自动校验目标供应商是否已熔断。"
    ),
    "temperature": "取值区间 [0, 2]。开 thinking 时 DeepSeek 端会忽略此参数。",
    "top_p": "取值区间 [0, 1]。开 thinking 时 DeepSeek 端会忽略此参数。",
    "max_icp": "正整数，单轮最大输出 token 数。",
    "thinking_mode": "`enabled` / `disabled` / `auto` 之一。",
    "reasoning_effort": "`high` 或 `max`，仅 DeepSeek 等部分模型支持。"
                     "**需 `thinking_enabled=true`**，否则会自动开启 thinking。"
                     "关 thinking 时本字段自动清除。",
    "thinking_enabled": "`true` / `false`。",  # description
}

UPDATE_IDENTITY_PARAMS = {
    "system_prompt": "核心人格定义（由 system message 注入到每一次对话）。",
    "title": "角色头衔，写入 experience.md 的角色身份区。",
    "traits": "特质描述，附加在 system prompt 后作为修饰。",
    "max_iterations": "单轮推理工具调用最大次数（正整数）。",
}


# ── 历史/摘要工具 ─────────────────────────────────────────

SUMMARIZE_CONVERSATION = (
    "将较早的对话历史压缩为 L1 摘要，节省上下文。\n\n"
    "**何时调用**：当前后端接近 max_icp 上限、发现上下文里有较旧的话题已结束、"
    "或者你想主动清理时。\n\n"
    "**默认行为**：`keep_recent_turns=6` 保留最近 6 轮原文，其余全部压缩。"
    "压缩后的摘要下轮起由 summarizer 自动注入。"
)

SUMMARIZE_CONVERSATION_PARAMS = {
    "keep_recent_turns": "保留的最近轮数（默认 6）。",
    "topic": "摘要主题（可选，留空自动推断）。",
}


# ── 话题归档工具 ─────────────────────────────────────────
# 这是项目里最重要的工具之一，描述长达 ~45 行，必须分块写。

ARCHIVE_RECENT_TALK = (
    "按时间戳归档一段或多段对话为话题摘要，释放上下文空间。\n\n"

    "**使用场景**：用户说「转为摘要」「归档这个话题」「先聊别的」时调用。\n\n"

    "**效果**：指定时间范围内的对话被提炼为结构化摘要存入磁盘，下轮起不再占用上下文。"
    "后续可通过 recall_topic 精确召回继续讨论。\n\n"

    "**重要原则：传参即结果** —— 你传入什么时间戳，工具就归档什么范围；工具不会自作主张"
    "扩展或收缩区间。归档前请从上下文里**直接读取**你看到的时间戳字面值，不要估算。\n\n"

    "**两种模式（二选一）**：\n"
    "1. **单段模式**（兼容性）：传 `time_range_start` + `time_range_end`，归档一段连续对话。\n"
    "2. **聚合模式**（推荐）：传 `time_ranges` 数组，每个元素是 `[start, end]`，可离散不连续。\n"
    "   工具一次性合并为同一条 L1、共享同一个 id，召回时一次性注入所有原始片段。\n\n"

    "**参数语义**：\n"
    "- `time_range_start`: 单段模式起始时间戳（与 `time_range_end` 配合使用）。\n"
    "- `time_range_end`: 单段模式结束时间戳。\n"
    "- `time_ranges`: 聚合模式数组，例如\n"
    "  `[[\"2026-07-12 10:01:00\", \"2026-07-12 10:01:30\"], [\"2026-07-12 10:03:00\", \"2026-07-12 10:03:30\"]]`。\n"
    "  留空字符串表示'最早/末尾'。\n"
    "- `topic_hint`: 话题方向提示（可选）。\n"
    "- `topic_label`: 用户指定的话题标签（可选，优先使用）。\n"
    "- `people`: 关联人物列表，逗号分隔（可选）。\n\n"

    "**单段 vs 聚合**：当一个话题在对话中被其他话题打断、分散成 N 个不连续片段时，"
    "**用聚合模式传 N 个区间**，一次调用即可。"
    "多区间合并为同一条 L1 后，recall_topic 召回时也是 1 次调用把所有片段拉回来。\n\n"

    "**触发条件（严格）**：仅当用户**显式说出归档意图**"
    "（「归档 / 总结 / 转摘要 / 先放一放 / 收尾 / 聊完了 / 这个话题结束」）"
    "才调用本工具。**用户只发话题标记（如「话题1」「话题2」）不算归档指令**——"
    "那是用户在测试对话连通性或标记语义，不是让你归档。\n\n"

    "**三种模式（按优先级）**：\n"
    "1. **话题标签模式（推荐，最简单）**：只传 `topic_label=\"话题1\"`，不传任何时间戳。"
    "工具自动扫描所有未归档 user 消息，匹配含「话题1」的 user，"
    "为每条 user 构造独立区间 `[user.time, user 后第一条 assistant.time]`，"
    "合并为同一条 L1。**无需你手动算时间戳**。\n"
    "2. **聚合模式**：传 `time_ranges=[[\"ts1\", \"ts2\"], [\"ts3\", \"ts4\"]]`，"
    "适用于精确指定离散不连续片段（每片段必须纯单一话题）。\n"
    "3. **单段模式**：传 `time_range_start` + `time_range_end`，"
    "仅适用于**纯单一话题连续区间**。\n\n"

    "**禁止行为（每次回复前自检）**：\n"
    "1) 不要主动识别对话里的话题边界。\n"
    "2) 不要猜测用户「可能想归档什么」。\n"
    "3) 不要在回复里暗示「我看到你提到的话题 X / 话题 Y」。\n"
    "4) **不要主动提议继续归档其他话题** —— 用户没说要归档的话题就不该提议。\n"
    "5) 工具调用成功后只回复一句确认，不要重复摘要细节、不要列 ID、"
    "不要列时间范围、不要问要不要继续。\n\n"

    "**区间纯度硬约束**：单段 / 聚合模式（time_range*）只允许区间内 user 消息"
    "含**同一话题标记**。若区间跨多个不同话题（例如 `[11:07:28, 11:07:39]` "
    "同时含「话题1」「话题2」），工具会拒绝执行并报错 ——"
    "**这是预期行为**，看到错误后立即改用「话题标签模式」传 `topic_label` 即可，"
    "**不要在单段/聚合模式里反复试不同的 time_range 组合**。\n\n"

    "**时间戳读取**：从上下文「近期对话原文」区直接复制 "
    "user / assistant 的 `### [YYYY-MM-DD HH:MM:SS] role` 时间戳字面值，不要估算。\n\n"

    "返回归档后的摘要内容。"
)

ARCHIVE_RECENT_TALK_PARAMS = {
    "time_range_start": "单段模式起始时间戳，格式 `YYYY-MM-DD HH:MM:SS`。",
    "time_range_end": "单段模式结束时间戳，格式 `YYYY-MM-DD HH:MM:SS`。",
    "time_ranges": "聚合模式时间区间数组，每元素 [start, end]。",
    "topic_hint": "话题方向提示（可选）。",
    "topic_label": "用户指定的话题标签（可选，优先使用）。",
    "people": "关联人物列表，逗号分隔（可选）。",
}

RECALL_TOPIC = (
    "召回已归档的话题摘要，将原始对话注入上下文继续讨论。\n\n"

    "**使用场景**：用户说「继续聊之前的话题」「回顾价值本质」「我们上次说的」时调用。\n\n"

    "**效果**：调用后原始对话内容会被注入到上下文，"
    "角色可以「续谈」而非「复述」。聚合归档的多区间也会被一次性全部召回。\n\n"

    "参数（至少传一个）：\n"
    "- `topic_label`: 话题标签**模糊匹配**（如「价值本质」「电影」）。\n"
    "- `topic_id`: **精确匹配**归档 ID（如 `T-20260707-143020`）。\n"
    "- `list_all`: `true` = 列出所有已归档话题（不做召回）。\n\n"

    "**优先级**：`topic_label` > `topic_id` > `list_all`。"
)

RECALL_TOPIC_PARAMS = {
    "topic_label": "话题标签模糊匹配。",
    "topic_id": "精确匹配归档 ID。",
    "list_all": "true 列出所有话题。",
}


# ── 角色管理工具 ─────────────────────────────────────────

CREATE_CHARACTER = (
    "创建新角色。\n\n"

    "参数：\n"
    "- `name`: 角色名（仅含字母/数字/下划线/连字符）。\n"
    "- `system_prompt`: 核心人格定义。\n"
    "- `title`: 头衔（可选，默认与 name 相同）。\n"
    "- `traits`: 特质描述（可选）。\n"
    "- `ipu`: 智能基元短名（可选，默认 `v4-pro`）。\n"
    "- `provider`: 供应商（可选；不传则按 ipu 反查）。\n"
    "- `temperature`: 0-2（可选，默认 1.0）。\n"
    "- `top_p`: 0-1（可选，默认 0.95）。\n"
    "- `thinking_enabled`: true/false（可选，默认 true）。\n"
    "- `reasoning_effort`: high/max（可选，默认 high）。\n\n"

    "注意：`thinking_enabled=false` 时不应设 `reasoning_effort`（互斥）。"
)

CREATE_CHARACTER_PARAMS = {
    "name": "角色名（仅字母数字下划线连字符）。",
    "system_prompt": "核心人格定义。",
    "title": "头衔（可选，默认与 name 相同）。",
    "traits": "特质描述（可选）。",
    "ipu": "智能基元短名（可选，默认 v4-pro）。",
    "provider": "供应商名（可选；不传则按 ipu 反查）。",
    "temperature": "取值区间 [0, 2]（可选，默认 1.0）。",
    "top_p": "取值区间 [0, 1]（可选，默认 0.95）。",
    "max_icp": "单轮最大输出 token（可选，默认 8192）。",
    "thinking_enabled": "true/false（可选，默认 true）。",
    "thinking_mode": "enabled/disabled/auto（可选，默认 auto）。",
    "reasoning_effort": "high/max（可选，默认 high）。",
}

LIST_CHARACTERS = "列出所有已注册角色及其智能基元与描述。"

SEND_TO_CHARACTER = (
    "向目标角色发送消息，触发对方生成回复。\n\n"

    "**重要**：当你根据用户要求，需要与另一个角色对话时，**必须使用此工具**，"
    "不要直接生成角色扮演文本。也不要在用户没有同意的情况下与其他角色沟通。\n\n"

    "调用后：\n"
    "1. 你的消息被转发给 recipient，写入对方对话历史。\n"
    "2. recipient 以角色身份生成回复（实时展示给用户）。\n"
    "3. 回复内容通过返回值返回，供你决定下一步。\n\n"

    "参数：`recipient`（目标角色名）、`message`（消息内容）。\n\n"

    "**注意：每调用一次 = 一轮对话。需要多轮时，等结果返回后再调用一次。**"
)

SEND_TO_CHARACTER_PARAMS = {
    "recipient": "目标角色名（先用 list_characters 查看）。",
    "message": "消息内容（不含 send_to_character 元信息，由工具自动注入）。",
}


# ── 时策工具 ─────────────────────────────────────────────

SHICE_SCHEDULE_ADD = (
    "注册定时任务。当用户要求在未来某个时间执行操作时调用。\n\n"

    "参数：\n"
    "- `timestamps`: 绝对时间戳列表（毫秒）。一次传入所有时间点，不要拆多次调用。"
    "例如「15秒后开始每10秒共3次」→ 传 3 个值 "
    "`[t_sent+15000, t_sent+25000, t_sent+35000]`。\n"
    "- `message`: 触发时要执行的任务内容。\n\n"

    "注意：根据上下文标注的 `t_sent` 自行计算绝对时间戳。"
)

SHICE_SCHEDULE_ADD_PARAMS = {
    "timestamps": "绝对时间戳数组（毫秒）。",
    "message": "触发时要执行的任务内容。",
}

SHICE_SCHEDULE_LIST = "列出所有活跃的时策定时任务及其状态（总数/已触发/剩余）。"

SHICE_SCHEDULE_CANCEL = (
    "取消指定的时策任务。\n"
    "参数 `job_id`：任务 ID（通过 `shice_schedule_list` 获取）。"
)

SHICE_SCHEDULE_CANCEL_PARAMS = {
    "job_id": "任务 ID（通过 shice_schedule_list 获取）。",
}


# ── 系统工具 ─────────────────────────────────────────────

EXECUTE_COMMAND = "执行 shell 命令。结果包含 stdout 和 stderr。"
EXECUTE_COMMAND_PARAMS = {
    "command": "要执行的命令字符串。",
}

WEB_FETCH = "获取网页内容。返回提取后的纯文本（剥离 script/style/标签）。"
WEB_FETCH_PARAMS = {
    "url": "网页地址（http/https）。",
}

WEB_SEARCH = (
    "搜索网页。返回搜索结果的标题、摘要和链接。"
    "使用 DuckDuckGo HTML 接口，无需 API key。"
)
WEB_SEARCH_PARAMS = {
    "query": "搜索关键词。",
    "max_results": "最大结果数（默认 5）。",
}


# ── 索引表（metadata.py 用）───────────────────────────────

ALL_DESCRIPTIONS = {
    "read_file": READ_FILE,
    "write_file": WRITE_FILE,
    "get_directory_tree": GET_DIRECTORY_TREE,
    "search_in_path": SEARCH_IN_PATH,
    "search_in_content": SEARCH_IN_CONTENT,
    "get_file_metadata": GET_FILE_METADATA,
    "update_runtime": UPDATE_RUNTIME,
    "update_identity": UPDATE_IDENTITY,
    "summarize_conversation": SUMMARIZE_CONVERSATION,
    "archive_recent_talk": ARCHIVE_RECENT_TALK,
    "recall_topic": RECALL_TOPIC,
    "create_character": CREATE_CHARACTER,
    "list_characters": LIST_CHARACTERS,
    "send_to_character": SEND_TO_CHARACTER,
    "shice_schedule_add": SHICE_SCHEDULE_ADD,
    "shice_schedule_list": SHICE_SCHEDULE_LIST,
    "shice_schedule_cancel": SHICE_SCHEDULE_CANCEL,
    "execute_command": EXECUTE_COMMAND,
    "web_fetch": WEB_FETCH,
    "web_search": WEB_SEARCH,
}

ALL_PARAM_DESCS = {
    "read_file": READ_FILE_PARAMS,
    "write_file": WRITE_FILE_PARAMS,
    "get_directory_tree": GET_DIRECTORY_TREE_PARAMS,
    "search_in_path": SEARCH_IN_PATH_PARAMS,
    "search_in_content": SEARCH_IN_CONTENT_PARAMS,
    "get_file_metadata": GET_FILE_METADATA_PARAMS,
    "update_runtime": UPDATE_RUNTIME_PARAMS,
    "update_identity": UPDATE_IDENTITY_PARAMS,
    "summarize_conversation": SUMMARIZE_CONVERSATION_PARAMS,
    "archive_recent_talk": ARCHIVE_RECENT_TALK_PARAMS,
    "recall_topic": RECALL_TOPIC_PARAMS,
    "create_character": CREATE_CHARACTER_PARAMS,
    "send_to_character": SEND_TO_CHARACTER_PARAMS,
    "shice_schedule_add": SHICE_SCHEDULE_ADD_PARAMS,
    "shice_schedule_cancel": SHICE_SCHEDULE_CANCEL_PARAMS,
    "execute_command": EXECUTE_COMMAND_PARAMS,
    "web_fetch": WEB_FETCH_PARAMS,
    "web_search": WEB_SEARCH_PARAMS,
}
