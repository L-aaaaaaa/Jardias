"""tool/descriptions.py + tool/metadata.py 的元数据完整性。

目标：保证 LLM 看到的工具描述永远不为空、参数描述始终存在、跨模块索引表一致。
"""
from __future__ import annotations

import pytest

from tool import builtin
from tool.descriptions import ALL_DESCRIPTIONS, ALL_PARAM_DESCS
from tool.metadata import build_tool_defs


# ── descriptions.py 内部完整性 ─────────────────────────────────

class TestDescriptionIndices:
    def test_two_indices_match(self):
        """ALL_PARAM_DESCS 是 ALL_DESCRIPTIONS 的子集（含必须的工具）。"""
        desc_names = set(ALL_DESCRIPTIONS.keys())
        param_names = set(ALL_PARAM_DESCS.keys())
        # 允许 param 集合略小（无参数工具不需登记），但 desc 必须覆盖每个
        for name in param_names:
            assert name in desc_names, f"{name} 在 param 集合但不在 desc 集合"

    def test_all_descriptions_non_empty(self):
        """每个工具的 description 必须非空字符串。"""
        for name, desc in ALL_DESCRIPTIONS.items():
            assert isinstance(desc, str) and desc.strip(), (
                f"{name} 的 description 为空")

    def test_required_params_have_descriptions(self):
        """ALL_PARAM_DESCS 中每个工具的参数描述必须非空。"""
        for tool_name, param_descs in ALL_PARAM_DESCS.items():
            for pname, desc in param_descs.items():
                assert isinstance(desc, str) and desc.strip(), (
                    f"{tool_name}.{pname} 的参数描述为空")


# ── metadata.py 输出完整性 ──────────────────────────────────

class TestBuildToolDefs:
    def test_count(self):
        """每个内置工具都应有定义。"""
        defs = build_tool_defs()
        assert len(defs) >= 19  # 19 个内置 + 留出冗余

    def test_names_unique(self):
        names = [t.name for t in build_tool_defs()]
        assert len(names) == len(set(names)), f"重名: {names}"

    def test_description_inherited(self):
        """description 应来自 ALL_DESCRIPTIONS，或由 metadata 内动态拼装。"""
        for tool in build_tool_defs():
            # 动态版（运行时拼装）允许覆盖 ALL_DESCRIPTIONS
            assert tool.description, f"{tool.name} description 为空"
            if tool.description != ALL_DESCRIPTIONS[tool.name]:
                # 动态版必须含原版的关键主题词
                original = ALL_DESCRIPTIONS[tool.name]
                # 至少含一个核心关键词
                keywords = ("ipu", "thinking", "system_prompt", "identity")
                assert any(k in tool.description for k in keywords), (
                    f"{tool.name} 动态 description 缺失关键关键词")

    def test_param_descriptions_injected(self):
        """所有必填参数都必须有 description。"""
        for tool in build_tool_defs():
            schema = tool.parameters
            required = set(schema.get("required", []))
            props = schema["properties"]
            for pname in required:
                assert "description" in props[pname], (
                    f"{tool.name}.{pname} 缺失 description")


# ── builtin.py 注册一致性 ──────────────────────────────────

class TestBuiltinRegistry:
    def test_file_tools_built(self):
        """FILE_TOOLS 应含全部内置工具（import 时已 register_file_tools）。"""
        # builtin.py 顶层调用 tools.register_file_tools() 自动填充 FILE_TOOLS
        names = {t.name for t in builtin.FILE_TOOLS}

        # 校验关键工具都注册上
        critical = {"read_file", "write_file", "update_runtime",
                    "archive_recent_talk", "send_to_character",
                    "shice_schedule_add", "execute_command", "web_search"}
        for n in critical:
            assert n in names, f"内置工具 {n} 缺失"

    def test_total_alignment(self):
        """FILE_TOOLS 与 ALL_DESCRIPTIONS 包含的工具总数一致。"""
        names = {t.name for t in builtin.FILE_TOOLS}
        desc_names = set(ALL_DESCRIPTIONS.keys())
        assert names == desc_names, (
            f"差异 FILE_TOOLS-DESC: {names - desc_names} vs {desc_names - names}")


# ── LLM 可见的 schema 结构正确 ─────────────────────────

class TestSchemasForLLM:
    def test_every_tool_has_object_schema(self):
        """每个 ToolDef.parameters 都应是标准 JSON Schema object。"""
        for tool in build_tool_defs():
            assert tool.parameters["type"] == "object"
            assert "properties" in tool.parameters
            assert "required" in tool.parameters

    def test_required_params_exist_in_properties(self):
        """required 中的字段必须出现在 properties。"""
        for tool in build_tool_defs():
            schema = tool.parameters
            required = set(schema.get("required", []))
            props = set(schema.get("properties", {}).keys())
            missing = required - props
            assert not missing, f"{tool.name}: required 字段 {missing} 不在 properties"


# ── 每个工具的 description 必须含足够上下文 ──────────────────

class TestDescriptionContentQuality:
    """防止「描述退化到一句话」。"""

    def test_long_tools_have_rich_description(self):
        """archive_recent_talk / send_to_character 必须含多行说明。"""
        for name in ("archive_recent_talk", "send_to_character",
                      "shice_schedule_add", "create_character"):
            desc = ALL_DESCRIPTIONS[name]
            assert "\n" in desc, f"{name} 描述过于简短"
            assert len(desc) >= 100, f"{name} 描述长度 {len(desc)} 偏短"

    def test_self_surgical_tools_mention_互斥(self):
        """自手术类工具应提示 reasoning_effort 与 thinking_enabled 互斥关系。"""
        # update_runtime 描述里出现 "互斥" 或 "自动清除" 等关键词
        rt_desc = ALL_DESCRIPTIONS["update_runtime"]
        assert any(k in rt_desc for k in ("互斥", "自动清除", "自动开启")), (
            "update_runtime 应提示 thinking 互斥关系")

    def test_archive_talk_lists_three_modes(self):
        """archive_recent_talk 必须明确出现「话题标签模式 / 聚合模式 / 单段模式」。"""
        desc = ALL_DESCRIPTIONS["archive_recent_talk"]
        for mode in ("话题标签模式", "聚合模式", "单段模式"):
            assert mode in desc, f"archive_recent_talk 描述缺失 {mode}"
