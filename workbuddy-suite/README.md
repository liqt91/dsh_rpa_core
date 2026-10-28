# WorkBuddy Studio

给 Agent 加一双「人的手」和「人的眼」：**改文件** 和 **点元素**。

三件套互相咬合，也能各自单飞：

| # | 产物 | 目录 | 作用 |
|---|---|---|---|
| 1 | 轻量编辑窗口（PPX 桌面应用） | `editor/` | 光标/选区/撤销/快捷键/IME 全按编辑器习惯来；保存即把 sha + 校验结果交回 Agent |
| 2 | 技能包 `workflow-studio` | `skills/workflow-studio/` | Agent 侧的入口：起会话、推文件、等保存、等拾取，一条命令拿结构化结果 |
| 3 | 浏览器扩展（MV3） | `extension/` | 点一下页面元素，算出稳定选择器（含候选与退化路径）并回传给 Agent |

上架材料在 `listing/`：`LISTING.md`（填表用的全部文案）、`privacy.md`、`CHANGELOG.md`、`screenshots/`。

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

返回 `selector` / `strategy` / `alternatives` / `tag` / `text` / `matches` / `shadow`。

---

## 目录结构

```
workbuddy-suite/
├── editor/                     # PPX 应用（PPX 6.0.1）
│   ├── ppx.toml                # 应用配置：窗口 1040×660、深色底、dev 端口 5273
│   ├── api/api.py              # Python 侧：只做磁盘读写 + 回环通信，不持有编辑状态
│   └── gui/
│       ├── index.html          # 顶栏 / 查找条 / banner / 状态栏 / 两个 dialog
│       ├── src/editor.js       # 编辑内核：透明 textarea + 着色层 + 自建撤销栈
│       ├── src/highlight.js    # 逐行产出 HTML（保证任一行可独立套错误底色）
│       ├── src/main.js         # RPC、会话轮询、查找、打开/保存、测试钩子
│       └── src/style.css       # GitHub Dark 语义色
├── extension/                  # MV3，无常驻 content script
│   ├── background.js           # 徽标 / 命令 / 右键菜单 / 按需注入
│   ├── content.js              # Shadow DOM 隔离的拾取 UI + 选择器算法
│   └── popup.*                 # 在线状态、最近拾取、端口设置
├── skills/workflow-studio/
│   ├── SKILL.md                # YAML 头 + 三段工作流 + 错误码表 + 边界
│   ├── references/protocol.md  # 7 个路由、事件字段、安全边界
│   └── scripts/studio.py       # 会话服务 + CLI（纯标准库）
├── verify/                     # 六套验证（见下）
├── tools/
│   ├── pack.py                 # 打技能包 / 扩展包（校验 3MB 上限）
│   └── make_icons.py           # 由同一套几何生成 4 个尺寸的图标
└── listing/                    # 上架材料
```

---

## 六套验证，都能自己复跑

```bash
PY=D:/Users/Administrator/Documents/代码/rpa_core/.venv/Scripts/python.exe

# 1) 协议 + 上架材料契约（44 项）
$PY verify/verify_suite.py

# 2) 编辑器手感：CDP 真键盘 / 真 IME（37 项）
node verify/editor_ui_test.mjs          # 需先 pnpm run dev --port 5273 起前端

# 3) 选择器质量 + 点击上报（14 项）
node verify/extension_test.mjs

# 4) 开发态真机开窗：ppx dev + vite，验文件真的进了编辑器
$PY verify/ppx_boot.py

# 5) 发布态真机开窗：先 ppx build，再用打包出来的 exe 跑一遍
$PY verify/ppx_exe.py

# 6) 扩展弹窗截图（328×373）
node verify/popup_shot.mjs

# 打包
$PY tools/pack.py
```

当前结果：44 / 37 / 14 全绿，`ppx_boot.py`、`ppx_exe.py` 都是 0 failed，
`ruff check workbuddy-suite` 通过。

发布态的产物在 `editor/build/WorkBuddy Studio/`（便携版目录，约 28 MB，双击里面的
`WorkBuddy Studio.exe` 就能跑）。本机没装 Inno Setup 6，所以 `ppx build` 最后一步
「出安装包」会提示跳过；装上 ISCC 再跑一次就有 `.exe` 安装包了。

### 验证都在证明什么

- `verify_suite.py` 不是「文件存在」检查。它会**真起一个服务**，跑完 `/open` → `/inbox` →
  游标不重复 → `/events` 长轮询超时 → `/capture` → 伪造 Origin 必须 `ORIGIN_DENIED` →
  越界路径必须 `BAD_PATH` → `/shutdown` 后进程真的退出、端点文件真的被清掉。
- `editor_ui_test.mjs` 走 CDP 的 `Input.dispatchKeyEvent` / `Input.insertText` /
  `Input.imeSetComposition`，不是 `element.value = ...`。断言的是**渲染层**：着色层逐行
  `textContent` 拼起来必须与原文逐字节相等，空行不许塞占位空格。
- `extension_test.mjs` 把 `window.chrome` 换成记录器，逐个元素验选择器质量：
  重复 `id` 不许硬用、`css-1a2b3c` 这种构建产物哈希不许当选择器、Shadow DOM 必须如实标
  `shadow: true`、退化路径至少两级（不许返回裸 `div`）、Esc 退出后页面点击必须恢复正常。

---

## 几个不显眼但会咬人的地方

- **系统代理**：Windows 上 `http_proxy` 常驻时，`urllib` 和 Chrome 都会把 `127.0.0.1`
  交给代理，报 `502` / `ERR_CONNECTION_REFUSED`。所以 `studio.py` 和 `api.py` 一律用
  `build_opener(ProxyHandler({}))`，Chrome 加 `--no-proxy-server`。
- **`[hidden]` 会被组件类盖掉**：`.banner` / `.findBar` / `.toasts` 都显式声明了 `display`，
  会压过浏览器默认的 `[hidden]{display:none}`。CSS 里补了 `[hidden]{display:none !important}`。
- **IME 组合期必须把渲染权交回 textarea**：着色层在组合期间 `visibility:hidden`，
  textarea 用 `-webkit-text-fill-color` 还原颜色，否则候选窗飘在空白处。
- **一次中文输入 = 一步撤销**：`compositionstart` 压一步、`compositionend` 压一步，
  中间的 `input` 事件不单独记。
- **窗口关闭时轮询还在飞**：JS 桥已经拆了，会抛 `_returnValuesCallbacks.call is not a function`。
  `main.js` 在 `pagehide` 和 `beforeunload` 上都会停表、`call()` 见到 `closing` 直接返回。
  同样的堆栈在**开发态改源码**时也会出现一次——vite HMR 重载页面会冲掉在飞的调用。
  所以跑 `ppx_boot.py` 的时候别同时改 `editor/gui/src/`，否则会看到一次假红。

---

## 边界（不假装支持）

1. 跨域 iframe 里的元素拾取不到（`activeTab` 拿不到跨源子框架注入权）。
2. Shadow DOM 内部元素只报 `shadow: true`，选择器穿不进去。
3. 只处理 UTF-8 文本，非 UTF-8 直接 `NOT_UTF8` 拒绝，不冒「存成乱码」的险。
4. 单文件 8MB、着色 240K 字符上限，超了不着色（仍可编辑）。
5. 不做语法补全 / LSP —— 这是编辑窗口，不是 IDE。
