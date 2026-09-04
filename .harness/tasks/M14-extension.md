# M14 浏览器捕获 — BrowserSkill（bsk）单扩展路线

状态：`done`

> 最终形态（2026-09-03 收口）：bsk（执行 + 登录态捕获）+ 自研 content-script 扩展（无缝跨浏览器/跨页捕获主路线）+ 桌面 hover/hybrid。三通道各司其职。

> 路线调整（2026-09-02，见 `docs/capture-browserskill.md`）：原"自研捕获扩展"改为 **bsk 单扩展路线**。bsk（Tencent/BrowserSkill，MIT）= Rust CLI/daemon + MV3 扩展，扩展主动反连 daemon（ws://127.0.0.1:52800），复用用户真实已登录浏览器、零弹窗。自研扩展降为 evaluate 通道不够用时的兜底；chrome-inspect-ws 维持"零安装备选"。S2 实测：Edge 152 全链路通过（navigate/fill --selector/click/evaluate 回验），ControlOverlay 遮罩已由 elementsFromPoint 变体解出。

## 目标

- 执行传输：`browser.launch` 新增 `transport:"bsk"`，executor 子进程调 `bsk` CLI，session 映射 + 取消时强制 `session stop`；manifest 声明能力差异（css/xpath、仅主 frame）
- 捕获：devserver 经 bsk evaluate 注入 picker（elementsFromPoint 变体）→ 轮询读回 → ElementDescriptor 落库（flow 资产）→ evaluate 回验
- 元素库「＋捕获」入口：面板按钮 → browser（bsk 为主 / persistent 兜底）/ desktop 二选 → 结果直接存入当前流程元素资产；desktop 分支先 attach 目标窗口（ADR 0010，免疫编辑器遮挡）

## 任务

- [x] bsk 传输执行器：`browser.launch transport:"bsk"`（子进程调 `bsk` CLI；session start/stop 映射；取消时强制 `session stop`）
- [x] manifest 能力声明：`browser.launch` v1.1.0，transport enum + 差异描述（css/xpath、仅主 frame；iframe XPath 后置）
- [x] devserver 捕获：经 bsk evaluate 注入 picker（elementsFromPoint 变体）→ 轮询 `window.__rpaCaptureResult` → ElementDescriptor（selector+verifyCount+metadata）落库当前流程 elements/ → evaluate 回验命中
- [x] bsk 借用模式（`pageUrl` 子串匹配用户已打开标签页 → picker 注入该页 → 结束归还）——2026-09-03 实装
- [x] 桌面 hover 捕获（`--hover`：悬浮框跟随鼠标、F9/Ctrl+Click 捕获、钻取最深叶子、win32 窗口链兜底虚拟元素、性能节流）——2026-09-03 实装
- [x] 元素编辑确认切片：捕获 → 编辑对话框（改名/改 selector/看 metadata/同名覆盖保护）→ 确认入库；元素库面板点元素名可编辑；bsk 捕获会话 pick 后未 cancel 的资源泄漏顺带修复
- [x] 自研 content-script 捕获扩展（无缝场景：跨浏览器跨页 hover 框选、零逐次授权；HTTP POST 回传 + pending 短轮询（stdlib 无 WS），复用已验证 picker JS；token 配对一次；与桌面 hover 交接）——2026-09-03 实装 + **实机验收通过**（Edge 小红书搜索框 + Chrome 煎蛋导航条，同一捕获会话跨浏览器无缝衔接，落库 demo 流程元素资产）
- [x] 元素库「＋捕获」入口：面板顶部按钮 → 网页（extension 无缝）/ 桌面（hover，已配对自动 hybrid）二选下拉菜单，捕获后经编辑确认对话框入库当前流程元素资产；E2E 覆盖菜单开合
- [x] 合同测试：bsk 子进程协议 mock（无真实浏览器）、session 映射、取消强制 stop、能力差异声明
- [x] 实机验收：登录态页面捕获（描述符回验命中）+ 真实点选 picker（elementsFromPoint 变体）确认
- [x] 完整门禁

## 验收标准

- [x] bsk 会话内连续多次捕获零弹窗（bsk 的 session 模型天然免弹窗）
- [x] 捕获出合法 selector 且回验命中（登录态页面）
- [x] bsk 不可用时降级到 persistent / chrome-inspect-ws 不破坏既有路径（各传输独立通道；extension 现为主路线，persistent/bsk 既有合同测试全绿，pick 超时优雅返回）
- [x] 取消（cancel）时强制 `session stop`，无悬挂 Agent Window
- [x] 完整门禁通过（175 tests）

## 完成证据

- bsk 传输执行器（`executors/browser_bsk.py` + 能力层 `bsk_client.py`），`browser.launch` v1.1.0 transport
- 自研 content-script 捕获扩展（`extension/`），token TOFU 免配对，跨浏览器/跨页无缝 hover 捕获
- 混合捕获（`capture/hybrid.py`，装扩展网页走插件/没装走 UIA，先回传者胜）
- 元素编辑确认对话框（捕获→编辑→入库）+ 元素库「＋捕获」入口
- 桌面 hover 捕获（细粒度钻取 + 性能节流 + 盲区修复）
- 合同/E2E 测试全绿；full gate 175 tests

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
