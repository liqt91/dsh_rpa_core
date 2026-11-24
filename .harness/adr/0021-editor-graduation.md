# ADR 0021：编辑器端转正 —— PPX 从实验脚手架迁入 `apps/editor`

- 状态：**定案（2026-11-24 维护者拍板「ppx 这个端可以转正了」）**
- 日期：2026-11-24
- 关联：ADR 0016（GUI 主力方向）、ADR 0019（PPX 宿主实验 / Qt 冻结）、
  ADR 0020（旁路形态退役门，本文接它的 §2 决策树）、ADR 0022（旁路应用层）

## 0. 拍板

> 「我觉得，ppx 这个端可以转正了。」

按 ADR 0020 §2 的决策树，三条实证过后应写「ADR 0021 退役 Qt gui」。**实际落地时情况比
0020 写的时候更靠前**：Qt 那 14.7k 行已经在 `7aaeac2 feat!: ppx-only 激进分支——只留 PPX
及其支撑层（ADR 0020 §2.3）` 里删掉了，`src/rpa_core/gui/` 只剩 `__init__.py`（10 行）与
`i18n.py`（140 行，中文标签的唯一 Python 入口）。也就是说「PPX 是唯一端」早已是既成事实，
只是**没有一份文档把这件事写下来**，且它物理上还住在脚手架目录里。

本文补的就是这两件：**正名**（位置与身份一致）+ **判决记录**（谁、凭什么、欠什么）。

## 1. 三条实证的最终读数（0020 §2.3）

| 实证 | 0020 时的状态 | 现在的实际情况 | 判 |
|---|---|---|---|
| ① 覆盖层 | 已过（0019 §2.4） | 抠色真透明 + 整窗穿透 + `GetCursorPos` 轮询跟随，已在原型期跑通 | **过** |
| ② 打包分发 | 未做 | `.harness/scripts/build_editor.py --release` 产 `dist/RPA-Editor-win64.zip` + `.sha256` + 包内 `editor-version.json`；`workbuddy-suite/release.json` 里 `editor: 0.1.3` 是版本事实源，安装器按需从 Release 装 | **过（走 zip 通道，不走 Inno）** |
| ③ 桌面集成 | 未做 | 单实例：`ppx.toml` 的 `singleInstance = true`，PPX 用 `msvcrt.locking` 落 `.instance.lock`；全局热键：能力层 `capture_kit` 的 `GetAsyncKeyState` 轮询（F9 捕获 / F10 校验），不依赖窗口焦点；**托盘：没有**（`ppx_py` 里 `tray` 零命中） | **半过** |

② 为什么不算 Inno：`ppx build` 打完便携目录后会去调 `ISCC.exe` 做安装包，本机没有就返回 1。
`build_editor.py` 的头部写明「靠识别这条失败消息判成功太脆」，于是自己让 ppx 生成 spec、直接
跑 PyInstaller，产出便携目录 + zip + sha256。分发链路是完整的（下载 → 校验 → 解压 → 启动），
只是没有「下一步下一步」的安装向导。这一点如实记账，不算过。

③ 的托盘没做不阻塞转正：它是**体验补齐**，不是**形态成立的前提**（窗口能起、能打包、能分发、
能全局触发捕获，都在）。但它必须挂在欠账上，见 §4。

## 2. 决策

### 2.1 编辑器是主力端

PPX 壳里的编辑器（三栏：指令树 / 流程卡片 / 节点详情）是**唯一**的图形编辑面。Qt 已退役，
devserver 的用户可见形态已退役（ADR 0020 §2.2）。CLI 与 WorkBuddy 连接器继续是另外两个入口，
它们消费同一套能力层。

### 2.2 位置：`.harness/demo/ppx_probe/app` → `apps/editor`

理由不是审美。ADR 0019 §3 当年把「不进 lint 与文档门禁」写成**故意的分界**：

> 原型不进 lint 与文档门禁（`.harness/demo/` 在 ruff `exclude` 里），质量只能靠人工与
> 即将到来的实证 —— 这是「脚手架」与「产品代码」的分界，故意如此。

而这段时间它早就是产品了：5 个 `apps/xhs-workbench/tests/test_editor_*.py`、3 个
`scripts/check_editor_*.mjs`、`release.json` 的版本事实源、`build_editor.py` /
`verify_editor_artifact.py` / `ppx_bridge.py` / `procenv.py` 全都指着它。**身份是产品、
住址是脚手架，且不在 lint 门禁里** —— 这才是要消掉的错位。

新结构：

```
apps/editor/
  ppx.toml  package.json  pnpm-lock.yaml  pnpm-workspace.yaml  ppx.lock  .gitignore
  api/      api.py（RPC 薄层，唯一与主仓的耦合面）+ requirements.txt + resources/
  gui/      index.html + src/{main.js,style.css} + _layout/（布局探针页）+ dist/（产物）
  ppx/      assets（图标、dmg 背景）
  tools/    run_probe.py（日常启动器：多屏救窗 + 端口预检）
```

### 2.3 质量门禁的身份

- `apps/editor/api/api.py` **进 ruff**（迁移时 14 条违例：12 条 E501、1 条 I001、1 条 B018，已修）。
  唯一豁免是 I001：这个文件把能力层入口用别名一次性重导出（`apply_selector as _kernel_apply_selector`
  …），聚在一个 from-import 里才看得出「这就是那组内核函数」，isort 会拆成二十来个独立语句。
  只豁免这一条，其余规则照查（`pyproject.toml` 的 `per-file-ignores`）。
- 探针与一次性脚手架归到 `.harness/spike/ppx/`（仍在 `exclude` 里，这是**真正**的脚手架）。
- `.harness/scripts/check_host_boundary.py` 从「PPX 原型隔离」改成三件事：i18n 残留冻结、
  编辑器不得引入 Qt / gui UI 模块、**编辑器住址**（必须在 `apps/editor` 且有 `ppx.toml`，
  且 `.harness/demo/ppx_probe/` 不得复活）。

### 2.4 分支治理：编辑器整棵共享

ADR 0022-R3 原本把「客户端打包配置」5 个文件（`ppx.toml`、`package.json`、`pnpm-lock.yaml`、
`pnpm-workspace.yaml`、`ppx.lock`）精确独占给 `ppx-only`。搬家后它们与被共享的编辑器源码同处
一室，`check_branch_scope` 当场判「越界」——**共享目录里的独占文件，一搬就违规**。

处置：`apps/editor/` 整体进 `shared_runtime`，`ppx-only` 的独占收窄为 `apps/editor/build/`
（客户端打包产物目录，gitignored）。语义没丢：**只有 ppx-only 出包**；丢的是「用文件路径表达
这件事」的精度。

## 3. 后果

**正面**

- 位置、门禁身份、文档口径三者一致，新人不会再问「编辑器到底在哪」。
- 后端 4064 行从此过 lint：迁移当场就抓到 1 条真实的 B018 与 12 条超长行；改超长行时又把
  `proc.returncode` 提前取值，破坏了短路求值，被 `test_ppx_bridge` 抓了个正着（桩进程没有
  这个属性）——这条判据本身也因此被证明有效。
- 启动器不再要求仓内 74M 的独立 venv：`find_ppx()` 按「本地 venv → `uv tool` → PATH」找
  `ppx.exe`。

**代价**

- 迁移触达 24 个代码/配置引用点（门禁 5、apps 6、workbuddy-suite 3、src 1、其他）与 31 个
  已归档的负向探针脚本。全部机械替换，但**任何一次漏改都是静默断链**（脚本读的是路径，不会
  报「你忘改了」）。
- `node_modules` 跟着搬会失效（pnpm 虚拟 store 记的是绝对路径）：必须在新位置 `pnpm install`
  重建（本次实测 16 个包全部命中本地 store，无网络下载）。`gui/dist` 也要重跑一次 `vite build`。
- `run_probe.py` 在**受限令牌**下拉起 ppx 时，ppx 建 `%APPDATA%\com.example.rpa-probe\.instance.lock`
  会 EACCES；直接 `ppx dev` 无此问题。这是宿主沙箱的继承行为，不是产品缺陷 —— 记在这里免得
  下一个人当 bug 查。

**门禁盲区（本次实测撞上）**

`check_editor_location` 只判「`apps/editor/ppx.toml` 在不在、旧目录有没有复活」，**判不出
文档与代码字符串里指向旧地址的引用**。迁移后第一次全量门禁是绿的，但仓库里仍有 12 个文件
写着 `.harness/demo/ppx_probe`（另有 1 处是新写的迁移注记，本就该留），其中两处是**可执行**的：
`gui/src/main.js` 的 `DEMO_FLOW` 与 `gui/_layout/probe.html` 的默认 `?flow=`。前者更阴——那个常量**从来就没匹配上过**流程
列表（后端 `rpa.bootstrap` 只扫 `examples/`、`workflows/`、`_sandbox/` 三处），一直是靠
`flows[0]` 兜底，所以它坏了也「看起来正常」。已改成按文件名 `struct.json` 认、认不出退回
列表首项；探针默认值改指 `.harness/spike/ppx/_sandbox/struct.json`。

没有加「全仓禁出现旧路径」的门禁：旧地址在 ADR、PROGRESS、历史教训表、负向探针里
**本来就该留着**（它们记的是「当时发生过什么」），要放行就得维护一张豁免名单，而豁免名单
本身就是新的漂移源。折中做法是**只把可执行入口收进判据**（`check_editor_location` 已覆盖
宿主目录本身），文档口径靠人工复核 —— 这次是靠 `grep` 兜住的，不是靠门禁。

## 4. 遗留债（按优先级）

1. **分支同步**：`.harness/branch-scope.json` 在 `must_match_across_branches` 里，本分支改了
   （`apps/editor/` 共享 + `ppx-only` 独占改 `apps/editor/build/`），**`ppx-only` 与
   `plugin-only` 两个分支需要同步到逐字节一致**，且那两条分支的树还是老结构（`apps/editor`
   不存在，`shared_runtime` 的目录前缀存在性检查会红）——它们也要各自跑一次这次迁移。
   在此之前，本分支的门禁只能保证「不是本分支改坏的」。
2. **托盘**：`ppx_py` 没有原生托盘，要自己 `Shell_NotifyIcon`（需另立任务单，含最小化到托盘
   与「关窗即退 vs 驻留」的取舍）。
3. **安装包**：zip 通道够用但没有向导；Inno Setup 6 一档继续挂着（ADR 0020 §5 第 1 条）。
4. **`rpa_core.gui.i18n` 的位置债**（0019 §4.1）：`gui/` 这具空壳只剩它是活的，应迁到能力层
   （`rpa_core.catalog.i18n` 之类）。现在冻结基线只锁这两个文件，动它就是签字。
5. **`apps/editor/gui/_layout/mock.json`** 是布局探针的生成物（gitignored），里面的路径还是
   老的 `.harness/demo/ppx_probe/_sandbox/...`；下次跑 `_layout_probe.py` 会重算。

## 5. 与 0019 / 0020 的关系

- 本文**执行** 0020 §2 决策树的「三条全过 → 退役 Qt」一支，但因为 Qt 已在 `7aaeac2` 先退役，
  实际落地的是这支的**后半段**：正名 + 门禁 + 分支治理。
- ADR 0019 §2.1 的「Qt 源码冻结」继续有效，但对象只剩 `i18n.py` / `__init__.py`；冻结的语义
  从「别改我们正在用的 Qt」变成「别往这具空壳里加东西」。
- ADR 0016「GUI 是唯一主力形态」的结论不变，只是「GUI」指的东西换了人。
