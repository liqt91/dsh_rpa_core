# WorkBuddy Studio

给 Agent 加一双「人的手」和「人的眼」：**改文件**、**点元素**，以及**把已有的 RPA 原子能力真正调起来**。

三件上架物 + 一个桥接技能：

| # | 产物 | 目录 | 作用 |
|---|---|---|---|
| 1 | 工作流编辑窗口（PPX 桌面应用） | `.harness/demo/ppx_probe/app/` | **复用 M65 的可视化编排器**：卡片拼流程、参数编辑、编译校验、真跑 `rpa-core run`；本轮给它接上会话通道（Agent 推文件 → 窗口打开 → 回 opened/心跳） |
| 2 | 能力连接器（CLI + Skill） | `connector/` | 把仓里 **89 条**原子能力整理成 WorkBuddy 能读懂、能触发的接口；**不重写执行器** |
| 3 | 浏览器扩展（MV3） | `extension/` | 点一下页面元素，算出稳定选择器（含 Shadow DOM 宿主与退化路径）并回传 Agent |
| — | 桥接技能 `workflow-studio` | `skills/workflow-studio/` | 1 与 3 的 Agent 侧入口：起会话、推文件、等打开、等拾取 |

上架材料在 `listing/`：`LISTING.md`（填表文案 + 审核问答）、`privacy.md`、`CHANGELOG.md`、`screenshots/`。

---

## 一条命令跑起来

```bash
# 开发态：先起会话服务，再起 PPX 窗口（前端走 vite dev，改 main.js 热更新）
python skills/workflow-studio/scripts/studio.py serve --root .
cd ../.harness/demo/ppx_probe/app && ppx dev

# 发布态：先在 ppx_probe/app 里 ppx build，产物 build/RPA Probe/
```

只想起服务（Agent 侧、CI、或已经手动开了窗口）：

```bash
python skills/workflow-studio/scripts/studio.py serve --root .
python skills/workflow-studio/scripts/studio.py status --pretty
python skills/workflow-studio/scripts/studio.py stop
```

环境体检（环境 / 会话 / 窗口 / 扩展四段）：

```bash
python skills/workflow-studio/scripts/studio.py doctor
```

### 人改流程（工作流编辑窗口）

```bash
# 1) 把工作流推到窗口里（ppx_probe 的可视化编排器会自动打开它）
python skills/workflow-studio/scripts/studio.py open D:/abs/path/flow.json --reason "改一下" --wait 300 --pretty
```

窗口轮询收件箱（1.4s）→ 自动 `openFlow` → 回 `opened` 事件与心跳；`status` 里
`editor.path` 就是窗口当前正在编辑的文件。用户在窗口里保存走的是 ppx_probe 自己的
保存链路（sha 冲突检测 + 编译校验）。

### 人点元素

```bash
# 2) 进入等待，请用户点扩展图标 → 在页面上点一下
python skills/workflow-studio/scripts/studio.py capture --timeout 120 --pretty
```

返回 `selector` / `strategy` / `alternatives` / `tag` / `text` / `matches` / `shadow` / `shadowHost`。

### Agent 调 RPA 原子能力

```bash
rpa-core status                      # {"status": "ready", ...}
rpa-core catalog                     # 89 条能力的 id / kind / effect + digest
rpa-core validate my-flow.json       # 静态校验（不进 run）
rpa-core run my-flow.json            # 跑，输出 RunResult
```

能力清单见 `skills/workflow-studio/references/commands-index.md`（一行一条）
与 `commands-reference.md`（参数/默认值/输出/失败码），两份都由
`connector/tools/gen_capability_reference.py` **从 `commands/**/*.json` 生成**，不手写。
能力参考只维护在这一个目录：连接器打包时从这里复制一份进 `skills/rpa-core-cli/references/`，
不存在第二份手工副本。

---

## 目录结构

```
workbuddy-suite/
├── (编辑窗口不在本目录：.harness/demo/ppx_probe/app/ —— PPX 应用，M65 宿主实验)
│   ├── ppx.toml                # 应用配置；dev 端口 5173
│   ├── api/api.py              # 能力层接线 + studio.* 会话胶水（本轮加）
│   └── gui/src/main.js         # 可视化编排 + 会话轮询（本轮加 pollSession/heartbeat）
├── connector/                  # CLI + Skill（open.workbuddy.cn/docs/connector 规范）
│   ├── connector-meta.json     # 市场元信息（中英名称/描述/示例）
│   ├── cli.json                # init / auth / unAuth / status / statusMatch
│   ├── icon.svg                # 64×64 市场图标
│   ├── skills/rpa-core-cli/    # 连接器必带的接线配件（不编排；编排归 workflow-studio）
│   └── tools/                  # 生成器 + 上架校验/打包器（能力文档从主技能源复制）
├── extension/                  # MV3，无常驻 content script
│   ├── background.js           # 徽标 / 命令 / 右键菜单 / 按需注入
│   ├── content.js              # Shadow DOM 隔离的拾取 UI + 选择器算法
│   └── popup.*                 # 在线状态、最近拾取、端口设置
├── skills/workflow-studio/     # 唯一的技能源：编排 + 人在环 + 运行；references/ 含 89 条能力文档
├── verify/                     # 五套验证 + 负向验证（见下）
├── tools/
│   ├── pack.py                 # 同步版本 + 一条命令打三个 zip（含结构断言）
│   ├── release.py              # release.json ↔ 各产物版本的读写（唯一事实源）
│   ├── dist_sources.py         # zip 内路径 → 源文件：打包与哈希判据共用一份映射
│   └── make_icons.py           # 由同一套几何生成 4 个尺寸的图标
├── release.json                # 七个产物的版本事实源；改版本只改这里
└── listing/                    # 上架材料

改版本 / 改技能源码之后，**必须重跑 `python tools/pack.py`**：前者由它把版本写回
各产物文件，后者由它重打三个 zip。`verify/verify_suite.py` 会逐文件比 zip 与源的
sha256，旧包再也不能靠「入口层级对」一直绿。
```

---

## 验证：正向 + 负向

```bash
PY=D:/Users/Administrator/Documents/代码/rpa_core/.venv/Scripts/python.exe

$PY verify/verify_suite.py            # 1) 协议 + 上架契约 + 版本口径 + 包内容哈希
$PY verify/negative_probe.py          # 2) 负向验证：注入 4 类坏数据，必须全红后还原
$PY ../.harness/spike/probe_release_dist_negative.py
                                      # 3) 新判据负向验证：版本/哈希/合并三条 +
                                      #    「关掉判据就变绿」的元验证
node verify/extension_test.mjs        # 4) 选择器质量 + Shadow DOM + 点击上报
node verify/skill_install_test.mjs    # 5) 安装器冒烟（含 --target workbuddy 幂等）
$PY tools/pack.py                     # 6) 同步版本 + 打三个 zip
$PY connector/tools/pack_connector.py --check   # 7) 连接器字段 / 权限 / digest 一致性
```

编辑窗口的真机探针在 ppx_probe 自己的目录（开发态 + 运行 + 事件流 + 插入/删除节点，
要开真窗口）：

```bash
cd ../.harness/demo/ppx_probe
./.venv/Scripts/python.exe launcher_probe.py     # 启动器不谎报（20 条）
./.venv/Scripts/python.exe ui_probe.py           # 界面端到端（26 条）
./.venv/Scripts/python.exe exec_probe.py         # 运行接线（30 条）
node ../workbuddy-suite/verify/popup_shot.mjs    # 扩展弹窗截图（328×373）
```

### 这些验证在证明什么（不是「文件存在」）

- `verify_suite.py` 真起一个服务：`/open` → `/inbox` → 游标不重复 → `/events` 长轮询超时 →
  `/capture` → 伪造 Origin 必须 `ORIGIN_DENIED` → 越界路径必须 `BAD_PATH` → `/shutdown` 后
  进程真的退出、端点文件真的被清掉；另外检查扩展**用到的 `chrome.*` API 是否都声明了权限**
  （`chrome.alarms` 曾经漏声明：代码在跑，定时器根本没起来）。
- `negative_probe.py` 是**门禁的元验证**：撤掉 `alarms`、塞一条不存在的命令、改假 digest、
  用 flat 布局重打技能包 —— 四种注入都必须被抓到，且逐字节还原（核 md5）后必须回绿。
- **会话胶水的端到端（本轮实测）**：起 `studio.py serve` → 起带 `studio.*` 胶水的 ppx 窗口 →
  `studio.py open <绝对路径>` → 6 秒内 `status` 里 `editor.path` 变成目标文件、`online=true`、
  事件流出现 `opened`。心跳 5s 一次，断线只表现为 `editor.online=false`，不影响窗口本地功能。
- `extension_test.mjs` 把 `window.chrome` 换成记录器：重复 `id` 不许硬用、`css-1a2b3c` 不许当
  选择器、Shadow DOM 必须**在 shadow root 内**算唯一性并给出宿主选择器、Esc 后页面点击必须恢复。

---

## 这次修掉的几个真问题

| 问题 | 症状 | 修法 |
|---|---|---|
| 编辑器跑偏 | 上轮从零写了个代码编辑器，而用户要的是 ppx_probe 的可视化编排器 | 整个退役（git 历史可找回），编辑窗口回归 `.harness/demo/ppx_probe` 并补上会话胶水 |
| Agent 推不开文件 | ppx_probe 不认识会话协议，`open` 排队永远没人取 | `api.py` 加 `studio.pollSession/notify`，`main.js` 加 1.4s 轮询 + opened/心跳 |
| 脱管后台服务被杀 | `( serve & )` 在宿主 shell 退出后死掉，`open` 报 `SESSION_DOWN` | 长命服务走受管后台任务 |
| `chrome.alarms` 未声明 | 徽标不刷新，看着像扩展没连上 | 补权限，并加一条「API ↔ 权限」门禁 |
| Shadow DOM 选择器是废的 | 用 `document` 查唯一性永远 0，退化成裸标签 | 在所属 root 内算唯一性 + 报 `shadowHost` |
| 能力文档停在 26 条 | 仓里 89 条，AI 照着旧文档写不存在的命令 | 生成器 + digest 门禁 + 技能内「以 catalog 为准」 |
| 技能包 flat 布局 | 平台按 `{skill-name}/SKILL.md` 解析，套错就失败 | 默认 folder，包结构进断言 |

---

## 几个不显眼但会咬人的地方

- **系统代理**：Windows 上 `http_proxy` 常驻时，`urllib` 和 Chrome 都会把 `127.0.0.1`
  交给代理，报 `502` / `ERR_CONNECTION_REFUSED`。所以 `studio.py` 和 ppx_probe 的
  `api.py` 一律用 `build_opener(ProxyHandler({}))`，Chrome 加 `--no-proxy-server`。
- **脱管后台进程会被连带杀**：`( cmd & )` 起的服务在宿主 shell 退出时跟着死
  （本轮 `studio.py serve` 死过一次，`open` 报 `SESSION_DOWN`）。长命服务必须交给
  受管的后台任务或独立终端。
- **会话胶水是可选依赖**：Agent 服务不在，编辑窗口照常能用；轮询失败静默离线，
  心跳 5s 一次，任何一次调用不超 1 秒 —— 别让对端不在卡住 UI。
- **发布态产物要重打**：改了 `gui/src/` 必须 `vite build`（`dist/` 被 PPX 直接吃），
  再 `ppx build` 出 exe。只改源码不重建，双击 exe 的人用的还是旧代码。

---

## 版本对齐（很重要）

PyPI 上的 `rpa-core-runtime` 最新发布是 **0.1.0（26 条命令）**，本仓已是 **89 条**。
所以连接器的能力文档按**本仓**生成，并在文档头写了 digest；技能里第 0 步要求先跑
`rpa-core catalog` 对齐 digest，不一致就**以本机为准**、不要照文档硬调。
发布新版本后重跑：

```bash
$PY connector/tools/gen_capability_reference.py     # 重新生成三份参考
$PY connector/tools/pack_connector.py               # 校验 + 打包
```

---

## 边界（不假装支持）

1. 跨域 iframe 里的元素拾取不到（`activeTab` 拿不到跨源子框架注入权）。上报里 `frame` 会如实写。
2. Shadow DOM 里的元素：给出**宿主选择器 + 内部选择器**，但纯 CSS 选择器本身穿不过去，
   调用方需要用 `host.shadowRoot.querySelector(inner)` 或 CDP 的 pierce 模式。
3. 只处理 UTF-8 文本，非 UTF-8 直接 `NOT_UTF8` 拒绝，不冒「存成乱码」的险。
4. 单文件 8MB、着色 240K 字符上限，超了不着色（仍可编辑）。
5. 不做语法补全 / LSP —— 这是编辑窗口，不是 IDE。

## 环境上的两处坑（跟代码无关）

`ppx doctor` 现在 16 项里 14 项通过，剩下两项都是本机环境：

| 失败项 | 原因 | 怎么办 |
|---|---|---|
| `pnpm: 12.15.0` | PPX 6.0.1 的 doctor 只认 `>=11.0.0, <12.0.0` | 实测 12.15 下 `dev` 与 `build` 都正常；要消掉这行就 `corepack prepare pnpm@11.25.0 --activate` |
| `Inno Setup 6: 未安装` | 打 Windows 安装包要 ISCC | 便携版已经能跑（`ppx_probe/app/build/RPA Probe/`，`ppx build` 产出）；要 `.exe` 安装包就装 Inno Setup 6 再 `ppx build` |
