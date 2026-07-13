"""工具元数据：把 builtin.py 里的 ToolDef 列表提取到独立文件。

只放声明，不放逻辑实现。所有 handler 仍由 builtin.py 提供。

动态描述（依赖运行时 IPU_REGISTRY）就地生成，其他静态描述从
descriptions.py 引入。
"""
from __future__ import annotations

from typing import Any, Callable

from data_shape import ToolDef
from .descriptions import ALL_DESCRIPTIONS, ALL_PARAM_DESCS


# ── 工具定义 (业务工具) ───────────────────────────────────
# helper: _tool(...) 一行声明一个 ToolDef
# - name: tool 名
# - properties/required: 与 OpenAI function-call schema 一致
# - fn: 注册在 builtin.py 中的实际处理函数；元数据阶段可不填（None 表示由
#       builtin.py 走 _BUILTIN_HANDLERS 调度表分配）
# - param_desc_overrides: 覆盖 descriptions.py 中同名工具的参数描述
#                         （目前所有非动态工具都直接复用全局参数描述）


def _tool(
        name: str, properties: dict[str, Any], required: list[str],
        fn: Callable | None = None, description_override: str | None = None, ) -> ToolDef:
    """一行声明一个 ToolDef，自动注入 description 与各参数 description。"""
    description = description_override or ALL_DESCRIPTIONS[name]

    # 从全局 param_descs 中拷贝参数描述
    enriched_props: dict[str, Any] = {}
    param_desc_map = ALL_PARAM_DESCS.get(name, {})
    for pname, spec in properties.items():
        spec = dict(spec)
        if pname in param_desc_map and "description" not in spec:
            spec["description"] = param_desc_map[pname]
        enriched_props[pname] = spec

    return ToolDef(
        name=name,
        description=description,
        parameters={
            "type": "object", "properties": enriched_props, "required": required, },
        fn=fn, )


# ── 工具表 ─────────────────────────────────────────────
# 本常量在 builtin.py 模块级调用时一次性构建，传入 FILE_TOOLS。

def _runtime_desc() -> str:
    """update_runtime 的动态 description（含运行时可用 IPU 列表）。"""
    try:
        import yinao.ipu_resolver as mr
        model_list_explicit: list[str] = []
        for _, ms in mr.IPU_REGISTRY.items():
            for short_name in ms.keys(): model_list_explicit.append(short_name)
        return (
            "update runtime params. Any combination is supported. "
            f"ipu: short name ONLY. Available: {', '.join(model_list_explicit)}. "
            "temperature: 0-2. top_p: 0-1. max_icp: positive int. "
            "thinking_mode: enabled/disabled/auto. "
            "reasoning_effort: high/max (需 thinking_enabled=true，否则自动开启 thinking)。"
            "thinking_enabled: true/false (关 thinking 时自动清除 reasoning_effort；"
            "开 thinking 时 temperature/top_p 由 DeepSeek API 忽略不生效)。"
        )
    except Exception:
        return "update runtime params: ipu, temperature, top_p, max_icp, thinking_mode"


def _identity_desc() -> str:
    """update_identity 的 description（纯静态，但保持与 _runtime_desc 对齐的接口形态）。"""
    return (
        "update identity config. system_prompt: core personality. "
        "title: title/position. traits: trait description. "
        "max_iterations: max reasoning iterations (positive int)."
    )


# 实际构造列表（导入 / 注册时被 builtin.py 调用一次）
def build_tool_defs() -> list[ToolDef]:
    return [
        # ── 文件工具 ──
        _tool("read_file", {"path": {"type": "string"},
                            "line_range": {"type": "string"}}, ["path"]),
        _tool("write_file", {"path": {"type": "string"}, "content": {"type": "string"},
                             "mode": {"type": "string", "default": "w"}}, ["path", "content"]),
        _tool(
            "get_directory_tree", {"path": {"type": "string"},
                                   "depth": {"type": "integer", "default": 1},
                                   "recursive": {"type": "boolean", "default": False},
                                   "max_entries": {"type": "integer", "default": 500}}, []),
        _tool("search_in_path", {"pattern": {"type": "string"}, "path": {"type": "string"}}, ["pattern"]),
        _tool("search_in_content", {"pattern": {"type": "string"}, "path": {"type": "string"},
                                    "case_insensitive": {"type": "boolean"}, "max_results": {"type": "integer"}},
            ["pattern"]),
        _tool("get_file_metadata", {"path": {"type": "string"}}, ["path"]),

        # ── 自手术工具（动态 description） ──
        _tool("update_runtime",
            {"ipu": {"type": "string"},
             "temperature": {"type": "number"},
             "top_p": {"type": "number"},
             "max_icp": {"type": "integer"},
             "thinking_mode": {"type": "string"},
             "reasoning_effort": {"type": "string"},
             "thinking_enabled": {"type": "boolean"}},
            [],
            description_override=_runtime_desc()),
        _tool("update_identity",
            {"system_prompt": {"type": "string"},
             "title": {"type": "string"},
             "traits": {"type": "string"},
             "max_iterations": {"type": "integer"}},
            [],
            description_override=_identity_desc()),

        # ── 历史归档/召回工具 ──
        _tool("summarize_conversation", {"keep_recent_turns": {"type": "integer"},
                                         "topic": {"type": "string"}}, []),
        _tool("archive_recent_talk",
            {"time_range_start": {"type": "string"},
             "time_range_end": {"type": "string"},
             "time_ranges": {
                 "type": "array",
                 "items": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2, },
                 "description": "聚合模式时间区间数组，每元素 [start, end]",
             },
             "topic_hint": {"type": "string"},
             "topic_label": {"type": "string"},
             "people": {"type": "string"}},
            []),
        _tool("recall_topic", {"topic_label": {"type": "string"},
                               "topic_id": {"type": "string"}, "list_all": {"type": "boolean"}}, []),

        # ── 角色管理 ──
        _tool("create_character",
            {"name": {"type": "string"},
             "system_prompt": {"type": "string"},
             "title": {"type": "string"},
             "traits": {"type": "string"},
             "ipu": {"type": "string"},
             "provider": {"type": "string"},
             "temperature": {"type": "number"},
             "top_p": {"type": "number"},
             "max_icp": {"type": "integer"},
             "thinking_enabled": {"type": "boolean"},
             "thinking_mode": {"type": "string"},
             "reasoning_effort": {"type": "string"}},
            ["name", "system_prompt"]),
        _tool("list_characters", {}, []),
        _tool("send_to_character",
            {"recipient": {"type": "string"},
             "message": {"type": "string"}},
            ["recipient", "message"]),

        # ── 时策工具 ──
        _tool("shice_schedule_add", {"timestamps": {"type": "array", "items": {"type": "integer"}},
                                     "message": {"type": "string"}}, ["timestamps", "message"]),
        _tool("shice_schedule_list", {}, []),
        _tool("shice_schedule_cancel", {"job_id": {"type": "string"}}, ["job_id"]),

        # ── 系统工具 ──
        _tool("execute_command", {"command": {"type": "string"}}, ["command"]),
        _tool("web_fetch", {"url": {"type": "string"}}, ["url"]),
        _tool("web_search", {"query": {"type": "string"},
                             "max_results": {"type": "integer"}}, ["query"]),
    ]
