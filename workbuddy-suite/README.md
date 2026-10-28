# WorkBuddy Studio

给 Agent 加一双「人的手」和「人的眼」：**改文件**、**点元素**，以及**把已有的 RPA 原子能力真正调起来**。

三件上架物 + 一个桥接技能：

| # | 产物 | 目录 | 作用 |
|---|---|---|---|
| 1 | 轻量编辑窗口（PPX 桌面应用） | `editor/` | 光标/选区/撤销/快捷键/IME 全按编辑器习惯来；保存即把 sha + 校验结果交回 Agent |
| 2 | 能力连接器（CLI + Skill） | `connector/` | 把仓里 **89 条**原子能力整理成 WorkBuddy 能读懂、能触发的接口；**不重写执行器** |
| 3 | 浏览器扩展（MV3） | `extension/` | 点一下页面元素，算出稳定选择器（含 Shadow DOM 宿主与退化路径）并回传 Agent |
| — | 桥接技能 `workflow-studio` | `skills/workflow-studio/` | 1 与 3 的 Agent 侧入口：起会话、推文件、等保存、等拾取 |

上架材料在 `listing/`：`LISTING.md`（填表文案 + 审核问答）、`privacy.md`、`CHANGELOG.md`、`screenshots/`。

---

## 一条命令跑起来

```bash
# 开发态：会话服务 + PPX 窗口（前端走 vite dev）
python skills/workflow-studio/scripts/studio.py gui --dev

# 发布态：窗口用打包好的 exe
python skills/workflow-studio/scripts/studio.py gui --exe path/to/WorkBuddyStudio.exe
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

### 人改文件

```bash
# 1) 把文件推到窗口里，等用户保存（--wait 到点就返回，不阻塞太久）
python skills/workflow-studio/scripts/studio.py open src/config.json --reason "缩进错了，改一下" --wait 300 --pretty
```

返回里有 `changed`、`sha`、`bytes`、`validation`。`changed=false` 说明用户没动就关了，**别假装他改过**。

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

能力清单见 `connector/skills/rpa-automation/references/commands-index.md`（一行一条）
与 `commands-reference.md`（参数/默认值/输出/失败码），两份都由
`connector/tools/gen_capability_reference.py` **从 `commands/**/*.json` 生成**，不手写。

---

## 目录结构

```
workbuddy-suite/
├── editor/                     # PPX 应用（PPX 6.0.1）
│   ├── ppx.toml                # 应用配置：窗口 1040×660、深色底、dev 端口 5273
│   ├── api/api.py              # Python 侧：只做磁盘读写 + 回环通信，不持有编辑状态
│   └── gui/
│       ├── index.html          # 顶栏 / 查找条 / banner / 状态栏 / 两个 dialog
│       ├── src/editor.js       # 编辑内核：透明 textarea + 着色层 + 自建撤销栈 + 行首索引
│       ├── src/highlight.js    # 逐行产出 HTML（保证任一行可独立套错误底色）
│       ├── src/main.js         # RPC、会话轮询、查找、打开/保存、测试钩子
│       └── src/style.css       # GitHub Dark 语义色
├── connector/                  # CLI + Skill（open.workbuddy.cn/docs/connector 规范）
│   ├── connector-meta.json     # 市场元信息（中英名称/描述/示例）
│   ├── cli.json                # init / auth / unAuth / status / statusMatch
│   ├── icon.svg                # 64×64 市场图标
│   ├── skills/rpa-automation/  # SKILL.md + 生成的能力参考
│   └── tools/                  # 生成器 + 上架校验/打包器
├── extension/                  # MV3，无常驻 content script
│   ├── background.js           # 徽标 / 命令 / 右键菜单 / 按需注入
│   ├── content.js              # Shadow DOM 隔离的拾取 UI + 选择器算法
│   └── popup.*                 # 在线状态、最近拾取、端口设置
├── skills/workflow-studio/     # 桥接技能：SKILL.md + references/protocol.md + scripts/studio.py
├── verify/                     # 五套验证 + 负向验证（见下）
├── tools/
│   ├── pack.py                 # 一条命令打三个 zip（含结构断言）
│   └── make_icons.py           # 由同一套几何生成 4 个尺寸的图标
└── listing/                    # 上架材料
```

---

## 验证：五套正向 + 一套负向

```bash
PY=D:/Users/Administrator/Documents/代码/rpa_core/.venv/Scripts/python.exe

$PY verify/verify_suite.py            # 1) 协议 + 上架契约 + 包结构（49 项）
$PY verify/negative_probe.py          # 2) 负向验证：注入 4 类坏数据，必须全红后还原
node verify/editor_ui_test.mjs        # 3) 编辑器手感：CDP 真键盘 / 真 IME（42 项，需先起前端）
node verify/extension_test.mjs        # 4) 选择器质量 + Shadow DOM + 点击上报（16 项）
$PY tools/pack.py                     # 5) 打三个 zip（结构断言）
$PY connector/tools/pack_connector.py --check   # 6) 连接器字段 / 权限 / digest 一致性
```

真机开窗（可选，需要 PPX 运行时）：

```bash
$PY verify/ppx_boot.py                # 开发态：ppx dev + vite，验文件真进了编辑器
$PY verify/ppx_exe.py                 # 发布态：先 ppx build，再用打包出的 exe 跑一遍
node verify/popup_shot.mjs            # 扩展弹窗截图（328×373）
```

### 这些验证在证明什么（不是「文件存在」）

- `verify_suite.py` 真起一个服务：`/open` → `/inbox` → 游标不重复 → `/events` 长轮询超时 →
  `/capture` → 伪造 Origin 必须 `ORIGIN_DENIED` → 越界路径必须 `BAD_PATH` → `/shutdown` 后
  进程真的退出、端点文件真的被清掉；另外检查扩展**用到的 `chrome.*` API 是否都声明了权限**
  （`chrome.alarms` 曾经漏声明：代码在跑，定时器根本没起来）。
- `negative_probe.py` 是**门禁的元验证**：撤掉 `alarms`、塞一条不存在的命令、改假 digest、
  用 flat 布局重打技能包 —— 四种注入都必须被抓到，且逐字节还原（核 md5）后必须回绿。
- `editor_ui_test.mjs` 走 CDP 的 `Input.dispatchKeyEvent` / `Input.insertText` /
  `Input.imeSetComposition`，断言的是**渲染层**：着色层逐行 `textContent` 拼起来必须与原文
  逐字节相等；行号定位在 4000 行文件里**不许逐行扫全文**（数 `_lineOf` 调用次数，8 次以内）。
- `extension_test.mjs` 把 `window.chrome` 换成记录器：重复 `id` 不许硬用、`css-1a2b3c` 不许当
  选择器、Shadow DOM 必须**在 shadow root 内**算唯一性并给出宿主选择器、Esc 后页面点击必须恢复。

---

## 这次修掉的几个真问题

| 问题 | 症状 | 修法 |
|---|---|---|
| 行号渲染 O(n²) | 4000 行文件每敲一键扫 1600 万字符，手感塌 | 行首偏移表 + 二分定位，`_lineOf` 每帧只调一次 |
| 闭合符重复 | `()` 里打 `)` 变成 `())` | 右边已有闭合符时「穿过去」 |
| Home 语义不对 | 永远跳到列 1 | 先到首个非空白，再按一次才回列 1 |
| `chrome.alarms` 未声明 | 徽标不刷新，看着像扩展没连上 | 补权限，并加一条「API ↔ 权限」门禁 |
| Shadow DOM 选择器是废的 | 用 `document` 查唯一性永远 0，退化成裸标签 | 在所属 root 内算唯一性 + 报 `shadowHost` |
| 能力文档停在 26 条 | 仓里 89 条，AI 照着旧文档写不存在的命令 | 生成器 + digest 门禁 + 技能内「以 catalog 为准」 |
| 技能包 flat 布局 | 平台按 `{skill-name}/SKILL.md` 解析，套错就失败 | 默认 folder，包结构进断言 |

---

## 几个不显眼但会咬人的地方

- **系统代理**：Windows 上 `http_proxy` 常驻时，`urllib` 和 Chrome 都会把 `127.0.0.1`
  交给代理，报 `502` / `ERR_CONNECTION_REFUSED`。所以 `studio.py` 和 `api.py` 一律用
  `build_opener(ProxyHandler({}))`，Chrome 加 `--no-proxy-server`。
- **`[hidden]` 会被组件类盖掉**：`.banner` / `.findBar` / `.toasts` 都显式声明了 `display`，
  会压过浏览器默认的 `[hidden]{display:none}`。CSS 里补了 `[hidden]{display:none !important}`。
- **IME 组合期必须把渲染权交回 textarea**：着色层在组合期间 `visibility:hidden`，
  textarea 用 `-webkit-text-fill-color` 还原颜色，否则候选窗飘在空白处。
- **一次中文输入 = 一步撤销**：`compositionstart` 压一步、`compositionend` 压一步。
- **窗口关闭时轮询还在飞**：`main.js` 在 `pagehide` / `beforeunload` 上停表，`call()` 见到
  `closing` 直接返回。开发态改 `editor/gui/src/` 会让 vite HMR 冲掉在飞的调用 ——
  跑 `ppx_boot.py` 时别同时改源码，否则会看到一次假红。
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
| `Inno Setup 6: 未安装` | 打 Windows 安装包要 ISCC | 便携版已经能跑（`editor/build/WorkBuddy Studio/`）；要 `.exe` 安装包就装 Inno Setup 6 再 `ppx build` |
