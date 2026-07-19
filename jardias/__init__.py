"""jardias — Just A Rather Dimension-Free-Updating Intelligent Actor System

包级入口仅指向 console_script 入口,实质逻辑在子模块中:

- app.py                   兼容旧式 `python app.py` 启动
- jardias/__main__.py      pip install 后的标准入口,被 `jardias` 命令调用
- common/                  会话引导、对话循环、日志、CLI 渲染
- yinao/                   义脑:多供应商 IPU 路由 + 容错热切换
- character/               角色注册表、身份管理、对话历史
- tool/                    内置工具 + @actor_tool 旁路小模型
- experience/              HEEL 自传体记忆 + 金字塔压缩
- schedule/                时策:语义驱动调度
- media/                   视觉智能基元自动切换
- data_shape/              数据契约 (Pydantic models)
"""
__version__ = "0.1.0"
