# M14 浏览器捕获 — BrowserSkill（bsk）单扩展路线

状态：`active`

> 路线调整（2026-09-02，见 `docs/capture-browserskill.md`）：原"自研捕获扩展"改为 **bsk 单扩展路线**。bsk（Tencent/BrowserSkill，MIT）= Rust CLI/daemon + MV3 扩展，扩展主动反连 daemon（ws://127.0.0.1:52800），复用用户真实已登录浏览器、零弹窗。自研扩展降为 evaluate 通道不够用时的兜底；chrome-inspect-ws 维持"零安装备选"。S2 实测：Edge 152 全链路通过（navigate/fill --selector/click/evaluate 回验），ControlOverlay 遮罩已由 elementsFromPoint 变体解出。

## 目标

- 执行传输：`browser.launch` 新增 `transport:"bsk"`，executor 子进程调 `bsk` CLI，session 映射 + 取消时强制 `session stop`；manifest 声明能力差异（css/xpath、仅主 frame）
- 捕获：devserver 经 bsk evaluate 注入 picker（elementsFromPoint 变体）→ 轮询读回 → ElementDescriptor 落库（flow 资产）→ evaluate 回验
- 元素库「＋捕获」入口：面板按钮 → browser（bsk 为主 / persistent 兜底）/ desktop 二选 → 结果直接存入当前流程元素资产；desktop 分支先 attach 目标窗口（ADR 0010，免疫编辑器遮挡）

## 任务

- [ ] bsk 传输执行器：`browser.launch transport:"bsk"`（子进程调 `bsk` CLI；session start/stop 映射；取消时强制 `session stop`）
- [ ] manifest 能力声明：css/xpath、仅主 frame（iframe 走 evaluate 的 document.evaluate 补 XPath，不进本里程碑）
- [ ] devserver 捕获：经 bsk evaluate 注入 picker（elementsFromPoint 变体）→ 轮询 `window.__rpaCaptureResult` → ElementDescriptor（selector+verifyCount+metadata）落库当前流程 elements/ → evaluate 回验命中
- [ ] 元素库「＋捕获」入口（并入切片，接 M13.1 flow 作用域）：面板顶部按钮 → browser（bsk）/ desktop 二选；desktop 分支**先选目标窗口（attach 拿 windowHandle）再走窗口作用域捕获**（免疫编辑器遮挡，ADR 0010），F9 热键提示
- [ ] 合同测试：bsk 子进程协议 mock（无真实浏览器）、session 映射、取消强制 stop、能力差异声明
- [ ] 实机验收：登录态页面捕获（描述符回验命中）+ 真实点选 picker（elementsFromPoint 变体）确认
- [ ] 完整门禁

## 验收标准

- [ ] bsk 会话内连续多次捕获零弹窗（bsk 的 session 模型天然免弹窗）
- [ ] 捕获出合法 selector 且回验命中（登录态页面）
- [ ] bsk 不可用时降级到 persistent / chrome-inspect-ws 不破坏既有路径
- [ ] 取消（cancel）时强制 `session stop`，无悬挂 Agent Window
- [ ] 完整门禁通过

## 范围外

- 自研扩展（仅兜底，设计已备不实装）
- iframe selector 盲区补全（仅主 frame）
- bsk record 模式（语义 trace 范式不符，见 capture-browserskill.md §2.3）
- 反检测缓解（逐键 press/合成轨迹/随机化，设计期捕获低频低风险，后置）

## 待定问题

- bsk 多浏览器在线时的 `--browser` 选择策略（默认单浏览器 / 传 instance_id；编辑器侧可暴露浏览器选择）
- Agent Window 常驻 ControlOverlay 对桌面其他操作的干扰边界（bsk stop 时 detach 回收）

## 完成证据

仅在全部验收标准通过后填写。
