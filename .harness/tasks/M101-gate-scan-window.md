# M101 — 校验首次 2 秒：gate 扫描按候选开窗

Status: `done`

## 背景

M100 上线后用户真机复验：「昨天捕获的微信搜索框，这次可以校验到了！就是首次有点慢，
再次的话稍快，约 2 秒」。前半句证实了 M100 的核心假设（唤醒抢在进程首次 UIA 查询之前），
后半句是新的性能反馈。

先量，不猜。`.harness/spike/probe_m101_verify_latency.py` 在真机上分项计时：

| 项 | 耗时 |
|---|---|
| 读 `Weixin.dll`（196.4 MB） | 95 ms |
| `rip_xrefs_to` 全段扫 | **892–1320 ms** |
| `GATE_PATTERN` 全文件 | 175 ms |
| `find(CORE_STRING)` | 38 ms |
| `scan_gate_rva` 整体 | 1172 ms |
| **端到端 verify（冷）** | **2071 ms** |
| 端到端 verify（热，进程内缓存命中） | 46–105 ms |

结论：2 秒里约 1.4 秒是 `rip_xrefs_to` —— 它用单字节 `\x8d` 做 memchr，在 120 MB
可执行段里命中 **314 万次**，每一次都跑一趟 Python 循环解析 ModRM。与「读文件慢」
「正则慢」都无关；也**不是**「只扫可执行段」能救的（正则 196 MB 与 120 MB 同为 180 ms）。

顺带排除一个误报：探针一度 `count=0`，看着像「校验又坏了」。实际是当时微信开着
「图片和视频」子窗口，元素指向主窗口的搜索框——不是校验链路的问题。

## 改动

`capture/accessibility.py`：

1. `rip_xrefs_to(..., rva_windows=)`：可把「找引用点」限制在若干 RVA 区间内。区间按
   RVA 给（不是文件偏移），与各可执行段求交；`raw` 起点可能不在段首，所以基准 RVA
   随 `raw_start` 平移（`base_rva = sec.rva + (raw_start - seg_start)`）。窗口版结果
   去重排序——重叠窗口会重复命中同一条指令。
2. `scan_gate_rva()` 改**两遍**：
   - 第一遍只做结构粗筛（指令在可执行段 + disp 指向可写段），此时不需要 core 引用点；
   - 第二遍只在**候选 ± `_MAX_CORE_DISTANCE`** 的窗口里找 core 引用点。
3. 删掉 `_XREF_WINDOW_MARGIN`：负向验证证明它对「距离恰等于阈值」的项不起作用
   （REX 落在窗口起点内），窗口边界只可能切掉「距离 > 阈值」的项，而那些本来就要被
   距离过滤丢掉。留一个证明不出作用的常量是负担。

## 一个必须记住的坑

第一版把窗口中心写成 `core_rva`（`qt.accessibility.core` 字符串的位置）。**错**：
字符串在 `.rdata`（RVA 0x914ce08），引用点在 `.text`（RVA ~0x87a000），两者 RVA
差几百 MB，窗口与整个 `.text` 无交集 ⇒ 扫出 0 个引用点 ⇒ gate 定位直接失效
（真机探针立刻红：`rva=None`，期望 189836232）。

窗口中心必须是 **gate 候选指令**的 RVA。`distance` 比较的也是「候选指令 ↔ core
引用点」，两者同在 `.text`。

## 判据

`tests/contract/test_accessibility_gate.py` 新增 5 条（共 32）：

| 用例 | 守什么 |
|---|---|
| `test_rip_xrefs_window_excludes_refs_outside` | 窗口外的引用点不许进结果（否则等于没限窗） |
| `test_rip_xrefs_overlapping_windows_dedupe` | 重叠窗口命中的同一指令只记一次 |
| `test_rip_xrefs_window_outside_sections_is_empty` | 窗口整段落在可执行段外时返回空，不去扫相邻段 |
| `test_scan_finds_gate_at_exact_distance_limit` | 距离**恰等于**阈值时必须命中（判定是「大于才丢」） |
| `test_scan_drops_gate_beyond_distance_limit` | 超阈值一个字节就必须丢掉 |

新增测试辅助 `_build_big_pe()`：段大小按内容取整（0x30000 的 `.text`），因为距离
阈值 0x20000 需要段内放得下两个相距 128 KB 的指令，固定 0x400 的老构造器装不下。

## 负向验证

`.harness/spike/probe_m101_negative.py`，4 个注入、失败集逐项相等、md5 还原、复跑绿：

| 注入 | 失败集 |
|---|---|
| `window-ignored`（`if True:` 强制全段） | 2 条（窗口外 + 段外窗口） |
| `no-dedupe`（`return out`） | 1 条（重叠去重） |
| `boundary-drop`（`>=` 代替 `>`） | 1 条（边界命中） |
| `window-too-narrow`（`radius - 1`） | 1 条（边界命中） |

第一版有两条**没红**，原因都不是实现而是判据/期望写错，已修：

1. `window-ignored` 不红：`test_rip_xrefs_window_excludes_refs_outside` 里
   `far_off=0x400` 正好是 `.text` 段右边界，那条 lea 实际落在 `.rdata`，全段扫也
   找不到 —— 假绿灯。同时两处 `bytearray(b"\x90" * 0x1000)` 会溢出 0x400 的段，
   一并改成 `SEC_SIZE`。
2. `no-window-margin` 不红：推断错了，margin 对距离 ≤ 阈值的项不起作用（见「改动」3）。
   换成 `window-too-narrow`（窗口比阈值窄 1 字节）才真正打中边界。

另有一处构造器 bug：gate 指令实际 **22 字节**（`48 85 c9` + `0f 84 rel32` +
`80 3d disp32 imm8` + `0f 84 rel32`），首版按 20 字节切片写入，把 `bytearray`
撑长 2 字节、回写时盖掉了 `.rdata` 开头 ⇒ `CORE_STRING` 偏移 +2。已在构造器里
按 `len(gate_bytes)` 切片并加 `assert len(text) == BIG_TEXT_SIZE`。

## 门禁

`check_all.py` → `FULL GATE PASSED` + `rc=0`（1051 passed / 20 skipped / 2 xfailed）。

## 效果（真机实测，同一台机器同一个 dll）

| 指标 | 改前 | 改后 |
|---|---|---|
| `scan_gate_rva` | 1172 ms | **283–305 ms** |
| 端到端 verify 冷 | 2071 ms | **1190 ms** |
| 端到端 verify 热 | 46–105 ms | 46–105 ms（未变，本来就靠进程内缓存） |
| gate rva | 189836232 | 189836232（不变，这是安全底线） |

## 边界（本单不承诺的）

- 剩下的 ~0.9 s 主要是**逐窗口 UIA 查询**（Z 序里未命中的窗口也要查一遍），以及
  微信第一次建树的对方进程开销。要继续压需要「按 `processName` 预筛窗口」这类
  **改变搜索策略**的改动（M74 的顺序契约），不在本单范围。
- 跨进程持久化扫描结果（把 1.4 s 变 0 ms）没做：需要往磁盘写 gate RVA，一旦缓存
  与 dll 版本错配就是往别人进程写错字节，风险与收益不成比例。
- `GateScan.core_xref_count` 语义从「全段引用点数」变成「窗口内引用点数」。真机仍
  是 5（5 个引用点本就聚在候选 ±2 KB 内）；契约测试断言的 1 不受影响。
