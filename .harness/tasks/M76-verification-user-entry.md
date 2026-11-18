# M76：统一校验的用户入口（CLI + PPX 原型）

Status: `done`

> **2026-11-17 立项**。承接上一轮差距复核：M74 的 `verify_element()` 只有契约测试调用，
> `src/` 与 `apps/` 没有生产调用方——API 已落地但用户点不到。本任务把已有能力接成
> 可见入口，不新增校验逻辑，不改动 M74/M75 的门面与后端。
>
> 前置：M74（统一门面）、M75（显式图像分支）。约束：**ADR 0016**（GUI 主力）、
> **ADR 0019**（Qt 源码冻结；PPX 原型只在 `.harness/demo/ppx_probe/`，能力层禁反向依赖
> `ppx_py`）、ADR 0022-R3（能力层共享）。`planned`，不占 active 位（M52 仍主线）。

## 一、可验证目标

| 目标 | 可验证形态 |
|---|---|
| CLI 能活体校验元素 | `rpa-core elements verify --live [--mode flash|preview|clear] [--shot]` 调 `verify_element`，输出统一 `VerificationResult.to_dict()`；不加 `--live` 时静态校验行为逐字不变 |
| PPX 原型能点按钮校验 | 资产行有「校验」按钮 → `rpa.verifyElement` → 同一个门面；结果在行内可读（命中数 / 未命中 / 错误） |
| 不造第二权威 | CLI 与 PPX 都只调 `capture.verification.verify_element`；禁止复制 kind 分派或浏览器目标选择 |

## 二、切片

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | CLI：`elements verify` 加 `--live` / `--mode` / `--shot`；静态分支原样保留 | 契约：静态 `valid/errors` 不变；活体走真门面（注入替身 verifier 证明分派与 mode）；未知 kind/错误结构化输出 | done |
| S2 | PPX：`rpa.verifyElement(path, name, mode, want_shot)` 薄 RPC + 资产行「校验」按钮 | 契约：RPC 读盘后调真门面；JS 静态判据钉按钮 → `rpa.verifyElement`；文案走 `ELEM_TEXT` 唯一表 | done |
| S3 | 收口：文档 + 台账 + 全门禁 | 末行 `FULL GATE PASSED` + 退出码 0；负向验证逐字节还原核 md5 | done |

## 三、判据总则

1. **默认不破坏既有路**：`elements verify` 不带 `--live` 的输出/退出码必须与现状一致，
   由既有 `test_cli_elements_list_show_verify` 钉住。
2. **覆盖用户实际走的路**：从 CLI 参数解析进到 `verify_element`，不是只测纯函数；
   PPX 侧从按钮事件进到 RPC 名，不是只 grep 函数定义。
3. **等值断言**：命中数、mode、分派到哪一端一律等值，不写 `>=`。
4. **负向验证**：注入删掉 `--live` 分支 / 改掉 RPC 名 / 改掉按钮绑定，判据必须精确红；
   `try/finally` 逐字节还原并核 md5。
5. **不改 GUI 冻结基线**：`src/rpa_core/gui/` 一行不动；只改 `cli.py` 与
   `.harness/demo/ppx_probe/app/{api/gui}`。

## 四、不在范围

- 桌面侧可见高亮/闪烁与截图回传（M78 候选）；执行层操作前自动前置（另一片欠账）。
- 捕获期图像 proposal / 微信自绘控件入库（下一步 M77）。
- 真机 DPI/遮挡/微信复验（需占机，另立）。
