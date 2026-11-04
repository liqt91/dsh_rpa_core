# ADR 0020：旁路形态退役门与仓库精简

状态：**定案（2026-11-04 维护者拍板）** —— §2.1 分流结果已由维护者选定；
Qt 处置仍受 0019 §2.3 三条实证门约束（Qt 保留冻结，未来优先 PPX）。

日期：2026-11-04（草案同日转定案）
关联：ADR 0016（GUI 主力方向）、ADR 0019（PPX 宿主实验）、`tasks/BACKLOG.md`

## 0. 维护者拍板（2026-11-04）

> 「主分支：1 做了，2 先放着，3 devserver 弄走，qt 的先放着，未来优先 ppx。
> 激进分支：只留 ppx 和支撑 ppx 运行所需的相关代码和文档」

对应执行：

| 决定 | 落点 |
|---|---|
| 残留清理照做 | §4 已执行 |
| 影刀 web 镜像 12M 先放着 | §4「暂不动」维持，待对标收口 |
| devserver 弄走 | §2.2 本日执行（见下） |
| Qt 先放着（冻结） | 0019 门不变；未来优先 PPX |

## 1. 背景：仓库同时养着四条形态

| 形态 | 位置 | 规模 | 状态 | 治理文件 |
|---|---|---|---|---|
| Qt GUI | `src/rpa_core/gui/` | **14.7k 行**（src 全量 43%） | 冻结 | ADR 0019 §2.1 冻结基线 |
| Web 编辑器 | `src/rpa_core/devserver/` | 426K | 冻结演进 | ADR 0016（退为可选） |
| PPX 原型 | `.harness/demo/ppx_probe/` | 6.7M（不含 venv） | 活跃实验 | ADR 0019 §2.3 |
| 上架三件套 | `workbuddy-suite/` | 662K | 活跃 | 编辑窗口口径已收口到 ppx_probe |

另有仓库级残留（与本 ADR 同日清理，见 §4）。

## 2. 决策树（按 0019 §2.3 三条实证分流）

### 实证现状（2026-11-04 盘点）

| 实证 | 状态 | 证据 |
|---|---|---|
| ① 覆盖层 | **已过**（带复杂度税） | 0019 §2.4：抠色真透明 + 整窗穿透 + 轮询跟随三件拼装可用；复杂度差记迁移账 |
| ② 打包分发 | **未做** | ppx_probe 无 `ppx build` 产物；BACKLOG 挂着（需 Inno Setup 6 出安装包一档） |
| ③ 桌面集成 | **未做** | 单实例 / 全局热键 / 托盘 / 最小化到托盘，均无探针 |

### 分流

- **三条全过** → 写 ADR 0021「退役 Qt gui」：删 `src/rpa_core/gui/`（-14.7k 行）、
  把 `gui.i18n` 迁到能力层（清 0019 §4.1 的位置债）、更新冻结基线为空。
  devserver 的处置**跟随同一次决策**（两者共享后端能力层，前端形态二选一后另一个没有存在理由）。
- **①过、②或③不过** → PPX 出局或降级，解冻 Qt（走 0019 §2.1 签字流程），GUI 回主力；
  ppx_probe 归档保留实证记录。
- **长期停在中间态**（实证不推进）→ 不可接受：冻结期 GUI 缺陷修复停摆，中间态每多一个月，
  M31 观感审计等冻结债就多积一个月。**本 ADR 要求给 ②③ 实证定死线**（见 §5）。

## 2.2 devserver 退役（2026-11-04，维护者拍板「弄走」）

**取证结论先行**：devserver 不是纯 web 壳——`store.py`/`runs.py` 是被冻结 GUI 与 CLI
消费的共享库；`static/i18n.js` 是中文标签**事实源**（`gui/i18n.py` 是它的解析器，ppx 与
连接器能力文档同源）；`static/app.js` 是两个「两端同口径」契约测试的 Web 参照实现；
扩展运行通道早在 M20 已迁 **Native Messaging**（`extension/background.js` 只走
`connectNative`，从不连 devserver 端口），`browser_ext.py` 里「HTTP → devserver → 扩展」
是 M20 前的陈旧注释。

**刀法**（门禁零破坏）：

| 部分 | 处置 | 理由 |
|---|---|---|
| CLI `devserver` 子命令 | **删除** | 用户可见 web 编辑器的唯一入口；运行期无其他 DevServer 起点 |
| `static/index|catalog|diagnostics.html`、`styles.css`、`icons.js` | **删除** | UI 壳随形态退役；`test_devserver.py` 一并退役 |
| `static/i18n.js` | **保留** | 标签事实源，冻结 GUI 硬编码路径消费；随 Qt 退役时一并迁移 |
| `static/app.js` | **保留** | `check_element_display/name/param_groups/retry_policy/scope_visibility` 五个 mjs 门禁 + 两个契约测试的参照实现 |
| `server.py` | **保留** | 6 个测试的进程内 HTTP 夹具；无 CLI 入口后不再面向用户 |
| `app.py`/`store.py`/`runs.py` | **保留** | 冻结 GUI 进程内复用（`WorkflowDirStore`/`RunManager`/`DEFAULT_CAPABILITIES`） |

**记账的损失**：`install-extension --policy` 的 update manifest 本地托管随入口退役，
策略安装需自行托管并经 `--update-url` 提供（CLI note 已改）。
**余债**：devserver 包名与 i18n.js/app.js 的位置债（0019 §4.1 同源），Qt 退役时
（ADR 0021）一并清——届时 gui 消费方消失，库上提到能力层、标签迁 `catalog.i18n`。

## 2.3 激进分支（`ppx-only`）

维护者拍板「激进分支：只留 ppx 和支撑 ppx 运行所需的相关代码和文档」。从 main 拉分支：

- **删**：`src/rpa_core/gui/`（**保 `i18n.py`**——ppx 依赖 `rpa_core.gui.i18n`）、
  `devserver/` 全量（ppx 零依赖，分支上 `cli.py` 同步摘掉 store import）、
  gui/devserver 专属测试、`docs/yingdao-*` 对标材料、`frontend/`、`testapps/`、`testsite/`
- **留**：能力层全量（model/catalog/compiler/runtime/executors/workers/capture/
  extension_exec/local_transport/control_channel）、`commands/`、`workflows/`、`examples/`、
  `extension/`、`.harness/demo/ppx_probe/`、`workbuddy-suite/`（Agent 侧会话服务
  `studio.py` 在其中）、相关 ADR
- **验收**：pytest 子集 + `rpa-core run` 冒烟 + ppx 窗口 catalog 加载

### 3. 为什么 main 上现在不能删 Qt

## 4. 已执行的精简（本 ADR 同日，全部不触碰代码行为）

| 动作 | 规模 | 方式 |
|---|---|---|
| 根目录手测残留 | 126 个文件 | 挪 `.workbuddy/tmp/_rejected/root-junk-2026-11-04/`（全部未入库） |
| `.harness/spike` 旧输出 | 222 个文件，4.9M→1.4M | 未跟踪且 2026-10-28 前的挪 `.workbuddy/tmp/_rejected/spike-2026-11-04/`；113 个已跟踪探针脚本保留 |
| 影刀桌面镜像 | 173 文件 / 4.6M | 复制到仓外 `_archives/rpa_core/` 后 `git rm`（c7073c0），`yingdao-command-model.md` 指针已改 |
| 影刀 web 镜像 12M | **暂不动** | benchmark 逐条链接 + 邻座未提交修改；待对标收口后随第二刀归档 |

## 5. 待办（把门走完）

1. **实证 ②**：装 Inno Setup 6 → `ppx build` 出双击可跑产物 → 记体积/启动耗时/带运行时否
   （BACKLOG 已挂）。
2. **实证 ③**：写托盘 + 全局热键 + 单实例探针（对标 Qt 版 `run_float` 的托盘行为），
   跑完落读数进 0019 附录。
3. ~~②③ 有读数后：本 ADR 从草案转定案~~（2026-11-04 已定案：devserver 弄走、Qt 先放着、
   未来优先 PPX）；Qt 退役由 ADR 0021 在三条实证全过后接棒。

## 6. 后果

- 正面：决策树固化后，「旁路什么时候能删」不再是主观判断；已清的 5.5M 残留不再占工作区视线；
  devserver 的用户可见形态（web 编辑器 + CLI 入口）已退役，main 门禁保持全绿。
- 代价：devserver 包名与 i18n.js/app.js 的位置债延续到 Qt 退役（0021 一并清）；
  隔离区（`.workbuddy/tmp/_rejected/`）占用约 10M 磁盘，留一个月反悔期后整目录删除。
