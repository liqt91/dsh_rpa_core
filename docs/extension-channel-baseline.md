# 扩展通道往返数与耗时基线（M28 S4）

自研扩展是浏览器执行/捕获的**唯一通道**（ADR 0013/0015）。它的性能特征不是「HTTP 快不快」，
而是**一步命令要在这条通道上走几个来回**：每多一个来回就多一次进程间往返 + 一次浏览器 API
调用，在长流程里会被成倍放大。

本文件是这条通道的度量基线：口径 → 每命令信封数（机器校验）→ 耗时（本机实测）→ 怎么刷新 →
哪些变化是回归、哪些是设计。

## 1. 口径（改口径等于改基线，勿随手调）

度量实现见 `src/rpa_core/extension_exec.py` 的 `ChannelMetrics`，字段：

| 字段 | 含义 |
|---|---|
| `ops` | 一次命令信封 = 一次 `submit`。自愈候选重试**各算一次**（有意为之：「这次点击花了 3 个来回」正是要看得见的东西） |
| `statusProbes` | `status` 探测次数。每次也是真实往返，但**不属于命令本身**，单独计量 |
| `roundTrips` | `attempts + statusProbes`，即这一步在通道上走的总来回数 |
| `retries` | 同一条命令因**跨端点重发**多走的次数（正常单端点时为 0） |
| `channelMs` | 通道内耗时合计（连接 + 等待应答） |
| `stepMs` / `channelShare` | 这一步的总耗时，以及通道占其中多少 —— 用来判断「慢在浏览器还是慢在编排」 |
| `byOp` | op → 次数（如 `{"page.call": 2}`），一眼看出这步在跟浏览器要什么 |

两个刻意的设计选择：

- **计数点只有一处**：`ExtensionExecClient` 的传输层（`submit` / `_status_of`）。80+ 条命令
  不需要逐个埋点，也就不会有「新命令忘了埋」的漏网。自愈候选、跨端点重发、失败重试全在
  这一层天然被计入。
- **探测与命令分开记**：否则「每步探测一次」会被读成「命令变重了」。

落点：执行器在**命令边界**把度量并进 `CommandResult.diagnostics["extension"]`（成功与失败
都有，见 `PlaywrightExecutor.execute`），随 `scopes.steps.<node>.diagnostics` 进
`checkpoint.json`；因此每步的往返数**可复盘**（运行历史/排查时不再只有一句「这步慢」）。

`stepMs` 用 `time.perf_counter()` 而不是 `time.monotonic()`：Windows 上后者的粒度约 **15.6ms**，
比一步命令还粗，用它计时会把常见命令整片记成 `0ms`，度量直接失去意义。

## 2. 每命令信封数基线

`byOp`（不含 status 探测）。**本表由测试机器校验**：`tests/contract/test_browser_channel_metrics.py`
的 `_BASELINE_CASES` 逐条断言，新增命令必须一并登记——改文档没用，漏登记或悄悄多打一次往返都会红。

| 命令 | 信封 | 备注 |
|---|---|---|
| `browser.click` / `hover` / `input` / `scroll` / `select` / `check` | `page.call` × 1 | 主选择器失效时按元素资产候选 **+1/条**（自愈） |
| `browser.getText` / `queryAll` / `getPosition` / `getScrollPosition` / `getSelectOptions` | `page.call` × 1 | 读取类不带预检，允许读隐藏元素 |
| `browser.setValue` / `setAttribute` | `page.call` × 1 | |
| `browser.drag` | `page.call` × 1 | 源元素走自愈 + 预检；目标元素（`targetSelector`）在扩展侧直接取，**不额外往返** |
| `browser.waitFor` | `page.call` × N | **轮询式**：每 300ms 一个来回，直到命中或超时 → 往返数随等待时长线性增长（唯一「不固定」的读命令） |
| `browser.executeScript` | `page.eval` × 1 | |
| `browser.screenshot` | `screenshot` × 1 | 落盘在 Python 侧，不占通道 |
| `browser.navigate`（无会话＝打开网页） | `tabs.create` × 1 | 目标浏览器离线时会先拉起再轮询探测，**探测次数随等待时间增长** |
| `browser.navigate`（既有会话）/ `back` / `forward` / `reload` | `tabs.navigate` / `tabs.history` × 1 | |
| `browser.attach` / `listPages` | `tabs.list` × 1 | |
| `browser.waitLoad` / `stopLoading` | `tabs.waitLoad` / `tabs.stopLoading` × 1 | |
| `browser.cookieGetAll` / `cookieGet` / `cookieSet` / `cookieRemove` | `cookies.*` × 1 | |
| `browser.close` | **0** | 只是本地解绑，一个信封都不发（因此不写度量） |
| `browser.upload` / `download` / `handleDialog` | **0** | 未实现，直接返回 `COMMAND_NOT_FOUND`（见 `element-mvp-boundaries.md`） |

### 会话内命令的探测（status）摊销

除 `browser.close` 外，会话内命令发命令前都会先确认扩展还在轮询（否则会白等 `timeoutMs`，
默认 30s 才报超时）——这次探测也是一次真实往返：

```text
第一个命令：  status 探测(1) + 命令(1)  = roundTrips 2
2s 内下一步： 命令(1)（探测命中 TTL 缓存）= roundTrips 1
```

`status` 结果带 **2 秒 TTL 缓存**（`ExtensionExecClient._status_ttl`），所以密集步骤通常只在
第一步付探测成本。这也是「同一条命令在不同位置 roundTrips 不一样」的唯一合法原因。

## 3. 耗时基线（本机实测）

真实 `rpa_core.workers.ext_bridge` host 子进程 + 假扩展（无浏览器、无真实 DOM），
20 次 `page.call` 连续往返：

| 采样 | p50 | max | 20 次合计 |
|---|---|---|---|
| 第 1 次 | 6.03 ms | 28.70 ms | 146.5 ms |
| 第 2 次 | 4.76 ms | 18.94 ms | 107.5 ms |
| 第 3 次 | 5.21 ms | 20.94 ms | 117.4 ms |

环境：Windows 本机 + 托管 Python 3.13，`uv run pytest`，仅 host 子进程 + 命名管道，
**不含浏览器**。这组数字是**下限**（参考用）：真实一步还叠加浏览器 API 与页面 DOM 的耗时，
量级通常 10–100ms 起，取决于页面。

**每条命令一次连接**：`local_transport.connect()` → 发一帧 → 收一帧 → `close()`。没有常驻
连接/连接池——这是「端点存在即可用」设计的代价，也解释了为什么往返数（而不是连接复用率）
是本通道最该盯的指标。

## 4. 防回归（两条腿）

1. **确定性数量**：`tests/contract/test_browser_channel_metrics.py`（40 项）精确断言每命令
   `byOp` / `roundTrips` / `statusProbes` / `retries`，含自愈 +1、预检失败不试候选、
   跨端点重发只算 `retries`、未实现命令不假装有往返、`waitFor` 轮询计数、以及
   **端到端落库**（真 catalog + 真编排器 → checkpoint 里能读到 `diagnostics.extension`）。
2. **耗时上界**：`tests/contract/test_ext_bridge.py::test_channel_round_trip_baseline`
   跑真实 host 子进程，断言 p50 < 200ms、max < 2000ms 并打印实测值。上界刻意很宽——
   机器负载会让毫秒抖动，但「通道里被加了 sleep / 多了一次探测」是数量级变化，宽上界足够拦住。

### 刷新基线数字

```bash
# 耗时（会打印 [channel-baseline] 行）
uv run pytest tests/contract/test_ext_bridge.py -k baseline -s

# 信封数（改了命令路径后看哪些用例红）
uv run pytest tests/contract/test_browser_channel_metrics.py -q
```

### 合法变化 vs 回归

| 现象 | 判定 |
|---|---|
| `roundTrips` 从 2 变 1 / 1 变 2（同一条命令、位置不同） | 合法：status 探测的 TTL 摊销 |
| 主选择器失效时 `byOp.page.call` 变 2 | 合法：自愈候选重试，证据里同时有 `fallback.index` |
| `waitFor` / 离线拉起时 `page.call` / 探测数随时长增长 | 合法：轮询/等待语义 |
| 双浏览器并存时 `retries` > 0 | 合法：跨端点重发（多实例路由兜底） |
| **同一场景 `byOp` 变大** | **回归**：多打了一次往返 |
| **p50 明显抬升**（远超上表量级） | **回归**：通道里多了等待 |
| `stepMs` 明显抬升但 `channelMs` 不变 | 回归在编排层（后端/延时参数），不在通道 |

## 5. 已知边界

- 度量只覆盖**扩展通道**；桌面（UIA/Win32）与 Python worker 走各自独立路径，本期未纳入。
- 度量按「执行器实例」累计：同一 `PlaywrightExecutor` 上并发的两条命令会混在一份计数里。
  当前编排器按节点串行执行，不构成问题；若将来并行化，需要改成按步上下文（thread-local
  或显式传入）。

## 6. 宿主生命周期（M34）

host 随浏览器扩展常驻（Native Messaging 通道），其生死直接决定「能否重装 venv」。

### 6.1 现象：`uv run` 报 `os error 5 拒绝访问`

```
error: failed to remove file `...\.venv\Scripts\rpa-core-ext-host.exe`: 拒绝访问。 (os error 5)
```

`uv run` 重装本项目会覆写两个 console script（`rpa-core.exe` / `rpa-core-ext-host.exe`）。
只要浏览器还连着扩展，宿主进程就**必然锁着这两个文件**——这不是异常，是正常态。
遇到此报错：**先关掉连着扩展的浏览器，再 `uv run`**。

### 6.2 根因：Windows 垫片残留

Windows 上 console script 是「垫片（`.exe`）+ python.exe」两个进程。真宿主
（`python.exe`）随扩展断开而退出，但**垫片不一定回收**，会一直攥着 exe 句柄。
每起一次浏览器多一对残留，攒到 `uv run` 触发重装时必被锁。

### 6.3 修复：空闲自杀

宿主在「**无客户端连接** 且 **静置超过阈值**」时主动 `os._exit(0)`：

- 阈值默认 30 分钟（`RPA_CORE_HOST_IDLE_EXIT_SECONDS`，`0`/负=关闭），宁可多等不可误杀。
- 有客户端、或扩展仍在发消息（焦点变化等）→ 不退，避免打断正在进行的流程。
- 退出方式用 `os._exit(0)` 而非 `shutdown()`：实测从监视线程调 `shutdown()` 会在
  `self._server.close()` 的 `_PipeServer._lock` 处**死锁**（后台 `accept_loop` 攥着锁
  阻塞在 `WaitForSingleObject`），进程卡死等于没退。置位 `_closed` 信号 `accept_loop`
  自行退出 + `os._exit` 强杀，无此竞争；管道句柄由 OS 回收。

### 6.4 验证

`tests/contract/test_ext_bridge.py` 的 `TestIdleExitThreshold`（阈值解析，纯函数）+
 `test_idle_exit_*` 四条（真 spawn host 子进程，断言退出/存活）。

**不拿单测结论冒充真机结论**：空闲自杀的「残留垫片随之回收、文件锁释放」只在真浏览器
长静置后才会被 OS 实际回收，单测只验证宿主自身退出与日志。
