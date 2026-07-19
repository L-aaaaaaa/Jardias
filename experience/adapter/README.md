# experience/adapter — 触发原因适配层

把"按触发原因组织的业务逻辑"从 IO 层抽出来，每个文件对应一类触发原因：

```
adapter/
├── __init__.py          # 本 README（同时作为模块入口）
├── init.py              # 初始化：角色注册、引擎切换
├── conversation.py      # 正常对话：用户输入、上下文注入、轮次完成
└── archive_recall.py    # 归档/召回
```

## 设计原则

1. **适配层知道"为什么"**，IO 层知道"怎么写"，触发层知道"何时调用"
2. **业务逻辑只允许出现在适配层**：summary 合并、covered 过滤、dump_meta 跟踪
3. **块2 字符串模板只在 writer 层**：禁止在适配层硬编码 `# 历史\n\n## 摘要` 等
4. **触发层不允许直接调 writer**：必须经过本适配层

## 每个 adapter 的触发时机

### init.py
```python
from experience.adapter.init import on_register, on_ipu_switch

# 角色注册时（新角色创建）
on_register(character_name, config)

# 引擎切换时（auto-switch / LLM requested / manual）
on_ipu_switch(character_name, config)
```

**写入**：块0（角色身份）+ 块1（状态占位）+ 块2（空骨架，让 dump 时自动建）

### conversation.py
```python
from experience.adapter.conversation import (
    on_user_input, on_inject_context, on_round_complete
)

# 用户输入时
on_user_input(character_name, user_input)

# 注入上下文时（读全 4 块 → 构建 messages）
messages = on_inject_context(
    config, character_name, user_input,
    image_url=image_url, switch_note=switch_note,
    round_context=round_context,
)

# 轮次完成时（dump）
updated_meta = on_round_complete(
    character_name, new_messages, meta=current_meta
)
```

**写入**：块3 / 读全4 → messages / 块2 追加 + 块3 清空

### archive_recall.py
```python
from experience.adapter.archive_recall import on_archive, on_recall

# 归档时（LLM 调 archive_recent_talk）
on_archive(
    character_name,
    messages=history_msgs,
    summary_entry=summary_entry,
    visible_msgs=visible_msgs,
    physical_total=len(history_msgs),
)

# 召回时（LLM 调 recall_topic）
on_recall(character_name, topic_id, recall_block)  # 实际是 no-op
```

**写入**：块2 重写（合并摘要 + filtered recent）+ 更新 `_dump_meta.written_len`

## 如何新增触发原因

假设要加"导入对话历史"功能：

1. 新建 `experience/adapter/import_history.py`
2. 定义 `on_import(character_name, history_file, ...)` 函数
3. 内部用 `writer.write_block2_append` 等按块 API
4. 触发层（CLI 命令）调 `from experience.adapter.import_history import on_import`

不需要改：
- writer 层（已有的按块 API 足够）
- reader 层
- 其他 adapter 文件

## 与三层架构的对应关系

见 `doc/adr/0003-experience三层架构.md`