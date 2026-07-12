"""
character_menu.py — 终端交互式角色选择/创建菜单。
基于 Jarvis0 的 character_menu.py 适配。
"""
from __future__ import annotations

from character.registry import registry
from common.cli_style import separator_to_terminal


def print_character_menu(character_list: list[str]) -> None:
    """打印角色选择菜单。"""
    separator_to_terminal("—", 20)
    print("角色选择")

    if character_list:
        print("\n已有角色：")
        for i, name in enumerate(character_list):
            try:
                config = registry.get_config(name)
                title = config.identity.title or "(未设置)"
                traits = config.identity.traits or "(无描述)"
                prov = config.runtime.provider
                ipu = config.runtime.ipu
                print(f"  [{i + 1}] {name} — {title} ({prov}/{ipu})")
                if traits != "(无描述)":
                    print(f"      {traits}")
            except Exception:
                print(f"  [{i + 1}] {name}")
    else:
        print("\n暂无角色。")

    print("\n  [N] 创建新角色")
    if character_list:
        print("  [Q] 退出")
    separator_to_terminal("—", 20)


def _build_engine_menu() -> tuple[list[str], dict[int, tuple[str, str]]]:
    """
    \u4ece IPU_REGISTRY \u52a8\u6001\u6784\u5efa\u667a\u80fd\u57fa\u5143\u9009\u62e9\u83dc\u5355\u3002
    返回 (显示行列表, {序号: (provider, model_short)})。
    """
    from yinao.ipu_resolver import IPU_REGISTRY
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

        choice = input("\n请选择 [数字/N/Q]: ").strip().upper()

        if choice == "Q" and character_list:
            return None

        if choice == "N":
            name = input("角色名称: ").strip()
            if not name:
                print("名称不能为空")
                continue

            traits = input("特质描述 (可选): ").strip()
            title = input("头衔 (可选): ").strip()

            print("\n选择预设系统提示：")
            print("  [1] 默认 AI 助手")
            print("  [2] 代码助手")
            print("  [3] 自定义")
            prompt_choice = input("请选择 [1/2/3]: ").strip()

            if prompt_choice == "2":
                system_prompt = (
                    "你是一个专业的编程助手。擅长 Python、TypeScript、系统架构。\n"
                    "写干净高效的代码，解释技术概念清晰，遵循最佳实践。"
                )
            elif prompt_choice == "3":
                system_prompt = input("请输入自定义系统提示: ").strip()
            else:
                system_prompt = "你是一个智能体角色，名字叫 #{character_name}。"

            # ── 动态引擎选择（从 providers.json 自动同步）──
            engine_display, engine_map = _build_engine_menu()
            print("\n选择默认引擎：")
            for i, display in enumerate(engine_display):
                print(f"  [{i + 1}] {display}")
            custom_idx = len(engine_display) + 1
            print(f"  [{custom_idx}] 自定义")

            engine_choice = input(f"请选择 [1-{custom_idx}]: ").strip()

            if engine_choice.isdigit():
                idx = int(engine_choice)
                if 1 <= idx <= len(engine_display):
                    provider, ipu = engine_map[idx]
                elif idx == custom_idx:
                    provider = input("Provider: ").strip()
                    ipu = input("IPU 短名: ").strip()
                else:
                    print("无效选择，使用默认引擎")
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
            print(f"\n角色已创建: {name} ({provider}/{ipu})")
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
            print(f"无效选择，有效范围 1-{len(character_list)}")
        else:
            print("无效输入")
