# Copyright 2026 Cazlor
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
common — 核心基础设施。

模块层 lazy 暴露 bootstrap / conversation_loop，避免顶层 import 时
拖动整条 yinao.ipu_client 链造成循环。
"""
from __future__ import annotations


def __getattr__(name):
    if name == "bootstrap":
        from .bootstrap import bootstrap
        return bootstrap
    if name == "conversation_loop":
        from .lifecycle import conversation_loop
        return conversation_loop
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")