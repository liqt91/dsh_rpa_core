# M77：捕获期显式图像 proposal（把自绘控件变成可入库元素）

Status: `done`

> **2026-11-17 立项**。M75 只给了运行期 `desktop.imageLocate`，但捕获期
> `desktop_agent._locator_candidates` 仍只从 UIA 三属性产候选——微信自绘控件
> 三属性全空 ⇒ 根本走不进元素库。本任务补「用户显式按图捕获」这条入口，
> 让图像 locator 在捕获确认框里成为可保存的主定位。
>
> 约束：ADR 0003（禁 UIA→坐标静默降级）、单一权威（不改 `_locator_candidates`
> 的语义候选口径）、ADR 0019（Qt 冻结，UI 只在 PPX 原型）。

## 一、可验证目标

| 目标 | 可验证形态 |
|---|---|
| 显式图像捕获有入口 | CLI `capture desktop --image`；PPX 资产工具区「按图捕获」按钮；不点就绝不产图像 proposal |
| 自绘控件能入库 | 截图裁光标周边模板 → `selector.locator` 是 `driver=image` 且 `imageTemplate/templateSize/windowHandle` 齐备，能被 M75 守卫放行 |
| 不冒充实测 | proposal 的 `matchedCount=0`、`proposal=true`；`verifyCount` 不因 proposal 变成 1 |
| 单一权威 | 语义候选仍由 `_locator_candidates` 产出，图像 proposal 只是追加记录，不改语义分派 |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | `capture/image_proposal.py`：复用 `screen_shot.grab_window`，PIL 裁光标周边正方形 → base64 模板 | 替身截图下 templateSize / 像素 / driver 等值；越界/坏图/非法句柄返回 None | done |
| S2 | 捕获主路径接线：`desktop_agent` 的 `--image` → `capture_at`/`_describe_info`/hover 快路径；`DesktopCaptureSession` 透传 | 显式模式才产 proposal；`matchedCount=0`；非显式路径逐字不变 | done |
| S3 | 用户入口：CLI `--image`、PPX `rpa.startCapture(image=...)` + 「按图捕获」按钮 | CLI/UI 判据端到端钉住 image=true；dist 重建 | done |
| S4 | 收口：文档 + 台账 + 全门禁 | 负向 7/7 精确红、md5 还原；FULL GATE PASSED | done |

## 三、判据总则

1. 显式开关是生死线：去掉任何一层透传都必须红（负向 A1/D1/E1/F1）。
2. 等值断言，不写 `>=`：driver/backend 严格 `image`，`templateSize`/`matchedCount` 等值。
3. proposal 不冒充实测命中；与 M75 运行期图像定位共用 locator 形状但不共用「命中」语义。
4. 不碰 GUI 冻结源码；PPX 原型改动在 `.harness/demo/ppx_probe/` 内。

## 四、不在范围

- 模板版本管理、DPI 自适应重采样、遮挡剔除（真机复验后另立）。
- HLay/Java 注入造控件树；AI 语义锚点。
