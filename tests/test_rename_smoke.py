"""test_rename_smoke.py — 重命名后 import 烟雾测试

按 AGENTS.md `rename-residual-scan-and-smoke` 规则设计。
仅做 import 与函数路径触发，不调用实际业务逻辑，
用于秒级发现命名重构后的漏网旧名（特别是函数体内的懒加载 import）。

用法：python tests/test_rename_smoke.py
"""
import sys
import os
import re

sys.path.insert(0, r"E:\Code\AIProjects\Actor01")

PASS = "[OK]"
FAIL = "[FAIL]"
results = []


def check(label, fn):
    try:
        fn()
        results.append((label, True, None))
        print(f"  {PASS} {label}")
    except Exception as e:
        results.append((label, False, f"{type(e).__name__}: {e}"))
        print(f"  {FAIL} {label}: {type(e).__name__}: {e}")


def section(t):
    print(f"\n--- {t} ---")


# ── 入口模块加载 ──
section("入口模块")

def t_entry():
    import app
check("import app", t_entry)


# ── data_shape 顶层类型 ──
section("data_shape 顶层类型（新 schema）")

def t_ds_types():
    from data_shape import (
        ActorConfig, RoleConfig, IPURuntime,
        IPUEntry, IPUProviderConfig, IPUConfigFile,
        IPUConfig, IPUProvider, IPUSwitch,
        ToolCall, RoundOutput, ChatResult, RoundMeta,
        L1Summary, ToolDef, ToolParam,
    )
check("data_shape types", t_ds_types)


# ── yinao 顶层 ──
section("yinao 顶层 API")

def t_yinao():
    from yinao import (
        IPUVendor, DEFAULT_ROLE_PROMPT,
        IPU_REGISTRY, IPU_CAPS,
        get_ipu_capabilities, choose_ipu, choose_ipu_provider, resolve_ipu,
    )
check("yinao top-level", t_yinao)


# ── yinao.ipu_client 顶层 ──
section("yinao.ipu_client 顶层 API")

def t_ipu_client():
    from yinao.ipu_client import (
        form_client, single_completion, form_stream,
        collect_round, replay_deltas, reason_action_loop,
        resolve_chat, sync_config_to_ipu,
        reload_after_switch, make_switch_note,
        _next_provider, _pick_fallback_ipu, _next_vision_provider,
        is_exhausted_error,
    )
check("yinao.ipu_client top-level", t_ipu_client)


# ── yinao.ipu_client.ipu_context 关键函数（最易漏）──
section("yinao.ipu_client.ipu_context 关键函数")

def t_ipu_context():
    from yinao.ipu_client.ipu_context import (
        IPUSwitched, set_active_ipu, get_active_ipu,
        record_ipu_success, record_ipu_failure,
        resolve_ipu_provider, list_ipu_providers, list_ipus,
        get_circuit_status, request_switch, pop_switch,
        set_round_meta, update_cumulative, build_round_context,
    )
check("ipu_context functions", t_ipu_context)


# ── character 全模块 ──
section("character 子模块")

def t_char():
    import character
    from character import (
        get_character_dir, get_config_path, get_history_path,
        get_summaries_dir, ensure_dirs, list_characters,
    )
    from character.config_io import (
        load_config, save_config, init_config, config_to_dict, config_from_dict,
    )
    from character.history import History
    from character.registry import registry
    from character.summarizer import (
        L1Summary, build_l1_context, check_and_compress,
        save_l1, load_all_l1, l1summary_to_dict, l1summary_from_dict,
    )
    from character.character_menu import select_or_create_character
check("character all submodules", t_char)


# ── common 子模块 ──
section("common 子模块")

def t_common():
    import common
    from common import bootstrap, conversation_loop
    from common.context import (
        form_full_context, build_system_message, strip_context_wrapper,
        build_config_context,
    )
    from common.lifecycle import (
        _run_turn, _post_round_async, _do_switch_character,
        extract_reply, _collect_round_meta,
    )
    from common.actor_log import (
        turn_open, turn_input, bootstrap_summary, model_switch,
        format_api_ok,
    )
check("common all submodules", t_common)


# ── tool 全模块 + _BUILTIN_HANDLERS 关键工具名 ──
section("tool 子模块 + _BUILTIN_HANDLERS 关键工具名")

def t_tool():
    import tool
    import tool.builtin
    from tool.builtin import (
        _BUILTIN_HANDLERS, ToolRegistry, tools,
        set_actor, _handle_update_runtime, _handle_update_identity,
        _handle_create_character, _handle_list_characters,
        _handle_send_to_character, _handle_summarize_conversation,
    )
    expected = [
        "update_runtime", "update_identity", "create_character",
        "list_characters", "send_to_character", "summarize_conversation",
        "shice_schedule_add", "shice_schedule_list", "shice_schedule_cancel",
    ]
    for name in expected:
        if name not in _BUILTIN_HANDLERS:
            raise AssertionError(f"missing handler: {name}")
check("tool + _BUILTIN_HANDLERS", t_tool)


# ── media / schedule ──
section("其他子模块")

def t_others():
    import media
    from media.image import (
        detect_image_url, detect_local_image, local_image_to_data_url,
        find_vision_ipu, auto_switch_for_vision,
    )
    import schedule
    from schedule import TemporalScheduler, Schedule, ScheduleParams
check("media + schedule", t_others)


# ── 实例化冒烟 ──
section("实例化冒烟")

def t_instantiate():
    from data_shape import IPURuntime, IPUConfig, IPUProvider, IPUSwitch, IPUEntry
    rt = IPURuntime(provider="deepseek", ipu="v4-flash", max_icp=8192)
    assert rt.ipu == "v4-flash"
    assert rt.max_icp == 8192
    cfg = IPUConfig(ipu="deepseek-v4-flash", max_icp=2048)
    assert cfg.ipu == "deepseek-v4-flash"
    assert cfg.max_icp == 2048
    prov = IPUProvider(api_key="x", base_url="https://x")
    sw = IPUSwitch(provider="deepseek", ipu="v4-pro")
    assert sw.provider == "deepseek"
    e = IPUEntry(id="x", caps=["text"])
check("instantiate new types", t_instantiate)


# ── 静态扫描：旧名残留 ──
section("静态扫描：旧名残留")

SCAN_DIR = r"E:\Code\AIProjects\Actor01"
OLD_NAMES = [
    "AIModelConfig", "AIModelProvider", "ModelSwitch", "ModelEntry",
    "RuntimeConfig", "IdentityConfig",
    "MODEL_NAMES", "MODEL_CAPABILITIES",
    "resolve_model", "choose_model", "sync_config_to_model",
    "list_providers", "list_models",
    "get_actual_model", "record_model_success", "record_model_failure",
]

# 自身和迁移说明文档天然含旧名 —— 跳过
SKIP_FILES = {"test_rename_smoke.py", "test_imports.py", "test_model_resolver.py", "agent_config.py"}


def scan_old_names():
    findings = []
    for root, dirs, files in os.walk(SCAN_DIR):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules")]
        for f in files:
            if not f.endswith(".py"):
                continue
            if f in SKIP_FILES:
                continue
            fp = os.path.join(root, f)
            try:
                with open(fp, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            # 跳过显式 shim（顶部含 DEPRECATED）
            head = text[:500]
            if "DEPRECATED" in head:
                continue
            for name in OLD_NAMES:
                if re.search(r"\b" + re.escape(name) + r"\b", text):
                    findings.append(f"{fp}: {name}")
    if findings:
        raise AssertionError("残留旧名:\n  " + "\n  ".join(findings[:30]))
check("static scan: no old names", scan_old_names)


# ── 汇总 ──
print("\n" + "=" * 60)
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
print(f"  smoke test: {passed}/{total} 通过")
print("=" * 60)
if passed != total:
    print("\n失败项:")
    for label, ok, err in results:
        if not ok:
            print(f"  {label}: {err}")
    sys.exit(1)
sys.exit(0)
