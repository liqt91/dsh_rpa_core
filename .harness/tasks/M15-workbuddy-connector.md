# M15 WorkBuddy 连接器入驻（CLI + Skill）

状态：`done`

## 背景与决策

WorkBuddy 开放平台五种入驻形态评估完成（2026-09-02，基于 open.workbuddy.cn 开放平台文档）：

| 形态 | 结论 | 原因 |
|---|---|---|
| **连接器 CLI+Skill** | ✅ 主路径 | rpa-core CLI 天然满足硬要求（非交互安装、JSON 输出、明确退出码、Python runtime 声明）；无认证场景 auth 可省略 |
| MCP+Skill | ⚠️ 升级路径 | 触碰 ADR 0006 的 MCP 排除清单，需新 ADR；CLI 场景下属于过度工程 |
| 纯 Skill | ⚠️ 不独立 | 不带安装管理，CLI 不存在时不可用 |
| 专家/专家团 | 🕓 后置包装 | 等 CLI 连接器跑通后包「RPA 自动化专家」（依赖声明原生支持 connectors） |
| Buddy 应用 | ❌ | 垂直行业工作台外壳，与工具型定位不符 |
| 第三方应用 | ❌ | 方向反了（那是外部应用调 WorkBuddy Open API） |

## 目标

把 rpa_core 的 CLI + 工作流编写能力打包成 WorkBuddy 连接器：用户在 WorkBuddy 对话中说「帮我每天打开 XX 抓数据」→ AI 经 Skill 指导编写 workflow → 调 CLI 运行 → 读回 result.json 回答。

## 任务

- [x] CLI 补齐连接器要求：`auth / status / unauth` 命令组（无认证场景返回固定 ready 状态 + 版本）；`statusMatch` 契约测试
- [x] 连接器目录结构（`workbuddy-connector/`）：`connector-meta.json`（type: cli、kebab-case source、中英描述与示例）、`cli.json`（init/auth/status 三平台命令、runtime python 声明、statusMatch）、`icon.svg`
- [x] `skills/rpa-automation/SKILL.md`：工作流格式指引（references/workflow-format.md 引用）、CLI 调用模式（run/resume 经 CLI）、常见错误与恢复（indeterminate 人工确认、ELEMENT_NOT_FOUND 重捕获）
- [x] `references/`：workflow-format.md（AST 契约）、commands-reference.md（26 条命令输入输出摘要）、error-codes.md
- [x] 安装验证：干净 venv `pip install <本地路径>` → `rpa-core status` / `validate` / `catalog` 可用
- [x] 提交前检查清单全绿（脚本化逐条断言）
- [x] 完整门禁通过

## 验收标准

- [x] 连接器目录通过 WorkBuddy 提交前检查清单全部条目（脚本化断言）
- [x] Skill 指导 AI 能完成：编写一个 data 类 workflow → 编译 → 运行 → 读回结果（文档含完整可运行示例）
- [x] 全门禁通过（181 tests）

## 范围外

- MCP 化（另立 ADR 评估）
- 专家/专家团包装（M16 候选）
- 真实 WorkBuddy 市场审核提交（需企业资质）

## 待定问题

- ~~CLI 三件套在无认证场景的最小语义~~ → status 返回 `{"status":"ready","version":...,"auth":"none"}`，statusMatch 匹配 `"status": "ready"`（已定）
- ~~连接器 source 命名~~ → `rpa-core`（已定）
- **init 安装源**：当前 win32 init 用本机绝对路径（自测权宜）。**市场提交前必须换成可安装源**——首选 PyPI（`python -m pip install rpa-core`），其次公网 wheel URL / git URL（见 2026-09-03 评估）。

## 完成证据

- `workbuddy-connector/`：connector-meta.json（type:cli + kebab source + 中英示例）+ cli.json（runtime python、init/auth/status/unAuth 三平台、statusMatch）+ icon.svg + skills/rpa-automation/（SKILL.md + references 三件套）
- CLI 新增 `auth/status/unauth`（无认证固定 ready 语义）；CLI 入口统一 UTF-8（修子进程 cp936 导致 statusMatch JSON 解码失败）
- **修真实部署缺口**：`commands/` 打进 wheel（hatch force-include）+ CLI 命令目录解析包内优先——pip 安装后开箱可用
- 合同测试 6 项（meta/cli.json schema、icon 存在、statusMatch 正则匹配、auth/unauth 无副作用、status 幂等带版本）
- 提交前检查清单脚本化全过；干净 venv 本地路径 pip install → status/validate/catalog 实测可用；full gate 181 tests
