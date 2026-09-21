# M33 整页 / 元素级截图

状态：`planned`

> **阻塞**：评估已完成，待维护者裁决路线（A/B/C）后再开工。

关联：M29 S3（从 `browser.screenshot` 删掉 `fullPage`/`selector` 留下缺口）、
ADR 0013（移除 Playwright，单扩展通道）、ADR 0015（Native Messaging 桥）、
`docs/element-mvp-boundaries.md` §2.12（截图是窗口可见区）、
`docs/extension-channel-baseline.md`（通道往返基线）

## 1. 现状（已核实）

`browser.screenshot` 的链路只有**一跳**：

```
执行器 browser.py:1031
  → self._ext.screenshot(tab_id)                      ← 扩展 batch 请求
    → extension background.js:387 case "screenshot"
      → chrome.tabs.captureVisibleTab(tab.windowId)   ← 唯一的取图 API
        → 返回 dataUrl（PNG base64）
  ← 执行器 base64 解码 → _write_screenshot 落盘
```

**`captureVisibleTab` 的固有限制**（Chrome 官方契约，非我们的实现问题）：

| 限制 | 后果 |
|---|---|
| 只能截「当前活动标签页」 | 后台标签页截不到（须先 activate） |
| 只能截「视口可见区」 | 整页需滚动分段拼接；元素需按 rect 裁剪 |
| 返回格式只有 PNG/JPEG 的 dataUrl | **无法在扩展里做像素级裁剪**——需要解码图像 |

## 2. 核心阻塞：MV3 service worker 没有图像解码能力

`background.js` 跑在 MV3 service worker 里，**没有 `Image` / `FileReader` / `document`**。
所以「先在扩展里把 dataUrl 解码成 canvas、按 rect 裁剪、再编码回 PNG」这条路**直接不通**。

三条可行路线，各有代价：

### 路线 A：offscreen document（推荐）

MV3 提供的 `chrome.offscreen` API，可以创建一个**真正的 DOM 环境**（隐藏文档），
在那里拿到 `Image` / `canvas` / `OffscreenCanvas`，做解码与裁剪。

| 项 | 评估 |
|---|---|
| 能力覆盖 | 元素裁剪 ✅、整页拼接 ✅（分段截 → 在 offscreen 里拼） |
| 权限 | 需 `offscreen` 权限（manifest 加一行，**非敏感权限**，不触发商店额外审核） |
| 用户可见影响 | 无（offscreen document 不显示） |
| 实现量 | 中：新增 `offscreen.html` + `offscreen.js`，扩展侧加一条消息通道 |
| 已知坑 | ① offscreen document **同时只能有一个**，要管理生命周期（`hasDocument()` / `closeDocument()`）；② 只能存在于 service worker 生命周期内，**SW 被回收时 offscreen 也会关**——长时间整页截图要防中途被回收；③ 与 service worker 的通信仍是消息传递，不是直接调用 |
| 确定性 | 高（纯本地图像操作，无外部依赖） |

### 路线 B：CDP `Page.captureScreenshot`

`chrome.debugger` 附加到标签页后，CDP 原生支持 `captureBeyondViewport` 与 `clip` 参数，
**一次调用就能拿整页或元素区**，不需要自己拼接。

| 项 | 评估 |
|---|---|
| 能力覆盖 | 元素裁剪 ✅、整页 ✅（原生参数，无需拼接） |
| 权限 | 需 `debugger` 权限 —— **这是敏感权限**，会显示「调试浏览器」的警告，且与 ADR 0013 的「无 CDP」定位**直接冲突** |
| 用户可见影响 | 标签页顶部出现黄色调试横幅（CDP 附加的既知表现），**用户能看见** |
| 实现量 | 小（一次调用） |
| 结论 | **与项目定位冲突，不建议**。ADR 0013 明确以「无 CDP、不可检测性更好」为通道卖点，用 `debugger` 权限会把这个卖点抵消掉 |

### 路线 C：执行器侧（Python）拼接

扩展只负责「按需滚动 + 分段截图」，把多张 dataUrl 回传，**Python 侧用 Pillow 拼接/裁剪**。

| 项 | 评估 |
|---|---|
| 能力覆盖 | 元素裁剪 ✅、整页 ✅ |
| 权限 | 无需新权限（仍只用 `captureVisibleTab`） |
| 代价 | ① 新增 Python 依赖 **Pillow**（当前 pyproject 只有 pydantic/jsonschema/pywinauto，这是个**真新增的重依赖**）；② 多图回传经本地端点，**传输量成倍增大**（整页 5 屏 = 5 张全分辨率 PNG 走 IPC）；③ 滚动-截图-回传的时序由执行器驱动，往返次数与 `extension-channel-baseline.md` 记的通道成本直接相乘 |
| 确定性 | 中（滚动位置与拼接接缝要自己保证，sticky/fixed 元素会重复出现） |

## 3. 三条路线共同的难点（与选哪条无关）

1. **sticky / fixed 元素**：整页分段截时，固定定位元素会在**每一段里重复出现**。
   正确做法是分段截图时临时把 `position: sticky/fixed` 改成 `static`（需注入 CSS），
   或接受重复并在文档里写清。
2. **懒加载**：滚动到某段时图片才开始加载，可能截到空白占位。
   需要在每段滚动后等待稳定（或强制触发所有懒加载）。
3. **`devicePixelRatio`**：Retina 下 `captureVisibleTab` 返回物理像素，
   而 `getBoundingClientRect` 是逻辑像素——**元素裁剪的 rect 必须乘 DPR**，否则裁错位置。
   这一条最容易漏，且在小数 DPR（如 1.25 / 1.5 的 Windows 缩放）下误差会累积。
4. **元素在视口外**：元素级截图要先 `scrollIntoView`，且元素可能**大于视口**（需分段）。

## 4. 初步建议（待维护者裁决）

**倾向路线 A（offscreen document）**，理由：

- 不引入新权限类型（`offscreen` 是能力权限，不是警告权限）；
- 图像处理留在扩展侧，**不新增 Python 依赖**，也不放大 IPC 传输量；
- 与 ADR 0013 的「无 CDP」定位不冲突。

**但要如实记下它的代价**：offscreen document 的生命周期管理（同时只能一个、
SW 回收会连带关闭），以及整页截图的耗时（分段 + 拼接 + 等待懒加载）会明显长于现在的单次
`captureVisibleTab`，需要一个合理的超时与取消路径。

**范围建议**：先做**元素级截图**（`selector`），它不需要分段拼接，
只需「scrollIntoView → 取 rect → 单张截图 → 按 DPR 缩放后裁剪」——
把 DPR 与裁剪坐标系这个最容易错的点先做对；整页（`fullPage`）作为第二步。

## 5. 验收口径（若立项）

- `selector` 给的元素：截图内容**只含该元素区域**（含边界断言，验证 rect × DPR 正确）；
- 元素大于视口时：分段截图并拼接，或**显式报错说明不支持**（不静默截一半）；
- `fullPage`：内容高度与文档 `scrollHeight` 一致（允许 ±1px 接缝误差）；
- sticky/fixed 元素的处理方式**写进文档**（是临时改 static 还是接受重复）；
- 后台标签页：先 activate 再截（并在文档里写明这一步的副作用：会切换用户可见的标签页）。

## 6. 明确的非目标

- 不引入 CDP（ADR 0013）；
- 不做「截整个桌面」（那是 `desktop.screenshot` 的范畴，与浏览器通道无关）；
- 不做视频录制。
