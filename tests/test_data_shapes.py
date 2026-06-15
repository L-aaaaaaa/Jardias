"""
test_data_shapes.py — 数据形状验证测试

验证所有 data_shape 类型的字段、默认值、实例化正确性。
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from data_shape.agent_config import (
    RuntimeConfig, IdentityConfig, ActorConfig,
    ModelEntry, ProviderConfig, ConfigFile,
)
from data_shape.character import L1Summary
from data_shape.model_client import (
    AIModelConfig, AIModelProvider, ToolCall,
    RoundOutput, ChatResult, RoundMeta, ModelSwitch,
)
from data_shape.tool import ToolDef, ToolParam


def test_runtime_config_defaults():
    rc = RuntimeConfig()
    assert rc.provider == "minimax"
    assert rc.model == "2.7"
    assert rc.temperature == 1.0
    assert rc.max_tokens == 8192
    assert rc.thinking_mode == "auto"
    print("  [OK] RuntimeConfig: 默认值正确")


def test_identity_config():
    ic = IdentityConfig(
        system_prompt="自定义提示词",
        title="分析师",
        traits="擅长数据",
        max_iterations=20,
    )
    assert ic.system_prompt == "自定义提示词"
    assert ic.title == "分析师"
    assert ic.max_iterations == 20
    print("  [OK] IdentityConfig: 字段赋值正确")


def test_actor_config_composition():
    ac = ActorConfig(
        identity=IdentityConfig(system_prompt="组合测试"),
        runtime=RuntimeConfig(temperature=0.5),
    )
    assert ac.identity.system_prompt == "组合测试"
    assert ac.runtime.temperature == 0.5
    print("  [OK] ActorConfig: 组合正确")


def test_model_entry():
    me = ModelEntry(id="deepseek-v4", caps=["thinking", "long_context"])
    assert me.id == "deepseek-v4"
    assert "thinking" in me.caps
    print("  [OK] ModelEntry: pydantic 模型正常")


def test_provider_config():
    pc = ProviderConfig(
        name="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        models={"v4": {"id": "deepseek-chat"}},
    )
    assert pc.name == "deepseek"
    assert pc.base_url == "https://api.deepseek.com/v1"
    assert pc.models["v4"]["id"] == "deepseek-chat"
    print("  [OK] ProviderConfig: pydantic 模型正常")


def test_config_file():
    cf = ConfigFile(
        version=1,
        providers=[
            ProviderConfig(
                name="minimax",
                api_key_env="MINIMAX_API_KEY",
                base_url="https://api.minimax.chat/v1",
                models={"2.7快": {"id": "MiniMax-M2.7"}},
            )
        ],
    )
    assert cf.version == 1
    assert len(cf.providers) == 1
    assert cf.providers[0].name == "minimax"
    print("  [OK] ConfigFile: 嵌套 pydantic 正常")


def test_l1_summary():
    s = L1Summary(
        id="sum_001",
        start_time="2025-01-01 12:00:00",
        end_time="2025-01-01 12:30:00",
        message_count=10,
        topic="测试对话",
        key_events=["事件1", "事件2"],
    )
    assert s.id == "sum_001"
    assert s.message_count == 10
    assert len(s.key_events) == 2
    print("  [OK] L1Summary: dataclass 字段正确")


def test_aimodel_config():
    cfg = AIModelConfig(
        model="test-model",
        base_url="https://test.api/v1",
        temperature=0.7,
        max_completion_tokens=1024,
    )
    assert cfg.model == "test-model"
    assert cfg.temperature == 0.7
    assert cfg.max_completion_tokens == 1024
    assert cfg.stream is True
    print("  [OK] AIModelConfig: pydantic 模型 + 默认值正确")


def test_round_output():
    tc = ToolCall(name="bash", arguments='{"cmd":"echo hello"}')
    ro = RoundOutput(
        reasoning="思考内容",
        content="回复内容",
        tool_calls=[tc],
        deltas=[],
    )
    assert ro.reasoning == "思考内容"
    assert ro.content == "回复内容"
    assert len(ro.tool_calls) == 1
    assert ro.tool_calls[0].name == "bash"
    print("  [OK] RoundOutput: dataclass 组合正确")


def test_chat_result():
    cr = ChatResult(messages=[])
    assert cr.should_switch is False
    assert cr.switch_provider == ""
    print("  [OK] ChatResult: 默认值正确")


def test_round_meta():
    rm = RoundMeta(api_time=2.5, finish_reason="stop")
    assert rm.api_time == 2.5
    print("  [OK] RoundMeta: 正确")


def test_model_switch():
    ms = ModelSwitch(provider="deepseek", model="v4")
    assert ms.provider == "deepseek"
    print("  [OK] ModelSwitch: 正确")


def test_tool_def():
    param = ToolParam(name="cmd", description="命令", parameters={})
    td = ToolDef(name="bash", description="执行 shell", parameters={"type": "object"}, fn=lambda: None)
    assert td.name == "bash"
    assert td.description == "执行 shell"
    print("  [OK] ToolDef: dataclass 正确")


if __name__ == "__main__":
    test_runtime_config_defaults()
    test_identity_config()
    test_actor_config_composition()
    test_model_entry()
    test_provider_config()
    test_config_file()
    test_l1_summary()
    test_aimodel_config()
    test_round_output()
    test_chat_result()
    test_round_meta()
    test_model_switch()
    test_tool_def()
    print("\n" + "="*50)
    print("  [OK] 数据形状: 全部 13 项测试通过")
    print("="*50)

