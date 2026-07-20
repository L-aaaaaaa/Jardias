"""
character_menu.py — 终端交互式角色选择/创建菜单。
基于 Jardias0 的 character_menu.py 适配。
支持中英文国际化。
"""
from __future__ import annotations

from character.registry import registry
from common.cli_output import separator_to_terminal
from common.i18n import (
    t, get_lang, toggle_lang, tr_menu_title, tr_existing_chars,
    tr_no_chars, tr_create_new, tr_exit, tr_switch_lang, tr_select_prompt,
    tr_name_input, tr_traits_input, tr_title_input, tr_preset_title,
    tr_default_ai, tr_code_asst, tr_custom_opt, tr_select_preset,
    tr_custom_prompt_input, tr_engine_title, tr_custom_engine, tr_select_engine,
    tr_provider_input, tr_ipu_input, tr_invalid_engine, tr_name_empty,
    tr_invalid_choice, tr_invalid_input, tr_created, tr_unconfigured,
    tr_no_description
)


def print_character_menu(character_list: list[str]) -> None:
    """打印角色选择菜单。"""
    separator_to_terminal("—", 20)
    print(tr_menu_title())

    if character_list:
        print("\n" + tr_existing_chars())
        for i, name in enumerate(character_list):
            try:
                config = registry.get_config(name)
                title = config.identity.title or tr_unconfigured()
                traits = config.identity.traits or tr_no_description()
                prov = config.runtime.provider
                ipu = config.runtime.ipu
                print(f"  [{i + 1}] {name} — {title} ({prov}/{ipu})")
                if traits != tr_no_description():
                    print(f"      {traits}")
            except Exception:
                print(f"  [{i + 1}] {name}")
    else:
        print("\n" + tr_no_chars())

    print("\n  " + tr_create_new())
    print("  " + tr_exit())
    print("  [L] 切换语言 / Switch Language")
    separator_to_terminal("—", 20)


def _build_engine_menu() -> tuple[list[str], dict[int, tuple[str, str]]]:
    """
    \u4ece IPU_REGISTRY \u52a8\u6001\u6784\u5efa\u667a\u80fd\u57fa\u5143\u9009\u62e9\u83dc\u5355\u3002
    返回 (显示行列表, {序号: (provider, model_short)})。
    """
    from yinao.launcher.config_resolver import IPU_REGISTRY
    display_lines: list[str] = []
    idx_map: dict[int, tuple[str, str]] = {}
    i = 1
    for prov_name, models in IPU_REGISTRY.items():
        for short_name, full_name in models.items():
            display_lines.append(f"{prov_name} / {short_name} ({full_name})")
            idx_map[i] = (prov_name, short_name)
            i += 1
    return display_lines, idx_map


def select_or_create_character() -> tuple[str, str, str] | None:
    """
    显示角色选择菜单，返回 (角色名, provider, ipu) 或 None（退出）。

    返回的是角色显示名，直接传给 bootstrap()。
    """
    while True:
        character_list = registry.scan()
        print()
        print_character_menu(character_list)

        choice = input("\n请选择 / Select [数字/number/N/Q/L]: ").strip().upper()

        # 语言切换
        if choice == "L":
            new_lang = toggle_lang()
            lang_name = "中文" if new_lang == "zh" else "English"
            print(f"\n>>> Language switched to: {lang_name}")
            continue

        if choice == "Q" and character_list:
            return None

        if choice == "N":
            name = input("\n" + tr_name_input()).strip()
            if not name:
                print(tr_name_empty())
                continue

            traits = input(tr_traits_input()).strip()
            title = input(tr_title_input()).strip()

            print("\n" + tr_preset_title())
            print("  " + tr_default_ai())
            print("  " + tr_code_asst())
            print("  " + tr_custom_opt())
            prompt_choice = input(tr_select_preset()).strip()

            if prompt_choice == "2":
                system_prompt = (
                    "你是一个专业的编程助手。擅长 Python、TypeScript、系统架构。\n"
                    "写干净高效的代码，解释技术概念清晰，遵循最佳实践。"
                    if get_lang() == "zh"
                    else "You are a professional programming assistant. Skilled in Python, TypeScript, system architecture.\n"
                    "Write clean and efficient code, explain technical concepts clearly, follow best practices."
                )
            elif prompt_choice == "3":
                system_prompt = input(tr_custom_prompt_input()).strip()
            else:
                system_prompt = "你是一个智能体角色，名字叫 #{character_name}。" if get_lang() == "zh" else "You are an agent character, your name is #{character_name}."

            # ── 动态引擎选择（从 ipu_config.json 自动同步）──
            engine_display, engine_map = _build_engine_menu()
            print("\n" + tr_engine_title())
            for i, display in enumerate(engine_display):
                print(f"  [{i + 1}] {display}")
            custom_idx = len(engine_display) + 1
            print("  " + tr_custom_engine(i=custom_idx))

            engine_choice = input(tr_select_engine().replace("{i}", str(custom_idx))).strip()

            if engine_choice.isdigit():
                idx = int(engine_choice)
                if 1 <= idx <= len(engine_display):
                    provider, ipu = engine_map[idx]
                elif idx == custom_idx:
                    provider = input(tr_provider_input()).strip()
                    ipu = input(tr_ipu_input()).strip()
                else:
                    print(tr_invalid_engine())
                    provider, ipu = "minimax", "2.7快"
            else:
                provider, ipu = "minimax", "2.7快"

            from data_shape import ActorConfig, RoleConfig, IPURuntime
            config = ActorConfig(
                identity=RoleConfig(
                    system_prompt=system_prompt,
                    title=title,
                    traits=traits,
                ),
                runtime=IPURuntime(
                    provider=provider,
                    ipu=ipu,
                ),
            )
            registry.create(name, config)
            print("\n" + tr_created(name, provider, ipu))
            return (name, provider, ipu)

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(character_list):
                name = character_list[idx]
                try:
                    config = registry.get_config(name)
                    provider = config.runtime.provider
                    ipu = config.runtime.ipu
                except Exception:
                    provider, ipu = "minimax", "2.7快"
                return (name, provider, ipu)
            print(tr_invalid_choice(n=len(character_list)))
        else:
            print(tr_invalid_input())
