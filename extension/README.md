# rpa_core 捕获扩展

MV3 零构建 content-script 扩展：捕获模式下在任意浏览器（Chrome/Edge）任意页面 hover 高亮、Ctrl+Click 捕获元素描述符回传 rpa_core devserver。无缝跨浏览器/跨页（无标签页借用、无逐次授权）。

## 安装（开发者模式）

1. Chrome：`chrome://extensions` → 开「开发者模式」→「加载已解压的扩展程序」→ 选本目录
2. Edge：`edge://extensions` → 同上
3. 两个浏览器都装 → 跨浏览器无缝捕获

> Windows 自动静默安装见 `docs/extension-install.md`：`rpa-core install-extension`
> （外部扩展注册表路线；Chrome/Edge 152 实测安装后需在扩展页点一次启用，零点击需商店上架，
> 见文档 §6.5）。

## 配对（自动，零操作）

扩展首次轮询 devserver 时自动生成 token 并携带——devserver **TOFU（首次接触自动采纳）**并持久化到 `workflows/.capture-extension-token`，之后只认这个 token。无需任何手动配对操作。popup 里可查看 token / 重新生成（重新生成后需删除 devserver 的 `.capture-extension-token` 文件再轮换）。

## 使用

- 编辑器元素库「＋捕获」（或 `rpa-core capture browser --transport extension`）发起捕获 → 扩展 content script 在所有页面激活 hover 高亮 → 鼠标移到目标 Ctrl+Click 捕获 → 描述符回传编辑器（可改名/改 selector 后入库）
- Esc 取消；普通点击不捕获（可正常导航）
- 网页 DOM 内容归本扩展；浏览器 UI 骨架（标签栏/工具栏）与桌面应用归桌面 hover 捕获（`rpa-core capture desktop --hover`）

## 协议（自测用）

- `GET /api/capture/extension/pending`（头 `X-Capture-Token`）→ `{pending, sessionId}`
- `POST /api/capture/extension/result`（头 `X-Capture-Token`）body `{sessionId, descriptor}`
- 短轮询：捕获激活 1.2s / 空闲 5s
