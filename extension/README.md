# rpa_core 扩展（捕获 + 执行通道）

MV3 零构建扩展（Chrome/Edge），两条通道均经 **Native Messaging 长连接**（ADR 0015）：

1. **捕获通道**：捕获模式下在任意页面 hover 高亮、手势点选捕获元素描述符回传 rpa_core。
2. **执行通道（一等公民，唯一通道）**：在**用户真实已登录浏览器**里执行浏览器自动化
   （tabs / scripting / cookies / webNavigation）。rpa_core 的 `browser.*` 命令统一走本通道；
   `playwright` / `bsk` 已彻底移除，无回退。

扩展经 `chrome.runtime.connectNative("com.rpa_core.ext_bridge")` 连本机 bridge host；host 由
**浏览器按需拉起**（无 8765 常驻服务、无 HTTP 轮询）。「bridge 已连接」= 浏览器指令可用。

## 安装（开发者模式 + 注册 host）

1. 注册 host（免管理员；`rpa-core install-extension` 默认引导会自动注册）：
   ```powershell
   rpa-core install-extension --browser both
   ```
   等价于在 `HKCU\Software\<vendor>\<browser>\NativeMessagingHosts\com.rpa_core.ext_bridge`
   写 host manifest 路径（manifest 落 `~/.rpa-core/native-host/`；macOS/Linux 写浏览器
   `NativeMessagingHosts/` 目录）。
2. Chrome：`chrome://extensions` → 开「开发者模式」→「加载已解压的扩展程序」→ 选本目录
3. Edge：`edge://extensions` → 同上
4. 两个浏览器都装 → 跨浏览器无缝捕获

> host 名固定 `com.rpa_core.ext_bridge`；host 入口是 venv 内的 `rpa-core-ext-host`
> （**无参数可执行文件**——manifest 的 `path` 不能写成带参数的命令行，S0 实测 Chromium
> 不解析参数，会把解释器无参拉起）。
> chrome:// 与 edge:// 扩展页需手动在地址栏输入（外部无法直接导航）。详见
> `docs/extension-install.md` §6.5.1。

## 连接（无需配对）

无 token 配对：host 仅是本机子进程，扩展与 host 之间经浏览器强制的
`nativeMessaging` 权限 + host manifest `allowed_origins`（扩展 ID 白名单）鉴权。
扩展加载后自动连接；断开按 3s 退避重连，SW 被回收时由 30s alarm 兜底拉起。

## 使用

- 编辑器元素库「＋捕获」（或 `rpa-core capture browser --transport extension`）发起捕获 →
  扩展 content script 在所有页面激活 hover 高亮 → 鼠标移到目标后用手势捕获 →
  描述符回传编辑器（可改名/改 selector 后入库）
- **捕获手势**（三者等价）：`⌘+单击`（macOS）/ `Ctrl+单击`（Windows/Linux）、**右键单击**
  （含触控板双指点按）、以及 macOS 的 `Ctrl+Click`。
  > macOS 在**系统层**把 `Ctrl+Click` 改写成"次要点击（右键）"，浏览器因此只派发
  > `contextmenu`，**永远不会**派发 `ctrlKey===true` 的 `click` —— 所以 Mac 用户必须用
  > `⌘`、或直接右键；页内红框旁的提示条会写明当前平台该用哪个手势。
- 捕获态是模态的：左键/右键都归捕获，系统右键菜单被屏蔽
  （普通点击不捕获、可正常导航；未按修饰键的单击会在提示条上回显系统实际派发的事件）
- Esc 取消；若提示条提示"扩展已重载"，说明本页脚本与扩展的通道已断 → 刷新页面即可
- 网页 DOM 内容归本扩展；浏览器 UI 骨架（标签栏/工具栏）与桌面应用归桌面 hover 捕获
  （`rpa-core capture desktop --hover`）

## 捕获描述符形态

`capture_result.descriptor` 即元素库落盘的 ElementDescriptor 文档：

```json
{
  "kind": "browser",
  "selector": {
    "css": "#sb_form_q",
    "candidates": [
      {"kind": "attribute", "selector": "input[name=\"q\"]", "matchedCount": 1}
    ]
  },
  "verifyCount": 1,
  "metadata": {
    "tag": "textarea", "id": "sb_form_q", "classes": ["sb_form_q"],
    "text": "", "rect": {"x": 215, "y": 183, "width": 843, "height": 22},
    "role": "searchbox", "accessibleName": "搜索", "placeholder": "输入搜索内容",
    "label": null, "containerText": "主页 搜索",
    "url": "https://www.bing.com/", "title": "Bing"
  }
}
```

- **`selector.css`（必填、第一顺位）**：语义与本字段引入前**完全一致**，运行时就只吃这一个
  字符串（`executors/browser.py` 的 `selector` 输入）。既有工作流与既有元素文档不受影响。
- **`selector.candidates`（可选，新增）**：捕获时额外收集的 **CSS 可解析**备选定位，按稳定性
  排序（`data-testid`/`data-test`/`data-qa` → `name` → `aria-label` → `placeholder` → `title`），
  每条带捕获时**实测** `matchedCount`；与主 css 重复、或捕获时就不命中的不收。
  形状与影刀导出格式一致（`kind` + `selector`）。**运行时暂不消费**——留给元素改版后的自愈排序；
  `rpa-core elements verify` 会校验其结构。
- **`metadata` 语义特征（新增）**：`role`（归一化到 14 个规范角色）、`accessibleName`
  （ARIA 优先级链：`aria-labelledby` → `aria-label` → `<label>` → value → alt → 文本 →
  title → placeholder）、`placeholder`、`label`、`containerText`（所属表单/对话框/列表项文本，
  用于同名元素消歧）、`url`/`title`（页面指纹）。

> 新字段一律放在 `selector` 或 `metadata` **内部**：描述符顶层的未知键会被 pydantic 的
> `extra="ignore"` 静默丢弃（顶层 `url` 曾长期回传却从未落盘）。
> 备选定位存的是**页面自身的属性**，不是"第几个节点"——序号只活当次快照，页面一变即失效。

## 协议（自测用）

Native Messaging 帧：4 字节小端长度前缀 + UTF-8 JSON（与 host 的 stdio 同构）。

扩展 → host：

- `hello` `{browser, instanceId, extVersion, version, platform, userAgent, focused, focusedAt}`
  （连接后首帧；host 据此命名本地端点
  `rpa_core_ext_<browser>_<sha256(instanceId)[:16]>` —— 实例段是**定长 token**，
  `instanceId` 长度不可控，直接拼名会顶穿 POSIX `sun_path` 的 103 字节上限）
- `result` `{id, ok, value}` 或 `{id, ok:false, error:{code,message}}`
  （host 转发给执行器时会补带 `instanceId`，供会话绑定与实例级路由）
- `capture_result` `{sessionId, descriptor}` 或 `{sessionId, cancelled:true}`
- `focus` `{focused, focusedAt}`（窗口焦点变化时上报）
- `pong`（应答 host 的 `ping`）

host → 扩展：

- `ready` `{browser, instanceId, endpoint, pid, ...}`（host 绑定端点后）
- `command` `{id, op, args}` → 扩展执行后回 `result`
- `capture_arm` `{sessionId}` / `capture_disarm` → 广播到全部标签页
- `cancel` `{id}`（宿主超时后 best-effort；迟到结果由 host 丢弃）
- `ping` `{seq}`

执行权限：默认整个浏览器；收窄模式与校验点见下。

## 用哪个浏览器

`browser.*` 命令统一走自研扩展单通道：**扩展装在哪个浏览器，执行就在哪个浏览器**
（没有"启动/切换浏览器"概念）。`transport` / `channel` 参数已随 playwright 一并移除，
`browser.navigate` 不再暴露。

> 想操作浏览器（如 Edge）上的真实已登录会话：把本插件装到 Edge，其余留空即可。

插件弹窗显示宿主浏览器与 bridge 连接状态；编辑器侧状态由后续切片接入（S7）。

## 执行权限（默认：整个浏览器）

- 默认 `{"mode": "browser"}`：全部窗口、全部标签页、全部 Cookie —— 与「扩展是一等公民」的
  定位一致，命令不做范围限制。
- 预留收窄模式（接口已立，两端同名同语义）：
  - `{"mode": "tabs", "tabIds": ["123"]}`：仅允许操作指定标签页
  - `{"mode": "origins", "allow": ["https://example.com"]}`：仅允许指定站点
- 校验点：扩展 `assertAllowed()`（执行前拦截；宿主侧权限下发随 S5 落地）。
- popup 可选切换模式；`tabIds` / `allow` 列表由宿主或人工写入
  `chrome.storage.local.rpaExecPermission`。

> 注意：扩展不代管用户标签页生命周期——`browser.close` 对扩展会话只做解绑，不关闭用户的
> 标签页。
