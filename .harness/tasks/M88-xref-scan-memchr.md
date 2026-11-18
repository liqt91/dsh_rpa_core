# M88：xref 扫描要快，但不能扫错——「点捕获假死十几秒」的真因

Status: `done`

> **2026-11-18 真机报障**：用户「用捕获元素，刚开始无法框选元素，有点假死的感觉，
> 十几秒后可以框选元素，包括微信元素，然后就一直可以了」。
>
> 取证（`%TEMP%\rpa-capture-trace.log`，09:48 那轮）：每次新 agent 启动后，前两帧
> `hover_hit_slow` 是 100~400ms，随后**一帧 10.5~14.9 秒**，卡完立刻出现
> `accessibility_wake reason=already_active`。四个独立进程（19300 / 29016 / 30528 /
> 4088）都是同一形状 ⇒ 不是稳态慢，是**每个新进程的第一次唤醒**。
>
> 分项计时（本机 Weixin.dll 196.4 MB）：

| 阶段 | 耗时 |
|---|---|
| `read_bytes` | 0.113s |
| `pe_sections` | 0.000s |
| `data.find(CORE_STRING)` | 0.051s |
| **`rip_xrefs_to`** | **14.584s** |
| `GATE_PATTERN.finditer` | 0.245s |

## 一、根因

`rip_xrefs_to` 用 `for i in range(len(raw) - 7)` **逐字节**遍历可执行段，每个字节做
三层判断。196 MB 镜像 ≈ 1.5 亿次 Python 循环。而 `gate_for_dll` 的缓存是**进程内**
字典——每个新捕获进程都要重付一次，所以「每次点捕获都假死」。

## 二、改法

| 片 | 内容 | 验收口径 | 状态 |
|---|---|---|---|
| S1 | `rip_xrefs_to` 改用 `bytes.find(b"\x8d")`（C 层 memchr）定位 `lea`，只在命中处解析 disp；REX / 非 REX 两种形式保持原语义与起始 RVA | 同一 dll 的 refs 列表与旧实现**逐项相等** | done |
| S2 | 图像模式补 `image_box` 埋点（每秒至多一条：root 句柄 + 类名 + box + 光标） | 报障时能区分「框的是谁」与「为什么没画」 | done |
| S3 | 新增 `test_rip_xrefs_report_instruction_start`：直接钉住返回的指令起始位置 | 见下「判据缺口」 | done |

结果：`rip_xrefs_to` 14.584s → **1.166s**，`scan_gate_rva` 全流程 **1.447s**，
`rva=0xb50abc8 / candidate=1 / core_xref=5` 与改前完全一致。

## 三、判据缺口（负向验证发现的）

第一次负向验证：注入 `has_rex = False`（等价于把 REX 指令按 6 字节解析）**测试全绿**。

原因：REX 与非 REX 两条分支算出的 disp 偏移**数学等价**（`pos+2 == rex+3`、
`pos+6 == rex+7`），唯一差别是返回的指令起始位置差 1 字节；而 `core_xrefs` 只用于
距离过滤，差 1 不影响 gate 定位。**等于「分支写反了也发现不了」**。

⇒ 补 S3 用例后，该注入精确红。教训与 M87 同：**判据要钉住中间产物本身，不能只钉
最终结论**——最终结论对 1 字节位移不敏感。

## 四、验证

- 新旧实现对真实 dll 的 `refs` 列表逐项比对：`IDENTICAL`（5 条，位置完全相同）。
- `test_accessibility_gate.py` 21 条绿（新增 1 条）。
- 负向验证 `probe_m88_negative.py`：2 个注入，失败集逐项相等、无 `error`、
  还原后 md5 一致、复跑 rc=0 ⇒ ALL PASS。
- 全量门禁：`FULL GATE PASSED`，rc=0。

## 五、未做 / 边界

- **未加磁盘缓存**：1.17s 已落在「可感知但不至于假死」区间；跨进程缓存要引入
  「缓存脏了会写错别人进程内存」的新风险，收益不抵。
- **图像模式为什么框不出来仍未定论**：本次真机复现时 `_win32_root_at` 在所有测试点
  都返回 **WorkBuddy 自己的窗口**（`Chrome_WidgetWin_1`，`(305,-1070)-(1839,-153)`，
  正好盖住副屏的微信 `126,-988`-`1166,-116`）。浮层本身已验证在主/副屏都正常渲染
  （各 468 个红像素）。需要下一次 `image_box` trace 才能判定是「框到了宿主自己」
  还是「压根没进绘制分支」。
