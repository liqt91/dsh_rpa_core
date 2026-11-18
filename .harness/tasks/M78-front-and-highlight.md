# M78：执行层前置 + 桌面校验可见反馈

Status: `done`

> **2026-11-17 立项**。M74 已有「flash 时置前命中窗口」，但桌面校验仍只回 `rect`，
> 用户看不到框；执行层 click/input 也只做元素 `set_focus()`，不等于顶层窗口前置。
> 本任务补齐这两块观感/时序工程，不改定位权威。
>
> 约束：ADR 0016/0019；观感层失败绝不能把执行/校验拖成失败；Qt 源码不动。

## 一、可验证目标

| 目标 | 可验证形态 |
|---|---|
| flash 看得见 | `flash` 模式在命中矩形外沿画一次红框；`preview`/`clear` 不画、不抢焦 |
| --shot 有回执 | `want_shot=True` 时桌面腿用 `shot_for_desktop` 回 `{dataUrl, box}`；截图失败不影响 `count` |
| 动作前窗口在前 | uia 与 win32 的 click/input/getText 执行前都调 `bring_to_foreground`；失败吞掉 |
| 不改契约 | 不带 `--shot` 的 `VerificationResult.to_dict()` 形状逐字不变 |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | `capture/desktop_highlight.py`：置顶/穿透/仅边框的瞬时红框 | 注入 highlighter 证明只 flash 画一次；真 win32 缺依赖时 no-op | done |
| S2 | `DesktopElementVerifier` 接 highlighter/shot_fn，`VerificationResult.to_dict()` 在 --shot 时带 shot | 命中数等值；截图/高亮异常被吞；preview/clear 不画 | done |
| S3 | 两个桌面执行器的元素动作前置窗口 | 静态接线 + 动态替身证明 helper 真调 bring_to_foreground | done |
| S4 | 收口：文档 + 台账 + 全门禁 | 负向 6/6 精确红、md5 还原、FULL GATE PASSED | done |

## 三、判据总则

1. 观感/截图是增强：任何一步失败都必须保持 `count`/执行结果不变。
2. 三 mode 成对：只有 `flash` 画框与置前；`preview`/`clear` 不抢焦、不留框。
3. 两个执行器都要接，不能只改 uia 留下 win32 暗路。
4. 真机 DPI/遮挡/多窗口的视觉效果不在本任务（见真机复验欠账）。

## 四、不在范围

- 桌面高亮跟随窗口移动、长时间驻留高亮（preview 场景真机再定）。
- 执行器所有命令一律前置：只接 click/input/getText 三类元素动作。
