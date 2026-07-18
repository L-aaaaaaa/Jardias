# meta/ — 项目元文件目录

本目录收纳项目**对外、对社区**的元文件（meta-files）。这些文件与 `doc/`（项目内部文档）
和 `library/`（论文原文）在性质上不同：

| 目录 | 协议 | 用途 |
|---|---|---|
| `meta/` | Apache-2.0（与代码同） | 开源协议、商标声明、贡献流程、变更日志 |
| `doc/` | Apache-2.0 | 项目内部技术文档、设计笔记、计划 |
| `library/` | CC BY-NC-SA-4.0 | 论文原文、理论文档、演示场景 |

---

## 文件清单

### 协议文件（法律原文，复制粘贴自官方源）

- **LICENSE-CODE** — Apache License 2.0 全文。适用于 `common/` `character/` `tool/` `schedule/` `yinao/` `data_shape/` `app.py` 等所有源代码文件。
- **LICENSE-PAPERS** — Creative Commons Attribution-NonCommercial-ShareAlike 4.0 全文。适用于 `library/` 下的所有论文与文档。

### 项目元文件（社区对接）

- **NOTICE** — Apache 协议强制要求的归属文件。声明项目作者、第三方依赖归属、商标保留、论文协议范围。
- **TRADEMARK** — 商标政策。声明 `Jardias` `Jarnis` `Jardias` `小明` `IPU` `Yinao` `Shice` `HEEL` `ACP` 9 个名称的保留状态和使用边界。
- **CONTRIBUTING** — 贡献流程。包括 CLA 签署指引、PR 规范、commit message 规范、开发约定、不接受的 PR 类型。
- **CLA-INDIVIDUAL** — Apache 个人贡献者许可协议占位骨架。下载官方 PDF 后填入。
- **CLA-CORPORATE** — Apache 企业贡献者许可协议占位骨架。同上。
- **CHANGELOG** — 版本变更日志（Keep a Changelog 格式）。当前仅有 `[Unreleased]` 段，待补历史版本。

---

## 使用方式

### 发布到 GitHub 时的部署

仓库根目录需要把以下文件**软链或复制**出来（GitHub 不会读 `meta/` 内的文件作为仓库元数据）：

| 目标位置 | 来源 | 备注 |
|---|---|---|
| `/LICENSE` | `meta/LICENSE-CODE` | GitHub 右侧 About 面板会识别 |
| `/NOTICE` | `meta/NOTICE` | Apache 协议强制 |
| `/TRADEMARK` 或 `/TRADEMARKS.md` | `meta/TRADEMARK` | 可选 |
| `/.github/CONTRIBUTING.md` | `meta/CONTRIBUTING` | GitHub 在 PR 页面会自动识别 |
| `/CHANGELOG.md` | `meta/CHANGELOG` | 可选 |
| `/library/LICENSE` | `meta/LICENSE-PAPERS` | 在 library/ 目录下单独放一份 |

`CLA-INDIVIDUAL` 和 `CLA-CORPORATE` 通常放在 `.github/CLA.md` 或外链到 Google Form / DocuSign。

### 本地开发时

- 修改协议类文件前请三思，**协议变更会触发所有下游重新授权**
- 修改 `TRADEMARK` 需要公告（CHANGELOG 加 entry）
- 修改 `CONTRIBUTING` 不需要公告但要在 PR 中明确标注

---

## 协议选择理由（决策记录）

代码：Apache-2.0
- LLM 时代的隐藏刚需：专利授权 + NOTICE 强制署名
- 与 `openai` SDK / `pydantic` 等依赖协议兼容
- AI 圈事实标准（PyTorch / TensorFlow / vLLM / LangChain 选它）

论文：CC BY-NC-SA-4.0
- 论文是有立场的方法论（破壁定理 / 四次策略升维 / 时策范式）
- NC 阻止有人把论文打包进付费课程 / 翻译稿商用
- SA 保证衍生论文必须同协议——方法论演化路径可追溯
- 学术引用、课堂分发、个人网站免费分享都允许

—— 决策记录见对话历史「协议选择」轮。

---

## 待补内容（标记 [TODO]）

- [x] `CONTRIBUTING.md` — 已补完（根目录 + meta/ 各一份）
- [x] `CLA-INDIVIDUAL` / `CLA-CORPORATE` — 已下载官方 PDF（icla.pdf / ccla.pdf）
- `SECURITY.md` — 安全漏洞报告流程（私下联系 vs 公开 issue）— 已有，需确认根目录是否需要
- `AUTHORS` — 项目作者与主要贡献者列表 — 已有
- `CHANGELOG` — 补完整历史版本条目