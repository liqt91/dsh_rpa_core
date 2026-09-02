# M14 自研捕获扩展（实装 M10c 设计）

状态：`active`

## 背景与动机

chrome-inspect-ws（chrome://inspect 授权开关）已验证可用且开关跨重启持久，但实测暴露运维痛点：**每次 WebSocket 连接都会弹窗要求用户再次确认**，在高频捕获会话中构成疲劳。M10c 的设计（token 配对反连 dev server WebSocket，一次配置免弹窗）正是为此准备——本里程碑把它提前实装。

设计依据：`docs/capture-transport.md` §2.5（M10c 弹窗成本矩阵已定稿：扩展 + token 配对 = 一次配置之后自动；原生消息 = 零弹窗）。

## 目标

实装自研 MV3 捕获扩展，作为 `user-browser` 登录态捕获的优先传输：

- 扩展一次安装、一次 token 配置，之后连接零弹窗
- dev server 捕获端点新增 `extension-ws` 传输子类型
- 与既有 chrome-inspect-ws（弹窗式）并存，扩展为主、inspect 为备

## 任务

- [ ] 扩展骨架（MV3，零构建 vanilla JS）：content script hover 高亮 + 点击采集 + Esc 取消（与 M10 picker 同一协议）
- [ ] 反连机制：扩展启动后主动 WebSocket 连 dev server（`ws://127.0.0.1:8765/capture-extension`），持 token 配对；token 在扩展 popup 展示，编辑器侧配置一次存入 dev server
- [ ] 授权边界：标签页粒度授权（借鉴 Playwright MCP 标签组 UX——哪个 tab 可达由用户拖拽/选择决定，默认仅当前 tab）
- [ ] dev server 新增 WebSocket 接入点与扩展消息路由（`capture/browser/*` 增加 `extension-ws` transport）
- [ ] 元素库「＋捕获」入口（并入切片，接 M13.1 flow 作用域）：面板顶部按钮 → browser（扩展为主/persistent 兜底）/ desktop（F9 热键提示）二选 → 捕获结果直接存入当前流程元素资产
- [ ] 图标与清单（manifest.json、16/48/128 图标）
- [ ] 合同测试：扩展消息协议 mock（无真实浏览器）、token 校验、标签页授权范围
- [ ] 实机验收：小红书登录态页面捕获（用户已登录）→ 描述符命中回验
- [ ] 完整门禁

## 验收标准

- [ ] 装扩展 + 配 token 一次后，连续多次捕获会话零弹窗
- [ ] 标签页授权粒度正确（未授权 tab 捕获请求被拒）
- [ ] 登录态页面（如小红书）捕获出合法 selector 且回验命中
- [ ] chrome-inspect-ws 路径不回退（扩展不可用时仍走弹窗式）
- [ ] 完整门禁通过

## 范围外

- Web Store 上架（本里程碑仅开发者模式加载）
- Native Messaging 备选实现（仅当反连模式证明不足）
- 扩展的运行时操控能力（点击/导航等——捕获是只读场景）

## 待定问题

- 扩展与编辑器的 token 交换方式：扩展 popup 显示 token → 用户粘贴到编辑器一次（本地存储）vs dev server 配对二维码？倾向前者（简单）
- 同一 dev server 同时挂几个扩展连接？（倾向：单连接，新连接顶掉旧连接并提示）

## 完成证据

仅在全部验收标准通过后填写。
