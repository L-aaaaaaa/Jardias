"""builtin_tools/config — update_runtime / update_identity 工具实现。

依赖的调度层符号（_current_actor / _format_error / _apply_field / 等）
在 builtin.py 中定义。函数体内延迟 import 避免循环引用（builtin.py
模块体导入本模块时，调度层符号尚未绑定到模块对象）。
"""
from __future__ import annotations

from data_shape import UpdateRuntimeArgs


def update_runtime(arguments: dict) -> str:
    """更新运行时智能基元参数（ipu/temperature/top_p/max_icp/thinking_mode 任意组合）。
    如果 ipu 变了 → 抛 ModelSwitched 让 app.py 重建 client。
    其他参数 → 直接写 JSON，下轮生效。
    """
    from tool.builtin import _current_actor, _apply_field, _format_circuit_error, _format_validation_error
    from character.config_io import load_config, save_config
    from yinao import IPU_REGISTRY
    from yinao.ipu_client.ipu_context import (
        get_active_ipu, is_provider_available, request_switch, resolve_ipu_provider, )

    # ── 解析参数（pydantic 自动做类型/范围/枚举校验）──
    try:
        args = UpdateRuntimeArgs(**(arguments or {}))
    except Exception as e:
        return _format_validation_error(e, "update_runtime")

    config = load_config(_current_actor);
    rt = config.runtime
    actual_ipu = get_active_ipu()  # 实际运行引擎（fallback 后可能与文件不同）
    changes: list[str] = [];
    ipu_changed = False

    # ── ipu 切换（含熔断检查）──
    if args.has("ipu") and (args.ipu != rt.ipu or (actual_ipu and args.ipu != actual_ipu)):
        provider = resolve_ipu_provider(args.ipu)
        if provider and not is_provider_available(provider): return _format_circuit_error(provider)
        ipu_changed = _apply_field(args, rt, "ipu", changes)

    _apply_field(args, rt, "temperature", changes)
    _apply_field(args, rt, "top_p", changes)
    _apply_field(args, rt, "max_icp", changes)

    # thinking_enabled: 关闭时清空 reasoning_effort（DeepSeek 400 防呆）
    if args.has("thinking_enabled"):
        _apply_field(args, rt, "thinking_enabled", changes)
        if not args.thinking_enabled and rt.reasoning_effort:
            old = rt.reasoning_effort
            log_value = f"reasoning_effort=(自动清除 {old}，关闭 thinking 时不可设 reasoning_effort)"
            _apply_field(args, rt, "reasoning_effort", changes, value="", log_value=log_value)

    # reasoning_effort: 开启时自动开 thinking（DeepSeek 400 防呆）
    if args.has("reasoning_effort") and not rt.thinking_enabled:
        log_value = "thinking_enabled=(自动开启，reasoning_effort 需 thinking 支持)"
        _apply_field(args, rt, "thinking_enabled", changes, value=True, log_value=log_value)
    _apply_field(args, rt, "reasoning_effort", changes)
    _apply_field(args, rt, "thinking_mode", changes)
    if not changes: return "[OK] no changes (all values match current)"
    save_config(config, _current_actor)
    if not ipu_changed:
        return f"[OK] runtime updated: {', '.join(changes)}"
    provider = resolve_ipu_provider(rt.ipu)
    error_hint = f"[Error] 无法解析智能基元 '{rt.ipu}' 的供应商。可用智能基元: 2.7快, 2.7, chat, 千问3.6+, kimi 2.5, glm-5, M2.5"
    if provider is None: return error_hint
    if rt.ipu == provider: rt.ipu = next(iter(IPU_REGISTRY[provider].keys()))
    rt.provider = provider;
    save_config(config, _current_actor)
    from common.experience_core import sync_experience_system_block
    sync_experience_system_block(config, _current_actor)
    request_switch(provider, rt.ipu)
    success_hint = f"[OK] runtime updated: {', '.join(changes)} → 将切换至 {provider}/{rt.ipu}"
    return success_hint


def update_identity(arguments: dict) -> str:
    """更新身份参数（system_prompt/title/traits/max_iterations 任意组合）。
    写 JSON 后下轮生效。 """
    from tool.builtin import _current_actor
    from character.config_io import load_config, save_config

    config = load_config(_current_actor)
    ident = config.identity
    changes = []
    # - parser: arguments[key] 的转换函数（str/int/...）
    # - validator: 校验函数，返回 True 通过；None 表示不校验
    # - log_with_value: True → 变更日志写 "key=value"，False → 只写 "key"
    field_specs = (  # 元组结构：(argument_key, dataclass_attr, parser, validator, log_with_value)
        ("system_prompt", "system_prompt", str, None, False),
        ("title", "title", str, None, True),
        ("traits", "traits", str, None, False),
        ("max_iterations", "max_iterations", int, lambda n: n > 0, True),)
    for key, attr, parser, validator, log_value in field_specs:
        if key not in arguments:  continue
        value = parser(arguments[key])
        if validator and not validator(value): return f"[Error] {key} 校验失败, got {value}"
        setattr(ident, attr, value)
        changes.append(f"{key}={value}" if log_value else key)
    if not changes: return "[OK] no changes"
    save_config(config, _current_actor)
    return f"[OK] identity updated: {', '.join(changes)}"


HANDLERS: dict[str, callable] = {
    "update_runtime": update_runtime,
    "update_identity": update_identity,
}
