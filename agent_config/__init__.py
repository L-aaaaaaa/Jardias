"""
agent_config — 智能体配置的集中管理

三层结构：
  environment → api_key/base_url（只读，不暴露给 LLM）— 在 model_resolver.py
  identity    → 身份定义（system_prompt/role/...）— config_shape.py
  runtime     → 运行时参数（model/temperature/...）— config_shape.py
"""
from .model_resolver import (
    Provider,
    SYSTEM_PROMPT,  # 向后兼容
    MODEL_NAMES,
    MODEL_CAPABILITIES,
    get_model_capabilities,
    choose_model,
    choose_provider,
    resolve_model,
)
from data_shape import (
    AgentConfig,
    RuntimeConfig,
    IdentityConfig,
)
from .config_io import (
    load_config,
    save_config,
    init_config,
    get_config_path,
)
