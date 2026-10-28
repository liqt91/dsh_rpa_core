# WorkBuddy Studio · 上架材料

一份把「传什么、怎么填、会被问什么」写全的上架包。三个上传物，各自独立又互相咬合。

## 一、这次要传什么

| 上传物 | 文件 | 平台入口 | 说明 |
|---|---|---|---|
| 技能包 | `dist/workflow-studio-skill.zip`（15.3 KB） | 发布管理 → 技能 | **folder 布局**：`workflow-studio/SKILL.md`（官方「技能基础结构」要求套一层） |
| 连接器 | `dist/rpa-core-connector.zip`（23.6 KB） | 发布管理 → 连接器（CLI + Skill） | `connector-meta.json` + `cli.json` + `icon.svg` + `skills/rpa-automation/` |
| 浏览器扩展 | `dist/workbuddy-studio-extension.zip`（21.9 KB） | 扩展分发（Chrome / Edge 商店，或内部分发） | MV3，解压后「加载已解压的扩展」也能用 |
| 编辑窗口 | `editor/`（PPX 工程）→ `ppx build` | 技能附件 / 官网下载 | 便携版 `editor/build/WorkBuddy Studio/`（约 28 MB）；本机没装 Inno Setup 6，未出安装包 |

> 生成命令：`python tools/pack.py`（一条命令打三个 zip，并断言包内结构）。
> 技能包默认 folder 布局；`--layout flat` 只在控制台亲测要求 flat 时用。

## 二、市场展示信息

| 字段 | 内容 | 约束核对 |
|---|---|---|
| 技能名称（`name`） | `workflow-studio` | 小写 + 连字符 ✅ |
| 展示名称（`display_name`） | `WorkBuddyStudio` | 无空格、无特殊字符（付费资产硬规则）✅ |
| 英文展示名 | `WorkBuddyStudio` | ✅ |
| 版本 | `1.0.0` | 语义化 ✅ |
| 作者 | `w` | ✅ |
| 分类 | 开发工具 | 与 `category` 一致 |
| 服务类目 | 商业服务 - 软件/建站/技术开发 | 建议 1~2 个，别贪多 |
| 标签 | 人机协作、文件编辑、元素定位、RPA、浏览器插件 | ≤ 5 个 |

**一句话简介（中文，≤ 60 字）**

> 让人改文件、让人点元素：AI 不再猜，改完的结果直接回到对话里。

**一句话简介（英文）**

> Human-in-the-loop for agents: edit files in a real editor window, pick page elements in the browser, get structured results back.

**详细描述（中文）**

> 大多数「AI 帮我改配置」的失败，不是模型不够聪明，而是它替人做了只有人能做的判断：
> 哪个缩进是对的、页面上到底哪个按钮是「提交」。
>
> WorkBuddy Studio 把这两件事交回给人，但不用人再去开软件、找路径、复制粘贴：
>
> - **轻量编辑窗口**：Ctrl+O 打开文件，或是 Agent 直接把文件推到窗口里。原生光标、选区、
>   撤销重做、Tab 多行缩进、Ctrl+/ 注释、Ctrl+F 查找、中文输入法全部按编辑器的习惯来。
>   保存的一瞬间，窗口把 sha、字节数、校验结果交回 Agent。
> - **浏览器拾取**：在页面上点一个元素，插件算出稳定的选择器（优先 id / data-testid，
>   跳过构建产物类名，退化路径至少两级），并把候选方案、尺寸、属性一起交回。
> - **会话协议**：两个客户端都只连本机 `127.0.0.1`，Agent 侧一条命令拿结果，不轮询屏幕、
>   不截图识别。
>
> 三件套可以只用其中一件：只装技能，Agent 会退化成「贴 diff 让你确认」；只装插件，
> 它就是一个老实的选择器生成器。

**详细描述（英文）**

> WorkBuddy Studio gives agents a human-in-the-loop bridge. A lightweight PPX editor window
> opens files (pushed by the agent or picked by the user), keeps native cursor/selection/undo/IME
> behaviour, and reports saves back with a content hash and validation result. A Manifest V3
> extension lets the user click one element on a page and returns a stable selector plus
> fallbacks and metadata. Both talk to a loopback-only session service, so nothing leaves the machine.

## 二·补 连接器（`connector-meta.json`，直接进市场）

| 字段 | 值 |
|---|---|
| `name` / `name_zh` / `name_en` | RPA Core / RPA 自动化 / RPA Core |
| `source` | `rpa-core`（kebab-case，全局唯一） |
| `type` | `cli` |
| `version` | `0.2.0` |
| `minWorkbuddyVersion` | `5.0.0`（用了 `runtime: python`，该字段 5.0.0 起支持） |
| 中文描述 | 把 89 条现成原子能力编排成工作流并确定性运行：浏览器、Windows 桌面、数据与 OCR。 |
| 英文描述 | Orchestrate 89 typed atomic actions into validated, deterministic workflows. |
| 使用示例 | 各 4 条（中英），见 `connector/connector-meta.json` |

**安装与状态**（`cli.json`）：三平台都声明的 `init` = `python -m pip install -U rpa-core-runtime`；
`auth` / `unAuth` 无需登录、幂等返回；`status` 输出 `{"status":"ready","version":…,"auth":"none"}`，
`statusMatch` 用正则 `"status"\s*:\s*"ready"`（不是死字符串，换行/缩进变了也不会判失败）。

## 三、截图（`listing/screenshots/`）

| 文件 | 内容 | 建议配文 |
|---|---|---|
| `editor-window.png` | **`ppx build` 打包后的应用**真机窗口：Agent 推来的 `weekly-report.json` 已打开，会话在线 | 文件是 Agent 推过来的，改完 Ctrl+S，它就知道 |
| `editor-browser.png` | 同一套界面的纯前端视图（JSON 着色、行号、状态栏） | 光标、选区、撤销、多行缩进，都是原生手感 |
| `extension-popup.png` | 扩展弹窗：Agent 正在等待拾取 | 「Agent 在等元素」时徽标会变黄，点一下就能选 |

## 四、权限说明（扩展，逐条）

| 权限 | 用途 | 为什么不能更小 |
|---|---|---|
| `activeTab` | 点「开始拾取」时获得当前标签页的临时访问权 | 比 `<all_urls>` 小得多；不点就不注入 |
| `scripting` | 按需注入 `content.js` | 若改成常驻 content script，会变成「每个页面都跑」，反而更大 |
| `storage` | 记住端口号与最近 8 条拾取记录 | 只存本机，`chrome.storage.local` |
| `contextMenus` | 右键「把此元素交给 WorkBuddy Agent」 | 无替代方案 |
| `alarms` | 每 30 秒探一次本地会话，好让工具栏徽标显示「在线 / Agent 在等」 | 这是扩展唯一的定时任务；缺了它徽标不刷新（曾经就是漏了这个） |
| `host_permissions`: `http://127.0.0.1/*`、`http://localhost/*` | 与本地会话服务通信 | 仅回环，不含任何公网域 |

**没有任何**：`tabs`、`webRequest`、`cookies`、`history`、`downloads`、`<all_urls>`。

## 五、数据与隐私

- 全部通信只发生在 `127.0.0.1`，不发起任何公网请求（代码里没有外部 URL）。
- 拾取只上报**被点中的那一个元素**：选择器、标签、文本（截断 120 字）、属性、尺寸、页面 URL。
  不上报页面正文、不截图、不遍历 DOM。
- 本地落盘：`%TEMP%\workbuddy-studio\`（`endpoint.json`、`events.jsonl`）。删掉该目录即完全清除。
- 见 `listing/privacy.md`。

## 六、用户安装路径（写进详情页）

1. 在 WorkBuddy 里安装技能 `WorkBuddyStudio`；
2. 解压 `workbuddy-studio-extension.zip` → `edge://extensions` 或 `chrome://extensions` → 开发者模式 →
   加载已解压的扩展；
3. 双击 `WorkBuddyStudio.exe`（PPX 打包产物），或在终端 `python scripts/studio.py gui`。

日常用法只有两句话：

- 「把这个文件打开，我改一下」→ Agent 推文件进窗口，你改完保存，它接着干；
- 「帮我把登录框拾取一下」→ Agent 进入等待，你点一下页面元素，选择器回到对话里。

## 七、上架检查清单

技能包

- [x] `SKILL.md` 在 `workflow-studio/` 目录下（官方结构 `{skill-name}/SKILL.md`），包结构由 `tools/pack.py` 断言
- [x] frontmatter 必填字段齐全：`name` / `display_name` / `display_name_en` / `description` /
      `description_zh` / `description_en` / `examples_zh` / `examples_en` / `version` / `author`
- [x] `name` 小写连字符，与目录名一致
- [x] `description` 写清「做什么 + 何时触发」，列了 10 个以上触发词
- [x] 正文引用的 `scripts/`、`references/` 全部存在（打包时逐个核过）
- [x] 无硬编码 token / 密钥；无「下载并执行」类指令
- [x] zip ≤ 3MB（实际 15.3 KB）

连接器

- [x] `connector-meta.json` 中英名称/描述/示例齐全，`source` 全局唯一、`type: cli`
- [x] `cli.json` 三平台 `init/auth/unAuth/status` 齐全，`statusMatch` 是合法正则
- [x] `icon.svg` 64×64（显式宽高，正方形）
- [x] `skills/rpa-automation/SKILL.md` 覆盖全部 89 条能力的入口与参数查法
- [x] 能力文档与 `commands/` 的 digest 一致（`pack_connector.py --check` 钉住）
- [x] 未在任何文件写真实凭证

扩展

- [x] 扩展 manifest 引用的文件全部存在；图标 16/32/48/128 齐全
- [x] 权限最小集，`host_permissions` 仅回环；**用到的 `chrome.*` API 都有权限声明**（门禁扫代码）
- [x] 商店描述 ≤ 132 字符
- [x] 三张截图 + 配文
- [x] 隐私政策（`listing/privacy.md`）
- [x] 头像 512×512（`editor/ppx/assets/logo.png`，PNG，< 500 KB）

## 八、已知限制（主动写出来，比被问出来好）

1. **跨域 iframe 内的元素拾取不到**：`activeTab` 拿不到跨源子框架的注入权。拾到 `<iframe>`
   本身可以，进去不行。技能里明确写了这条，不假装支持。
2. **Shadow DOM 里的元素**：上报里给三样东西 —— `shadow: true`、`selector`（在该 shadow root 内
   唯一）、`shadowHost.selector`（宿主怎么找）。纯 CSS 穿不过去，调用方要用
   `host.shadowRoot.querySelector(inner)`；我们**不假装**返回一个能直接用的字符串。
3. **只处理 UTF-8 文本**：非 UTF-8 文件会被拒绝（`NOT_UTF8`），而不是「打开后保存成乱码」。
4. **单文件 8MB / 着色 240K 字符上限**：超过就不着色（仍可编辑），轻量窗口不背大文件。
5. **编辑器不做语法补全 / LSP**：这是编辑窗口，不是 IDE。要补全请用 IDE。
6. **只提供便携版，且未做代码签名**：`editor/build/WorkBuddy Studio/` 双击即用；首次运行
   Windows SmartScreen 会提示「未知发布者」，点「更多信息 → 仍要运行」即可。要安装包与签名，
   需要 Inno Setup 6 + 一张代码签名证书。

## 九、审核可能会问

**Q：为什么不直接让 Agent 改文件？**
A：Agent 改完无法确认「人是不是同意这一版」。本技能把「人保存」当成唯一的确认信号，
并把 sha 交回，Agent 不会拿旧内容继续推理。

**Q：插件是不是在偷偷采集页面？**
A：注入是按次触发的（`activeTab` + `scripting`），一次点击只上报一个元素的属性，
代码里没有遍历 DOM 的循环、没有外部域名。可在 `extension/content.js` 直接核对。

**Q：为什么需要本地 HTTP 服务？**
A：窗口、扩展、Agent 是三个进程。用一个只绑回环的小服务做会合点，比让扩展去连 native
messaging host 更可审计（协议就是 5 个路由，`references/protocol.md` 有全表）。

**Q：Skill 会不会自己写文件？**
A：不会。写盘只发生在用户在窗口里按 Ctrl+S 时；Agent 侧没有写文件的工具调用。
