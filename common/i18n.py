"""
i18n.py — 国际化支持模块。
提供中英文切换和翻译功能。
"""

from typing import Literal

# 当前语言，默认为简体中文
_current_lang: Literal["zh", "en"] = "zh"


def get_lang() -> Literal["zh", "en"]:
    """获取当前语言。"""
    return _current_lang


def set_lang(lang: Literal["zh", "en"]) -> None:
    """设置当前语言。"""
    global _current_lang
    _current_lang = lang


def toggle_lang() -> Literal["zh", "en"]:
    """切换语言，返回切换后的语言。"""
    global _current_lang
    _current_lang = "en" if _current_lang == "zh" else "zh"
    return _current_lang


def _t(key: str) -> str:
    """根据当前语言获取翻译。"""
    return TRANSLATIONS[_current_lang].get(key, key)


# ── 翻译字典 ──────────────────────────────────────────────────────────────

TRANSLATIONS: dict[Literal["zh", "en"], dict[str, str]] = {
    "zh": {
        # 菜单标题
        "menu_title": "角色选择",
        "existing_characters": "已有角色：",
        "no_characters": "暂无角色。",
        "create_new": "[N] 创建新角色",
        "exit": "[Q] 退出",
        "switch_lang": "[L] 切换语言",

        # 输入提示
        "select_prompt": "请选择 [数字/N/Q/L]: ",
        "name_input": "角色名称: ",
        "traits_input": "特质描述 (可选): ",
        "title_input": "头衔 (可选): ",
        "prompt_preset_title": "选择预设系统提示：",
        "default_ai": "[1] 默认 AI 助手",
        "code_assistant": "[2] 代码助手",
        "custom": "[3] 自定义",
        "select_preset_prompt": "请选择 [1/2/3]: ",
        "custom_prompt_input": "请输入自定义系统提示: ",
        "engine_title": "选择默认引擎：",
        "custom_engine": "[{i}] 自定义",
        "select_engine_prompt": "请选择 [1-{i}]: ",
        "provider_input": "Provider: ",
        "ipu_input": "IPU 短名: ",
        "invalid_engine": "无效选择，使用默认引擎",

        # 验证消息
        "name_empty": "名称不能为空",
        "invalid_choice": "无效选择，有效范围 1-{n}",
        "invalid_input": "无效输入",

        # 对话界面
        "current_role": "当前角色: {name} | 引擎: {provider}/{ipu}",
        "quit_switch_hint": "输入 'quit' 退出，输入 'switch' 切换角色",
        "user_input_prompt": "# 【用户输入】：",
        "reasoning": "推理过程",
        "reply": "回复",
        "tool_call": "工具调用",
        "user_input": "【用户输入】：",
        "turn_input_prefix": "  {text}",

        # 智点统计
        "input_tokens": "输入 {n} 智点",
        "reasoning_tokens": "思考 {n} 智点",
        "output_tokens": "输出 {n} 智点",
        "total_tokens": "合计 {n} 智点",
        "this_turn_input": "本轮输入 {n} 智点",
        "this_turn_reasoning": "输出 {n} 智点的思考",
        "this_turn_reply": "{n} 智点的回答",
        "this_turn_total": "合计 {n} 智点",
        "period": "。",
    },
    "en": {
        # Menu title
        "menu_title": "Character Selection",
        "existing_characters": "Existing Characters:",
        "no_characters": "No characters yet.",
        "create_new": "[N] Create New Character",
        "exit": "[Q] Quit",
        "switch_lang": "[L] 切换语言 / Switch Language",

        # Input prompts
        "select_prompt": "Select [number/N/Q/L]: ",
        "name_input": "Character Name: ",
        "traits_input": "Traits Description (optional): ",
        "title_input": "Title (optional): ",
        "prompt_preset_title": "Select System Prompt Preset:",
        "default_ai": "[1] Default AI Assistant",
        "code_assistant": "[2] Code Assistant",
        "custom": "[3] Custom",
        "select_preset_prompt": "Select [1/2/3]: ",
        "custom_prompt_input": "Enter custom system prompt: ",
        "engine_title": "Select Default Engine:",
        "custom_engine": "[{i}] Custom",
        "select_engine_prompt": "Select [1-{i}]: ",
        "provider_input": "Provider: ",
        "ipu_input": "IPU Short Name: ",
        "invalid_engine": "Invalid choice, using default engine",

        # Validation messages
        "name_empty": "Name cannot be empty",
        "invalid_choice": "Invalid choice, valid range 1-{n}",
        "invalid_input": "Invalid input",

        # Status messages
        "created": "Character created: {name} ({provider}/{ipu})",
        "unconfigured": "(not set)",
        "no_description": "(no description)",

        # Dialog interface
        "current_role": "Current Role: {name} | Engine: {provider}/{ipu}",
        "quit_switch_hint": "Type 'quit' to exit, type 'switch' to switch characters",
        "user_input_prompt": "# 【User Input】：",
        "reasoning": "Reasoning",
        "reply": "Reply",
        "tool_call": "Tool Call",
        "user_input": "【User Input】：",
        "turn_input_prefix": "  {text}",

        # Token statistics
        "input_tokens": "Input {n} tokens",
        "reasoning_tokens": "Reasoning {n} tokens",
        "output_tokens": "Output {n} tokens",
        "total_tokens": "Total {n} tokens",
        "this_turn_input": "This turn input {n} tokens",
        "this_turn_reasoning": "Output {n} tokens reasoning",
        "this_turn_reply": "{n} tokens reply",
        "this_turn_total": "Total {n} tokens",
        "period": ".",
    },
}


# ── 便捷翻译函数 ──────────────────────────────────────────────────────────

def t(key: str, **kwargs) -> str:
    """
    获取翻译后的字符串。

    用法:
        t("menu_title")           # "角色选择" 或 "Character Selection"
        t("invalid_choice", n=5)  # "无效选择，有效范围 1-5"
    """
    template = _t(key)
    if kwargs:
        return template.format(**kwargs)
    return template


# 预定义常量，方便直接引用
def tr_menu_title() -> str: return t("menu_title")


def tr_existing_chars() -> str: return t("existing_characters")


def tr_no_chars() -> str: return t("no_characters")


def tr_create_new() -> str: return t("create_new")


def tr_exit() -> str: return t("exit")


def tr_switch_lang() -> str: return t("switch_lang")


def tr_select_prompt() -> str: return t("select_prompt")


def tr_name_input() -> str: return t("name_input")


def tr_traits_input() -> str: return t("traits_input")


def tr_title_input() -> str: return t("title_input")


def tr_preset_title() -> str: return t("prompt_preset_title")


def tr_default_ai() -> str: return t("default_ai")


def tr_code_asst() -> str: return t("code_assistant")


def tr_custom_opt() -> str: return t("custom")


def tr_select_preset() -> str: return t("select_preset_prompt")


def tr_custom_prompt_input() -> str: return t("custom_prompt_input")


def tr_engine_title() -> str: return t("engine_title")


def tr_custom_engine(i: int) -> str: return t("custom_engine", i=i)


def tr_select_engine() -> str: return t("select_engine_prompt")


def tr_provider_input() -> str: return t("provider_input")


def tr_ipu_input() -> str: return t("ipu_input")


def tr_invalid_engine() -> str: return t("invalid_engine")


def tr_name_empty() -> str: return t("name_empty")


def tr_invalid_choice(n: int) -> str: return t("invalid_choice", n=n)


def tr_invalid_input() -> str: return t("invalid_input")


def tr_created(name: str, provider: str, ipu: str) -> str: return t("created", name=name, provider=provider, ipu=ipu)


def tr_unconfigured() -> str: return t("unconfigured")


def tr_no_description() -> str: return t("no_description")


def tr_current_role(name: str, provider: str, ipu: str) -> str: return t("current_role", name=name, provider=provider,
    ipu=ipu)


def tr_quit_switch_hint() -> str: return t("quit_switch_hint")


def tr_user_input_prompt() -> str: return t("user_input_prompt")


def tr_reasoning() -> str: return t("reasoning")


def tr_reply() -> str: return t("reply")


def tr_tool_call() -> str: return t("tool_call")


def tr_user_input() -> str: return t("user_input")


def tr_turn_input_prefix(text: str) -> str: return t("turn_input_prefix", text=text)
