# rpa_core 捕获扩展

MV3 零构建 content-script 扩展：捕获模式下在任意浏览器（Chrome/Edge）任意页面 hover 高亮、Ctrl+Click 捕获元素描述符回传 rpa_core devserver。无缝跨浏览器/跨页（无标签页借用、无逐次授权）。

## 安装（开发者模式）

1. Chrome：`chrome://extensions` → 开「开发者模式」→「加载已解压的扩展程序」→ 选本目录
2. Edge：`edge://extensions` → 同上
3. 两个浏览器都装 → 跨浏览器无缝捕获

## 配对（一次性）

1. 编辑器所在机器起 devserver（`rpa-core devserver`，默认 8765）
2. 扩展 popup（点工具栏图标）→「生成新 token」→ 复制
3. devserver 侧写入：`POST /api/capture/extension/token` body `{"token": "<粘贴>"}`；或编辑器元素库捕获入口自动带上
4. 配对后零弹窗；token 存 devserver `workflows/.capture-extension-token` + 扩展 `chrome.storage.local`

## 使用

- 编辑器元素库「＋捕获」（或 `rpa-core capture browser --transport extension`）发起捕获 → 扩展 content script 在所有页面激活 hover 高亮 → 鼠标移到目标 Ctrl+Click 捕获 → 描述符回传编辑器（可改名/改 selector 后入库）
- Esc 取消；普通点击不捕获（可正常导航）
- 网页 DOM 内容归本扩展；浏览器 UI 骨架（标签栏/工具栏）与桌面应用归桌面 hover 捕获（`rpa-core capture desktop --hover`）

## 协议（自测用）

- `GET /api/capture/extension/pending`（头 `X-Capture-Token`）→ `{pending, sessionId}`
- `POST /api/capture/extension/result`（头 `X-Capture-Token`）body `{sessionId, descriptor}`
- 短轮询：捕获激活 1.2s / 空闲 5s
