"""experience.adapter — 触发原因适配层。

把"按触发原因组织的业务逻辑"从读写层抽出来，每个文件对应一类触发原因：

    init.py            角色注册 / 引擎切换 → 写块0/1/2
    conversation.py    正常对话：用户输入 / 注入上下文 / 轮次完成
    archive_recall.py  归档 / 召回

设计原则：
    - 适配层知道"为什么"，IO 层知道"怎么写"，触发层知道"何时调用"
    - 业务逻辑（如 summary 合并、covered 过滤）只允许出现在适配层
    - 块2 字符串模板只在 writer 层
"""