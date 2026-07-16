"""
@actor_tool — 角色侧旁路小模型调用框架

与普通 @tool 的关键区别：
- 固定 pipeline：单次 API 调用，不允许 tool chain
- 结构化输出：output_schema 约束返回 JSON
- 不进 history：不追加 tool_call / tool_result 到对话消息
- 独立 system prompt：与 worker 模型完全隔离

本文件还集中注册了"经验模块"用到的两个 LLM 工具：
    _summarize_conversation   对话分段（auto L1）
    _summarize_topic          话题提炼（manual 归档）
它们的提示词独立为模块级常量 SUMMARIZE_CONVERSATION_SYSTEM / SUMMARIZE_TOPIC_SYSTEM，
方便单测修改 / 版本对比。
"""

from __future__ import annotations

from typing import Callable

_registry: dict[str, dict] = {}
_executor: Callable | None = None


# ═══════════════════════════════════════════════════════════════════
# 经验模块 — 旁路 LLM 工具的提示词常量
# ═══════════════════════════════════════════════════════════════════

SUMMARIZE_CONVERSATION_SYSTEM = (
        "你是对话分段摘要器。阅读带 [msg:N] 标记的对话记录，按话题变化切分为多个段。\n\n"
        "## 分段原则\n"
        "1. 话题明显变化时切分（新任务、新方向、新发现、方法论转折）\n"
        "2. 每段必须有**独立的认知价值**——如果两段信息完全重复，合并为一段\n"
        "3. 碎片/闲聊可合并到相邻话题段，不必单独拆出\n"
        "4. 最少 3 段，最多 12 段。宁可多拆也不要丢信息\n\n"
        "## 每段的 detail 必须包含\n"
        "- 做了什么 → 用了什么方法 → 得到了什么结论\n"
        "- 如果有具体数值（通过率、参数值、错误类型），写进去\n"
        "- 如果有命名实体（角色名、智能基元简称、文件名），写进去\n"
        "- 禁止「进行了测试」「讨论了配置」这类空泛说法\n\n"
        "## 正确 vs 错误示例\n"
        "✅「创建角色小高配合完成 15 个工具测试，通过率 93.3%（14/15），仅 web_search 超时未通过」\n"
        "✅「用户提供照片测试 vision 能力。actor 发现角色配置标注千问3.6+但实际引擎为 MiniMax-M2.7——角色配置≠实际引擎」\n"
        "❌「共 15 轮对话，涉及引擎切换 + 图片理解」\n"
        "❌「用户要求测试配置修改，actor 进行了修改和验证」\n\n"
        "## 硬约束\n"
        "1. from_msg/to_msg 必须是消息的 [msg:N] 编号，精确读取\n"
        "2. topic: 15 字以内\n"
        "3. 禁止出现「共 N 轮」「涉及: X, Y, Z」等模板化表述\n"
        "4. 必须覆盖全部可见轮次，不允许遗漏\n"
        "5. 最终输出必须是纯 JSON 数组，以 [ 开头，不要加任何 markdown 标签或解释文字\n"
        "6. ⚠️ detail 中避免使用英文双引号 \"，用中文引号「」代替，否则 JSON 解析会失败\n"
)

SUMMARIZE_TOPIC_SYSTEM = (
        "你是话题提炼师。阅读带 [msg:N] 标记的对话记录，提炼出一个话题的语义摘要。\n\n"
        "## 提炼要求\n"
        "1. topic_label：给这个话题起一个 10 字以内的标签（如「价值本质」「电影推荐」「项目架构」），"
        "   优先使用对话中已出现的关键词\n"
        "2. people：**只识别对话中直接参与发言的角色名**（通过 send_to_character 调用的目标角色、或对话对象）；"
        "对话中**被提及**但不是直接参与者的角色不应写入（如「像之前和 XX 讨论时那样」这类引用不算）。返回这些角色名列表；无特定人物则返回空数组\n"
        "3. summary：用 2-4 句话综合这段对话的核心结论，不要复述细节，要提炼洞察\n"
        "4. key_points：列出 2-5 个关键观点，每个 20 字以内，用中文句号结尾\n\n"
        "## 正确示例\n"
        "topic_label: 价值本质\n"
        "people: [张三, 李四]\n"
        "summary: 讨论了价值的本质是主观还是客观。认为价值既非纯粹主观也非纯粹客观，而是主体与客体交互过程中涌现的属性。\n"
        "key_points: [价值是主体-客体交互的涌现属性。, 演化心理学视角：价值是为了生存和繁衍的适应机制。, 主观主义认为价值完全取决于个体偏好。]\n\n"
        "## 硬约束\n"
        "1. from_msg/to_msg 精确对应 [msg:N] 编号，不可遗漏\n"
        "2. 最终输出必须是纯 JSON 对象，以 { 开头，不要加任何 markdown 标签\n"
        "3. ⚠️ 所有字符串中避免使用英文双引号 \"，用中文引号「」代替\n"
)


def set_actor_executor(fn: Callable):
    """注入 API 执行器（由 app.py 在启动时调用）。"""
    global _executor
    _executor = fn


def actor_tool(*, ipu: str, output_schema: dict[str, str], system: str, ):
    """装饰器：将函数标记为旁路智能基元调用工具。

    调用时 → 组 system + user prompt → 单次 API → JSON 解析 → 返回 dict。

    示例:
        @actor_tool(
            ipu="qwen-turbo",
            output_schema={"topic": "str", "detail": "str"},
            system="你是对话摘要器。输出 JSON。"
        )
        def summarize(messages: str) -> dict:
            pass  # 函数体不执行，由装饰器替换
    """

    def decorator(fn: Callable):
        name = fn.__name__
        _registry[name] = {
            "ipu": ipu, "output_schema": output_schema, "system": system, "fn": fn, }

        async def wrapper(**kwargs) -> dict:
            if not _executor:
                raise RuntimeError(
                    f"@actor_tool '{name}' called before executor is set. "
                    "Call set_actor_executor() at startup.")
            user_text = "\n".join(f"{k}:\n{v}" for k, v in kwargs.items())
            return await _executor(
                ipu=ipu, system_prompt=system,
                user_message=user_text, output_schema=output_schema, )

        wrapper.__name__ = name
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator


def list_actor_tools() -> dict[str, dict]:
    """返回已注册的 @actor_tool 列表（名称 → 元数据）。"""
    return dict(_registry)


# ═══════════════════════════════════════════════════════════════════
# 经验模块 — 旁路 LLM 工具（auto L1 / manual 归档共用）
# ═══════════════════════════════════════════════════════════════════

@actor_tool(
    ipu="v4-flash",
    output_schema={
        "segments": "array of {from_msg: int, to_msg: int, topic: string, detail: string}"
    },
    system=SUMMARIZE_CONVERSATION_SYSTEM,
)
async def _summarize_conversation(conversation_text: str) -> dict:
    """Auto-invoked by @actor_tool — 返回 {"segments": [{from_msg, to_msg, topic, detail}, ...]}"""
    pass


@actor_tool(
    ipu="v4-flash",
    output_schema={
        "topic_label": "string",
        "people": "array of string",
        "summary": "string",
        "key_points": "array of string"
    },
    system=SUMMARIZE_TOPIC_SYSTEM,
)
async def _summarize_topic(conversation_text: str) -> dict:
    """Auto-invoked by @actor_tool — 返回 {topic_label, people, summary, key_points}"""
    pass
