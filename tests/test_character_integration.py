"""端到端集成测试：创建一个角色 → 让另一个角色调它，看整条链路是否通。

覆盖：
    1. create_character 真正落地所有文件骨架
    2. list_characters 能看到新角色
    3. send_to_character 整条链路：
       - 注册接收者历史（user 消息已写）
       - 调 LLM（mock 掉）
       - 回填接收者历史（assistant 消息）
       - 写入发送者历史（pair）
       - 返回正确格式的回复
       - 切换回发送者

不依赖真实 LLM —— 用 monkeypatch 替换 ``resolve_chat`` 返回固定结果。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tool import builtin
from tool.builtin import tools, _BUILTIN_HANDLERS
from data_shape import ActorConfig, RoleConfig, IPURuntime
from character import get_history_path
from character.registry import registry


# ── 假 LLM 回复 ──────────────────────────────────────────────


class _FakeChatResult:
    def __init__(self, content: str):
        self.messages = [{"role": "assistant", "content": content}]


async def _fake_chat(*args, **kwargs):
    """替代真实 LLM：accept 任意签名，避免跟生产 chat_fn 签名不匹配。"""
    # 从位置/关键字参数里提取 (messages, character_name)
    messages = kwargs.get("messages") or (args[0] if args else [])
    character_name = kwargs.get("character_name", "?")
    last_user = next((m.get("content", "") for m in reversed(messages)
                      if m.get("role") == "user"), "")
    # mock 回显完整内容（不被截断），便于断言
    return _FakeChatResult(f"[{character_name} 应答] {last_user}")


async def _fake_boom_chat(*args, **kwargs):
    """替代 LLM 但总是抛异常。"""
    raise RuntimeError("simulated LLM down")


@pytest.fixture
def patched_llm(monkeypatch):
    """把所有 provider 的 LLM 调用替换成假函数。

    关键：patch ``switch._CHAT_FNS`` 字典（这是 chat_fn 的实际取值源），
    模块属性 monkeypatch 不影响字典已存的旧引用。
    """
    from yinao.ipu_client import ipu_switch as _switch
    monkeypatch.setitem(_switch._CHAT_FNS, "deepseek", _fake_chat)
    monkeypatch.setitem(_switch._CHAT_FNS, "dashscope", _fake_chat)
    monkeypatch.setitem(_switch._CHAT_FNS, "minimax", _fake_chat)


@pytest.fixture
def patched_boom_llm(monkeypatch):
    """所有 provider 都抛异常的假函数。"""
    from yinao.ipu_client import ipu_switch as _switch
    monkeypatch.setitem(_switch._CHAT_FNS, "deepseek", _fake_boom_chat)
    monkeypatch.setitem(_switch._CHAT_FNS, "dashscope", _fake_boom_chat)
    monkeypatch.setitem(_switch._CHAT_FNS, "minimax", _fake_boom_chat)


# ── 创建角色 ──────────────────────────────────────────────────


class TestCreateCharacterEndToEnd:
    """create_character 走完整链路：磁盘上看到所有骨架文件。"""

    @pytest.mark.asyncio
    async def test_create_lands_all_skeleton_files(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        r = await tools.execute("create_character", {
            "name": "bob",
            "system_prompt": "你是 Bob。",
            "title": "助手",
            "traits": "友善",
            "temperature": 0.6,
        })

        assert r.startswith("[OK]")

        # config.json 落地
        config_path = get_history_path("bob").parent / "config.json"
        assert config_path.exists()

        # history.json 落地
        history_path = get_history_path("bob")
        assert history_path.exists()
        assert json.loads(history_path.read_text(encoding="utf-8")) == []

        # experience.md 落地
        exp_path = history_path.parent / "experience.md"
        assert exp_path.exists()

        # summaries/L1 目录存在
        l1_dir = history_path.parent / "summaries" / "L1"
        assert l1_dir.exists()

        # 注册表能查到
        assert registry.exists("bob")
        cfg = registry.get_config("bob")
        assert cfg.identity.title == "助手"
        assert cfg.identity.traits == "友善"
        assert cfg.runtime.temperature == 0.6


# ── 列表 ────────────────────────────────────────────────────


class TestListAfterCreate:
    @pytest.mark.asyncio
    async def test_create_then_list(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers):
        await tools.execute("create_character",
                            {"name": "carol", "system_prompt": "Carol"})
        await tools.execute("create_character",
                            {"name": "dave", "system_prompt": "Dave",
                             "title": "工程师"})

        r = await tools.execute("list_characters", {})

        # 两个都能看到
        assert "carol" in r
        assert "dave" in r
        assert "工程师" in r


# ── 跨角色调用（mock LLM）───────────────────────────────────


class TestSendToCharacter:
    """核心：'A 调用 B 看会不会有问题' 的完整链路。"""

    @pytest.mark.asyncio
    async def test_full_chain_create_send_reply_history(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            patched_llm):
        """A=default → create B='alice' → send_to_character('alice', 'hi') →
        验证 alice 收到、alice 收到 B 的回复、default 收到完整 pair。"""

        # 1. 创建接收者
        await tools.execute("create_character", {
            "name": "alice",
            "system_prompt": "你是 Alice，一个数学老师。",
            "title": "老师",
            "ipu": "2.7",  # 用一个真实存在的 ipu，避免 unknown 错误
        })

        # 2. 发送者 _current_actor 已经是 default（reset_actor）
        assert builtin._current_actor == "default"

        # 3. 调用 send_to_character
        r = await tools.execute(
            "send_to_character",
            {"recipient": "alice", "message": "你好 Alice，给我讲讲勾股"})

        # 4. 返回值包含 mock 的回复
        assert "来自 alice 的回复" in r
        assert "[alice 应答]" in r
        assert "勾股" in r

        # 5. 发送者 _current_actor 已被还原
        assert builtin._current_actor == "default"

        # 6. 接收者历史：最后一条是 assistant 回复
        alice_history = json.loads(
            get_history_path("alice").read_text(encoding="utf-8"))
        assert len(alice_history) >= 2
        # 最后一条是 alice 的回复
        assert alice_history[-1]["role"] == "assistant"
        assert "勾股" in alice_history[-1]["content"]
        # 倒数第二条是 user（来自 default 的消息）
        assert alice_history[-2]["role"] == "user"
        assert "[来自 default 的消息]" in alice_history[-2]["content"]
        assert "勾股" in alice_history[-2]["content"]

        # 7. 发送者历史：写入了完整 pair
        default_history = json.loads(
            get_history_path("default").read_text(encoding="utf-8"))
        assert len(default_history) == 2
        assert default_history[0]["role"] == "user"
        assert "勾股" in default_history[0]["content"]
        assert default_history[1]["role"] == "assistant"
        assert "[alice 应答]" in default_history[1]["content"]

    @pytest.mark.asyncio
    async def test_recipient_not_exists(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            patched_llm):
        """不存在的接收者要返回 [Error]，不抛 Python 异常。"""
        r = await tools.execute(
            "send_to_character",
            {"recipient": "ghost", "message": "hi"})
        assert r.startswith("[Error]")
        assert "ghost" in r

    @pytest.mark.asyncio
    async def test_self_send_skips_sender_history(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            patched_llm):
        """A 调自己时不应写两遍历史（receiver==sender 跳过发送者回写）。"""
        await tools.execute("create_character",
                            {"name": "selfie", "system_prompt": "x"})
        builtin.set_actor("selfie")

        await tools.execute("send_to_character",
                            {"recipient": "selfie", "message": "自语"})

        # selfie 历史应该只一条 pair（user + assistant），不重复
        h = json.loads(
            get_history_path("selfie").read_text(encoding="utf-8"))
        # 接收者写了一次 (user, assistant)，sender 等于 recipient → 跳过
        # 所以只有 2 条（1 pair）
        assert len(h) == 2
        assert h[0]["role"] == "user"
        assert h[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_llm_exception_returns_error(
            self, tmp_workdir: Path, reset_actor, reset_circuit_breakers,
            patched_boom_llm):
        """LLM 抛异常时：所有供应商都失败 → 返回 [Error]，不冒泡 Python 异常。"""
        await tools.execute("create_character",
                            {"name": "bob", "system_prompt": "Bob"})

        r = await tools.execute("send_to_character",
                                {"recipient": "bob", "message": "hi"})

        assert r.startswith("[Error]")
        assert "RuntimeError" in r or "simulated" in r
        # 关键：_current_actor 必须还原
        assert builtin._current_actor == "default"