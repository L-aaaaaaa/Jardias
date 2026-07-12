# Jardias 单元测试

使用 `pytest` 运行。所有测试不依赖真实 LLM / 网络，可离线运行。

## 运行

```powershell
# 1. 切到测试目录
cd tests

# 2. 全部运行
python -m pytest

# 3. 跑某个文件
python -m pytest test_data_shape.py

# 4. 跑某个类 / 方法
python -m pytest test_circuit_breaker.py::TestCircuitBreakerStateMachine::test_at_threshold_opens

# 5. 详细输出
python -m pytest -v

# 6. 首个失败即停 + 短 traceback
python -m pytest -x --tb=short
```

## 覆盖范围

| 文件 | 覆盖模块 |
| --- | --- |
| `test_data_shape.py` | `data_shape/`：ActorConfig / IPURuntime / IPUConfig / L1Summary 等数据形状声明 + 序列化 |
| `test_circuit_breaker.py` | `yinao/ipu_client/circuit_breaker.py`：状态机 + 耗尽检测 |
| `test_common.py` | `common/utils.py`、`common/cli_style.py`、`common/actor_log.py`、`common/logger.py` |
| `test_character.py` | `character/`：命名解析、History、配置 IO、registry |
| `test_summarizer.py` | `character/summarizer.py`：区间合并 / gap 计算 / ground truth 提取 / 正则防误匹配 / L1 序列化 |
| `test_experience_core.py` | `common/experience_core.py`：4 段结构读写 / 渲染 helpers |
| `test_media_image.py` | `media/image.py`：URL / 路径 / data-uri 检测 |
| `test_actor_tool.py` | `tool/actor_tool.py`：装饰器 + executor 注入 |
| `test_schedule.py` | `schedule/`：DelayCondition、ScheduleRepository、TemporalScheduler、JobFireContext |
| `test_ipu_resolver.py` | `yinao/provider_manager.py` + `yinao/ipu_resolver.py`：IPURegistry 加载 / 重载 / Provider CRUD |

## 关键边界用例

- **熔断器**：阈值 + 半开超时（直接修改 `_opened_at`）+ 错误耗尽模式（关键字 + HTTP 状态码）
- **区间合并**：不相交 / 重叠 / 相邻 / 嵌套 / 乱序输入归一
- **前后向兼容**：旧字段名 `model → ipu`、`role → title` 自动迁移
- **路径解析**：`202605011252-小明` → `小明`，12 位时间戳严格匹配
- **正则防误匹配**：`_build_topic_label_regex("话题1")` 不误匹配 `话题12`（负向先行）
- **experience.md 4 段结构**：init → write user input → 对话完成占位
- **shice 队列**：准时 / 错过 / 全部过期 / 合并到已有 job（跳过条件）
- **IPU registry**：reload 覆盖缓存、字段重命名兼容 `models → ipus`

## 不污染根目录

所有用到文件系统的 fixture 都基于 pytest 的 `tmp_path` 自动 cleanup，并通过 `tmp_workdir` fixture 重定向 `character.CHAR_ROOT` 到临时目录，**不会写入真实** `character_data/`，亦不会污染 `logs/`。
