# M82：图像元素的执行面（捕获出来的 image locator 真的能点）

Status: `done`

> **2026-11-17 立项**。维护者追问「没有集成到当前的捕获功能中吗？」——查接线后确认两件事：
> ① 捕获/落盘/校验都接了图像，但**编排器与宿主 UI 一个入口都没有**；
> ② 更硬的是执行侧：`desktop.findElement` 只走 `DesktopLocator.model_validate`，而它的
> `require_identity` 只放行 `uia`/`win32`，`driver` 还被 `extra="forbid"` 判非法
> ⇒ 图像元素「捕得到、存得下、校验得过，**却点不动**」，报的还是 pydantic ValidationError。
>
> 本轮只修 ②（维护者选的切片）。①留给宿主：PPX 编辑窗口不在本仓，接口契约见第四节。

## 一、可验证目标

| 目标 | 可验证形态 |
|---|---|
| 图像元素可被 findElement 接受 | `desktop.findElement` + `driver=image` 回 `elementId`，不再抛 ValidationError |
| 唯一性仍然是硬门 | 多处匹配 → `ELEMENT_AMBIGUOUS`，且**不写入** `session.elements` |
| 旧模板仍拒 | V1（无 `templateSchema`）→ `INVALID_INPUT`，且**一次匹配都不跑** |
| 点击真落到矩形上 | 中心点 = 矩形中点；双击/右键/辅助键按下-抬起成对 |
| 辅助键必抬起 | 点击中途抛异常也执行抬起（`finally`） |
| 不静默降级 | 未显式 `driver=image` 的 locator **绝不调用**图像定位器 |
| 动作面诚实 | `getText`/`input`/`select` 对图像元素给干净 `INVALID_INPUT`，不读空串 |
| 两后端同源 | uia/win32 都走 `image_target` 同一份实现（各写一份必漂移） |
| 错误码不越界 | 图像层动态码收敛成封闭集，并比对四个 manifest 的 `errors` 声明 |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | 新增 `executors/image_target.py`：定位（复用 `ImageElementVerifier`）+ 落点 + 合成鼠标 + 证据 | 不实现任何匹配；`usedLayer=image` / `usedScale` / `matchedCount` 进证据 | done |
| S2 | uia 执行器接线：findElement 图像分派 + 元素动作分发 | `ValidationError` 消失；`path=image-mouse` 证据 | done |
| S3 | win32 执行器接线（**先于窗口解析**分派） | 句柄失效时不再先炸 `InvalidWindowHandle` | done |
| S4 | 契约测试 23 条 + 负向验证 5 注入 | 期望失败集**集合相等**；还原后全绿 | done |

## 三、验证结果

- `tests/contract/test_desktop_image_element.py`：**23 passed**
- 负向验证 `.harness/spike/probe_m82_negative.py`：**5/5 精确红**，逐条 md5 还原
  （拆元素动作分派 → 1 红；拆 findElement 分派 → 1 红；中心点改 `//3` → 3 红；
  辅助键不抬起 → 1 红；拆图像层守门 → 2 红）
- 全量：**954 tests / 0 failed / 0 errored / 22 skipped**（新增 23 条，无回归）
- 顺带修一处**既有计时假红**：`test_browser_input_params.py::test_post_delay_sleeps_only_when_declared`
  把 4 次 `asyncio.run` 的固定开销累加进 50ms 预算，复跑门禁时实测 78ms 变红（单跑 3 次全绿）。
  改成**逐次判据**（每次 < 50ms）：去掉累加噪声，同时比原来更严——任何一次真的等了 60ms 都会被抓。
  负向验证：把 `_post_delay` 改成恒等待 → 精确红并指名 `postDelayMs=None 不该等待`，md5 还原一致。

## 四、留给宿主（未做，不冒充）

- **捕获编排器**：`CaptureController` 没有 `image` 参数，`build_hybrid_session(image=...)`
  的 `image` 全仓无一处传 `True`（参数是死的）。要接就三行：构造参数 → 工厂 → 浮窗开关。
- **宿主 UI**：PPX 编辑窗口不在本仓；本分支 `src/rpa_core/gui/` 只剩 `i18n.*`，Qt 面板在
  `main`。宿主只需在建会话时传 `image=True`，其余链路已通。
- **图像元素的其他动作**：`desktop.input`（先点后打字）没有做。图像元素现在只支持 click，
  其余命令给显式 `INVALID_INPUT`——不做「看起来支持、其实没打字」的假动作。
- 真 125%/150% 系统 DPI 环境仍未复验（M80 的边界不变）。
