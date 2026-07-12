"""
update_args.py — update_runtime 强类型参数。

所有字段 Optional：未在 arguments dict 中传则保持 None，handler 用
model_fields_set 区分"未传"与"传为 None"。

设计动机：
- handler 内部不再出现 float()/int()/bool() 等显式类型转换
- 范围/枚举校验由 pydantic validator 一次性完成，失败报 [Error] ... 与旧代码同文案
- 字段语义（含类型 + 校验 + default）集中在此处，与 metadata.py 同步
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── 常量：枚举值 ──
_THINKING_MODES = ("enabled", "disabled", "auto")
_REASONING_EFFORTS = ("high", "max")


class UpdateRuntimeArgs(BaseModel):
    """update_runtime 工具的强类型入参。

    字段全部 Optional，handler 通过 model_fields_set 区分"未提供"与"传为 None"。
    校验失败时报中文 [Error] ... 与旧代码文案一致。
    """

    model_config = {"extra": "forbid"}  # 拒绝未知字段

    ipu: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_icp: Optional[int] = Field(default=None, gt=0)
    thinking_enabled: Optional[bool] = None
    reasoning_effort: Optional[str] = None
    thinking_mode: Optional[str] = None

    # ── 自定义 validator：替换默认英文错误文案为旧实现一致的中文 ──

    @field_validator("temperature", mode="before")
    @classmethod
    def _check_temperature(cls, v):
        if v is None:
            return v
        f = float(v)
        if f < 0 or f > 2:
            raise ValueError(f"must be in [0, 2], got {f}")
        return f

    @field_validator("top_p", mode="before")
    @classmethod
    def _check_top_p(cls, v):
        if v is None:
            return v
        f = float(v)
        if f < 0 or f > 1:
            raise ValueError(f"must be in [0, 1], got {f}")
        return f

    @field_validator("max_icp", mode="before")
    @classmethod
    def _check_max_icp(cls, v):
        if v is None:
            return v
        n = int(v)
        if n <= 0:
            raise ValueError(f"must be positive, got {n}")
        return n

    @field_validator("thinking_enabled", mode="before")
    @classmethod
    def _check_thinking_enabled(cls, v):
        if v is None:
            return v
        return bool(v)

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def _check_effort(cls, v):
        if v is None:
            return v
        eff = str(v).lower()
        if eff not in _REASONING_EFFORTS:
            raise ValueError(f"must be {'/'.join(_REASONING_EFFORTS)}, got {eff}")
        return eff

    @field_validator("thinking_mode", mode="before")
    @classmethod
    def _check_mode(cls, v):
        if v is None:
            return v
        m = str(v).lower()
        if m not in _THINKING_MODES:
            raise ValueError(f"must be {'/'.join(_THINKING_MODES)}, got {m}")
        return m

    # ── 帮助方法：handler 调用处判断字段是否被显式提供 ──

    def has(self, field_name: str) -> bool:
        """字段是否被 LLM 显式传入（含 None）。

        等价于 `field_name in original_arguments`，但基于 model_fields_set。
        """
        return field_name in self.model_fields_set
