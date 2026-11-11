# 小红书专用工作台（P0）

`xhs-workbench` 分支独占目录。从零实现，参考对象是网上看到的一张工作台截图，
没有源码可搬；采集执行**不自己写浏览器逻辑**，只调本仓能力层。

## 边界

工作台是仓内旁路应用层，只通过 `rpa-core run <workflow.json> --inputs ...` 调用
运行时，不 import `src/rpa_core` 内部模块。因此 README 的 `scope.out`
（排除调度器 / 数据库 / FastAPI / React UI）描述的是运行时核心，本目录不违例。
完整取舍见 `.harness/adr/0022-xhs-workbench-scope.md`。

```
config/schedule.json  →  src/schedule.py   频率配置 + 校验 + 风控估算
                     →  src/scheduler.py  错峰排程（纯函数，单 worker 语义）
                     →  src/collector.py  拼 CLI 命令 + 解析 result.json
workflows/            →  rpa-core run     扩展单通道 / 真实登录态浏览器
```

## 频率怎么改

编辑 `config/schedule.json`：`mode` 支持 `manual` / `daily` / `interval` / `cron`，
`startAt` / `windowHours` / `intervalMinutes` / `batchSize` /
`perItemIntervalSeconds` 全部可改。默认值取自参考工作台（8:00 起、12 小时窗口、
20 条一批、间隔 3~6 秒），只是预设。

**`maxConcurrentRuns` 恒为 1，不可改。** ADR 0015 写明扩展侧串行化
`connectNative`，并发 run 会拉起多个 native host 进程。改成 2 或删掉这个键，
`src/schedule.py` 会直接判配置非法，不静默改回 1。`estimate()` 按当前配置算出
每日请求数与峰值 QPS，UI 应当在用户把间隔调到 0 之前把这个数显示出来。

同一商品在窗口内的时刻由 `sha256(item_id) % window_seconds` 决定，每天一致，
跨天快照才对得齐；jitter 只加在批内间隔上。

## 跑起来

```bash
# 应用层单测（P0 未接进仓库全门禁，见下方欠账）
./.venv/Scripts/python.exe -m pytest apps/xhs-workbench/tests -q

# 静态编译校验：证明 workflow 能被 catalog + compiler 接受
rpa-core validate apps/xhs-workbench/workflows/xhs_product_search.json

# 真机采集：需要 Chrome 已登录小红书、扩展已安装并注册 native host
rpa-core run apps/xhs-workbench/workflows/xhs_product_search.json \
  --inputs '{"keyword":"AI效率工具"}' --artifacts run_artifacts/xhs
```

真机这一步**不进默认门禁**，跑之前先确认是否占机。产物在
`run_artifacts/xhs/<run_id>/result.json`，采集结果取
`outputs.grabItems.outputs.result`（`browser.executeScript` 返回的 JSON 字符串），
同时由 `data.writeText` 落一份 `xhs-products.json`。

## 状态语义

`collector.parse_result()` 把六种终态翻译成四类，不折叠：

| `result.json.status` | 归类 | 工作台该显示在哪 |
|---|---|---|
| `succeeded` | `ok` | 商品 / 运行 |
| `failed` / `cancelled` | `failed` | 采集失败日志 |
| `indeterminate` / `recovery_required` | `needs_attention` | 告警 |
| `paused` | `paused` | 运行 |
| 其它 | `unknown` | 告警（绝不静默当成功） |

## P0 验收与欠账

P0 交付：配置化频率、稳定错峰排程、CLI 接线与终态解析、第一条采集 workflow
（搜索框元素引用 → `executeScript` 结构化提取 → 落盘）、上述三块各自的单测。

还没做，按计划归 P1/P2：

- **真机 E2E**：登录态、`executeScript` 在小红书页面上的实际返回值、选择器是否命中，
  全部没验证过。上面那条 `run` 是待验证路径，不是已跑通路径。
- **跨 run 会话复用**：P0 workflow 末尾仍 `browser.close`。`browser.attach` 的
  `sessionId` 是 run 内逻辑 id，能否跨 run 复用没实测；一天跑几十次时应当改成
  常驻标签页 + attach，否则又慢又像机器人。
- **快照库 / 前后比对告警 / 监控池状态机 / 本地 HTTP 与 UI**：截图里那套
  「739 件商品、告警 12、全暂停/全恢复、8 个 tab」都还没落。
- **门禁接线**：`apps/xhs-workbench/tests` 目前独立运行，`check_all.py` 还没带上它。
  接线时要一并加哨兵，证明测试真被跑到。
