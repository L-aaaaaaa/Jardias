"""
yinao_config.py — 智能演员（Actor）配置数据形状

Actor 配置三层结构：
  - RoleConfig     身份定义（system_prompt / 角色定位 / 特质）
  - IPURuntime     运行时参数（IPU 简称 / 温度 / 智点预算 / 思考模式）
  - ActorConfig    角色完整配置 = 身份 + 运行时

本模块仅声明字段，零行为。
"""
from dataclasses import dataclass, field


@dataclass
class IPURuntime:
    """智能演员可以自由调整的运行时参数。"""
    provider: str = "minimax"
    ipu: str = "2.7"
    temperature: float = 1.0
    top_p: float = 0.95
    max_icp: int = 8192  # 最大输出 ICP（智点）；映射到 API 层 max_tokens
    thinking_mode: str = "auto"
    reasoning_effort: str = "high"
    thinking_enabled: bool = True


@dataclass
class RoleConfig:
    """身份定义。"""
    system_prompt: str = "智能体项目测试助手。"
    title: str = ""  # 头衔，如"数据分析师"
    traits: str = ""  # 特质描述，如"擅长结构化报告"
    max_iterations: int = 10
    birth_time: str = ""


@dataclass
class ActorConfig:
    """一个角色的完整配置。"""
    identity: RoleConfig = field(default_factory=RoleConfig)
    runtime: IPURuntime = field(default_factory=IPURuntime)