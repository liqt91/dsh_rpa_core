# M47：会话保活 → 活体校验（S1：校验元素落地）

Status: `done`

Plan: docs/yingdao-gap-catchup.md §L3（C1/C2）

## 目标

让「捕获后的编辑界面」能对页面做**活体**操作——校验元素（现场查找 + 黄框闪烁 +
最新命中数）。这是 C2 的最小闭环，也是 C3 预览 / C4 批量验证的地基。

## 设计决策（2026-09-29）

- **C1 的目标用「按需短连接校验通道」实现，放弃「捕获会话保活」的原设想**：
  捕获完成时页面本就已撤防（`background.sendCapture → disarmCapture`），校验需要的
  不是 armed 态，而是「content script 可达 + scripting API」。按需通道
  （`capture/verify.py::ElementVerifier`）完全满足，且免去会话所有权、迟到结果、
  超时策略三组生命周期改造（M40 挂住教训）。任何失败都是结构化 error，
  失败模式是「报错」不是「挂住」。
- **校验只发活跃标签页**：校验对象是「刚捕获元素的那一页」，广播全部标签页既慢
  又可能命中别的页面上的同名结构给出误导计数。回传带 `url` 供核对。
- **与捕获会话完全解耦**：独立 sessionId（`ver-` 前缀）、独立 requestId 配对，
  不读不写 `captureSessionId`。

## S1 清单

- [x] `extension/content.js`：`[verify-helpers]` 纯函数（querySelectorAll 求值 /
      无效选择器结构化报错 / 闪烁上限 20）+ 黄框闪烁（z-index 低于捕获红框，
      1600ms 定时清理）+ `rpa-capture-verify` 同步应答（带 contentBuild）
- [x] `extension/background.js`：`capture_verify` → `runVerify`（活跃页优先、
      无脚本补注入重试一次、`capture_verify_result` 按 requestId 回传）
- [x] `src/rpa_core/capture/verify.py`：`ElementVerifier`（多端点并发、requestId
      配对、硬超时、结构化 error：extension-offline / verify-timeout / bad-reply /
      透传扩展侧 error）
- [x] `ElementDialog`：`verify_css` 回调注入；「校验元素」按钮（工作线程 +
      Qt 信号回主线程；校验中禁用；命中数替换捕获时旧值；错误就地展示）；
      desktop 元素 / 未注入回调 = 无按钮
- [x] `app.py::_confirm_element_save`：browser 元素注入 `ElementVerifier().verify`
- [x] 判据：`test_verify_element.py` 6 条（配对/陈旧忽略/离线/超时不挂死/错误
      透传/bad-reply）+ `test_gui_panels.py` 3 条（命中回显/错误内联/无按钮）+
      `check_verify.mjs` 17 项（纯函数求值 + runVerify 矩阵 + 接线断言）

## 验证

- 负向验证 4 注入全命中：requestId 配对失效→stale 判据红；count 校验放宽→
  bad-reply 判据红；background 广播化→W1 红；content 应答去 contentBuild→C1 红。
  均逐字节还原（无 INJECTED 残留）。
- 150 passed（含既有回归）；ruff 全绿；13 个 .mjs 全过（新增 1 个）；
  架构/tasks/param/error/matrix 五组静态门禁全过。

## 残留（后续切片）

- S2 预览（C3）：编辑中即时高亮——同一通道加 `mode:"preview"`（黄框驻留 +
  撤离清除）。
- S3 元素库活体批量验证（C4）：同一通道批量化 + 飘红标失效。
- S4 桌面腿活体查找：另一条通道（UIA 现场查找），未实现前桌面元素不摆校验按钮。
