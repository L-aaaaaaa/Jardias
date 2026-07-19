"""Unit tests for yinao/launcher/reply_getter.

注意：这些测试不发起真实 HTTP 请求——通过 FakeOpenAI 类（duck-typed）检查
三个函数是否按 IPUConfig 字段正确映射到 OpenAI 客户端调用参数。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from data_shape import IPUConfig, IPUProvider
from yinao.launcher import reply_getter


# ────────────────────────────────────────────────────────────────────
# Fake OpenAI client （接收参数检查）
# ────────────────────────────────────────────────────────────────────


class _FakeCompletions:
    def __init__(self):
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        # 构造一个 sync 响应（get_ipu_reply 用）
        msg = SimpleNamespace(content="hello-from-fake", role="assistant")
        choice = SimpleNamespace(message=msg, finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})


class _FakeChatCompletions(_FakeCompletions):
    """流式用：返回可迭代对象（不是 response）。"""
    def create(self, **kwargs):
        self.last_kwargs = kwargs
        # 返回一个简单的 iterable
        return iter([SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")],
        )])


class _FakeChat:
    def __init__(self):
        self.completions = _FakeChatCompletions()


class _FakeOpenAISync:
    """给 get_ipu_reply 用——返回完整 response。"""
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


# ────────────────────────────────────────────────────────────────────
# form_client
# ────────────────────────────────────────────────────────────────────


def test_form_client_uses_provider_when_supplied():
    provider = IPUProvider(api_key="kp_test", base_url="https://example.test/v1")
    client = reply_getter.form_client(provider)
    # OpenAI 客户端对象本身不易检查身份，我们只看它能被正常使用。
    assert client is not None


def test_form_client_uses_default_provider_when_none():
    # 不应抛
    client = reply_getter.form_client(None)
    assert client is not None


# ────────────────────────────────────────────────────────────────────
# get_ipu_reply —— 非流式单次调用
# ────────────────────────────────────────────────────────────────────


def test_get_ipu_reply_returns_first_choice_content(monkeypatch):
    fake = _FakeOpenAISync()
    monkeypatch.setattr(reply_getter, "OpenAI", lambda **kw: fake)
    content = reply_getter.get_ipu_reply(
        client=fake, ipu="model-x", messages=[{"role": "user", "content": "x"}],
        temperature=0.3, max_icp=200,
    )
    assert content == "hello-from-fake"
    sent = fake.chat.completions.last_kwargs
    assert sent["messages"] == [{"role": "user", "content": "x"}]
    assert sent["model"] == "model-x"
    assert sent["temperature"] == 0.3
    assert sent["max_completion_tokens"] == 200


def test_get_ipu_reply_returns_empty_when_no_choices(monkeypatch):
    class _NoChoice:
        chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: SimpleNamespace(choices=[])))

    fake = _NoChoice()
    content = reply_getter.get_ipu_reply(
        client=fake, ipu="x", messages=[{"role": "user", "content": "x"}])
    assert content == ""


def test_get_ipu_reply_returns_empty_when_content_is_none(monkeypatch):
    class _NoneContent:
        chat = SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None), finish_reason="stop")])
        ))

    fake = _NoneContent()
    content = reply_getter.get_ipu_reply(
        client=fake, ipu="x", messages=[{"role": "user", "content": "x"}])
    assert content == ""


# ────────────────────────────────────────────────────────────────────
# get_ipu_stream_reply —— 流式请求构造（关键参数映射）
# ────────────────────────────────────────────────────────────────────


def test_get_ipu_stream_reply_uses_supplied_config(monkeypatch):
    fake_chat = _FakeChat()
    fake_client = SimpleNamespace(chat=fake_chat)

    cfg = IPUConfig(
        ipu="stream-model", api_key="k1", base_url="https://x.test/v1",
        stream=True, temperature=0.5, top_p=0.7, max_icp=512,
        tools=[{"type": "function", "function": {"name": "x"}}],
        tool_choice="auto",
        extra_body={"k": "v"},
        reasoning_effort="medium",
    )

    msgs = [
        {"role": "user", "content": "hello", "_reasoning": "should be stripped"},
        {"role": "assistant", "content": "world"},
    ]

    stream = reply_getter.get_ipu_stream_reply(msgs, fake_client, cfg)

    # 消费流（拿一次确认）
    list(stream)
    sent = fake_chat.completions.last_kwargs
    assert sent["model"] == "stream-model"
    assert sent["temperature"] == 0.5
    assert sent["top_p"] == 0.7
    assert sent["max_completion_tokens"] == 512
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert sent["extra_body"] == {"k": "v"}
    assert sent["tools"] == [{"type": "function", "function": {"name": "x"}}]
    assert sent["tool_choice"] == "auto"
    assert sent["reasoning_effort"] == "medium"
    # _reasoning 字段被剥离（调用层要求）
    assert all("_reasoning" not in m for m in sent["messages"])
    assert sent["messages"][0] == {"role": "user", "content": "hello"}


def test_get_ipu_stream_reply_creates_default_client_when_none(monkeypatch):
    fake_chat = _FakeChat()
    monkeypatch.setattr(reply_getter, "OpenAI", lambda **kw: SimpleNamespace(chat=fake_chat))

    cfg = IPUConfig(ipu="m", api_key="k", base_url="https://x")
    list(reply_getter.get_ipu_stream_reply(
        [{"role": "user", "content": "y"}], None, cfg))

    sent = fake_chat.completions.last_kwargs
    assert sent["model"] == "m"


def test_get_ipu_stream_reply_uses_default_config_when_ipu_config_none(monkeypatch):
    fake_chat = _FakeChat()
    fake_client = SimpleNamespace(chat=fake_chat)

    list(reply_getter.get_ipu_stream_reply(
        [{"role": "user", "content": "y"}], fake_client, None))
    # 默认 IPUConfig 的 ipu = "MiniMax-M2.7"
    sent = fake_chat.completions.last_kwargs
    assert sent["model"] == "MiniMax-M2.7"


def test_get_ipu_stream_reply_strips_all_reasoning_keys(monkeypatch):
    fake_chat = _FakeChat()
    fake_client = SimpleNamespace(chat=fake_chat)

    msgs = [
        {"role": "user", "content": "a", "_reasoning": True, "extra": "ignored"},
        {"role": "assistant", "content": "b", "_reasoning": True},
    ]
    list(reply_getter.get_ipu_stream_reply(msgs, fake_client, IPUConfig(ipu="m")))
    sent = fake_chat.completions.last_kwargs
    # 只保留 _reasoning 之外的所有 key（实际实现保留了除 _reasoning 外的所有 key）
    for m in sent["messages"]:
        assert "_reasoning" not in m
