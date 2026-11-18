# M80：模板 V2（provenance）+ DPI 自适应重采样

Status: `done`

> **2026-11-17 立项**。M75/M77 让自绘控件能进元素库，但图像模板只存 base64 与尺寸：
> 不知道它是在什么 DPI/缩放下截的，匹配时也只会按原尺寸跑一次。结果是一台 100% 缩放
> 的机器上捕获的微信图标，到 125% 缩放的机器上直接失配（像素尺寸对不上）。
>
> 维护者拍板的两个口径：**DPI provenance + 多尺度**（不是盲扫）、**旧模板不迁移，
> 显式报错要求重捕获**（开发期硬切，不做数据迁移）。
>
> 约束：不建第二套匹配实现（复用 `ocr_worker.slide_match`）；不静默降级（ADR 0003）；
> `minConfidence` 仍然是 `TM_CCOEFF_NORMED` 相关系数门槛，不当作概率。

## 一、可验证目标

| 目标 | 可验证形态 |
|---|---|
| 捕获带 DPI 事实 | `grab_window` 回 `dpi`/`scale`；拿不到就明确缺字段，不猜 96 |
| 模板 V2 | locator 含 `templateSchema=2` / `templateHash` / `captureDpi` / `captureScale` / `sourceRect` |
| 缺 provenance 不出模板 | 截图没有 DPI 信息时 `make_image_proposal` 返回 `None`，不产半成品 |
| 跨 DPI 命中 | 1.25 倍模板在 96 DPI 屏上按 0.8 重采样命中，矩形换算回当前屏幕坐标 |
| 尺度可解释 | 命中回 `usedScale`，能看出这次用的是哪个尺度 |
| 旧模板显式拒绝 | V1 模板进 `verify_element` → `template-schema-requires-recapture`，不静默兼容 |
| 判据不假绿 | 五条注入逐条精确 `1 failed / 0 error`，md5 逐字节还原 |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | `capture/dpi.py` 统一窗口/显示器 DPI 与 effective scale；`grab_window` 回 `dpi`/`scale` | 截图元数据带 provenance；探针注入缺字段路径可测 | done |
| S2 | `image_proposal` 写 V2 字段；缺 provenance 返回 `None` | `templateSchema=2` + `sourceRect` + `matchedCount=0` | done |
| S3 | `image_locator` 单一重采样/多尺度函数 + `used_scale`；V1 显式报错 | 跨 DPI 命中且矩形是**当前**屏幕坐标 | done |
| S4 | 图像腿 `usedScale` 接线 + `--shot` 回执 | 命中即回 `usedScale`；`--shot` 给同源命中框；门面 `to_dict` 透传 | done |

## 三、关键实现

- **单一重采样权威**：`image_locator.locate_by_template(..., capture_scale, current_scale)`
  按 `current/capture` 比值生成候选尺度，命中后把矩形换算成当前屏幕像素；`usedScale`
  记录实际命中的尺度。没有第二处缩放逻辑。
- **old template 的两道闸**：`image_guard.image_locator_errors()`（静态 `selector_errors`）
  与 `image_verification._run`（活体用户路径）都拦 V1，因为「实现覆盖」不等于
  「用户实际走的路覆盖」。
- **`usedScale` 无条件回**：它是「重采样生效没有」的核心证据，不绑在 `--shot` 上；
  图像分支比语义分支多这一个字段，因为语义定位没有缩放概念。
- **`--shot` 复用匹配那张图**：不重新截图，否则 box 与命中矩形来自不同帧，元素一动就对不上。

## 四、负向验证

`probe_m80_negative.py`（五条，全部精确红并 md5 还原）：

```text
PASS|grab_window 不再回传 DPI provenance|failed=1 errors=0
PASS|缺 DPI 时 proposal 猜 96/1.0|failed=1 errors=0
PASS|跨 DPI 只跑原尺寸（多尺度被摘掉）|failed=1 errors=0
PASS|verify_element 不再拦 V1 模板|failed=1 errors=0
PASS|静态校验不再要求 templateSchema|failed=1 errors=0
M80 FIX NEGATIVE: 5 pass / 0 fail
```

`probe_m81_negative.py`（五条，覆盖 S4 的 `usedScale`/`--shot` 接线）：

```text
PASS|usedScale 不再无条件回|failed=1 errors=0
PASS|--shot 不再产出截图回执|failed=1 errors=0
PASS|门面 to_dict 吞掉 usedScale|failed=1 errors=0
PASS|缺窗口原点也硬造 shot|failed=1 errors=0
PASS|命中框改用加法（原点符号反了）|failed=1 errors=0
M81 NEGATIVE: 5 pass / 0 fail
```

## 五、门禁

`TMPDIR/TEMP/TMP=D:\Temp`（本机系统 Temp 不可写），连续两次
`909 passed, 20 skipped, 2 xfailed` + `FULL GATE PASSED`。

## 六、不在范围 / 欠账

- 真的切系统缩放到 125%/150% 复验：本机只跑 100%，跨 DPI 用的是「真机像素 + 模拟
  `captureScale`」；要让真实 125% 环境再验一次（需要改系统设置并注销，未做）。
- 多显示器不同 DPI（per-monitor）混合场景。
- 模板版本迁移（明确不做，V1 一律要求重捕获）。
