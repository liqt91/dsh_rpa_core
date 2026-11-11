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
scripts/verify_live.py →  真机 E2E 验证   显式 --run 才占机，不进默认门禁
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

# 真机 E2E：先做不占机的静态预检，再显式 --run 占机跑一次（默认 Chrome）
./.venv/Scripts/python.exe apps/xhs-workbench/scripts/verify_live.py --check
./.venv/Scripts/python.exe apps/xhs-workbench/scripts/verify_live.py --run \
  --keyword "AI效率工具"

# 手工单跑同一条流程（等价的裸 CLI 形式）
rpa-core run apps/xhs-workbench/workflows/xhs_product_search.json \
  --inputs '{"keyword":"AI效率工具"}' \
  --artifacts run_artifacts/xhs
```

`browserType` 是流程输入，默认值 `chrome`（脚本也默认 Chrome）。**Chrome 的
Default profile 里装的是 `nhbndpphgnbjbofjpgijhdjlnpapaeem`**（location=4、
`disable_reasons` 为空，扩展目录在 uv tools 安装目录下），host 白名单本来就有
这个 ID。Edge 里那条 unpacked 记录带 `disable_reasons=[16777216]`，不要用 Edge 跑。

`env-status` 传空 extension_id 时只按 unpacked **目录路径**匹配，遇到装在别处的
已启用扩展会误报 `installed=false`；预检因此改成按 host 白名单 ID 扫
Secure Preferences。它此刻报 `online: false` 通常是 host 空闲 4 秒自动退出、
扩展 service worker 休眠，不代表通道坏——通道可用与否只有 `--run` 能证明。

真机这一步**不进默认门禁**，跑之前先确认是否占机。`verify_live.py` 不带
`--run` 时只做静态预检（不碰浏览器）；`--run` 才会在 Chrome 里开标签页、用当前
登录态搜索并关闭标签页。证据落在
`run_artifacts/xhs-e2e/e2e-verdict-<UTC>.json`。裸 CLI 形式的产物在
`run_artifacts/xhs/<run_id>/result.json`，采集结果取
`outputs.grabItems.outputs.result`（`browser.executeScript` 返回的 JSON 字符串），
同时由 `data.writeText` 落一份 `xhs-products.json`。

## 真机 E2E 判据

`scripts/verify_live.py` 的五条硬判据收敛在唯一函数 `evaluate_run()` 里，
任一条不过都非零退出，绝不把「离线 / 没跳搜索页 / 空结果 / 没落盘」折叠成成功：

| 判据 | 挡住什么 |
|---|---|
| `run_succeeded` | CLI 非零退出、`failed` / `cancelled` / `paused` / 未知终态 |
| `items_parsed` | `executeScript` 返回空数组、非数组、不是 JSON |
| `items_have_content` | 选择器没命中，只抓到一堆空字段的空壳 |
| `write_matches` | `data.writeText` 没落盘，或落盘内容与提取结果不一致 |
| `search_effective` | 回车/跳转没生效，脚本实际抓的是 `/explore` 首页 feed 而非搜索结果页（用 `pageUrl` 必须落在 `search_result?keyword=…` 判定） |

第 5 条是这次真机踩出来的假绿灯：最初用合成 `pressEnter`
（只派发键盘事件，小红书不认），URL 一直是 `/explore`，前四条仍全绿。
现在改成「新开标签显式 `goto` 搜索结果页」，并用 `search_effective` 把「真的到了
搜索页」钉死。

`tests/test_live_verdict.py` 在不占机的前提下对每条判据做负向验证，并带一条
接线哨兵（证明 `run_live` 真的调用了 `evaluate_run`，而不是测了个没人用的纯函数）。
静态预检的负向对照：`--check --browser chrome` 应当绿（profile 里插件未禁用），
`--check --browser msedge` 必须红（Edge 那条记录带 `disable_reasons`）。

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

- **真机 E2E 已跑通（2026-11-11）**：维护者手动打开 Chrome 后，`--run --browser chrome`
  连续两次 PASSED，可重复。证据 `run_artifacts/xhs-e2e/e2e-verdict-*.json`：
  跳到 `search_result?keyword=AI效率工具`、抓到 22 条、落盘 5293 字节且逐字一致、
  五条判据全绿。中途修了三个真机才暴露的坑：① host 白名单缺本仓扩展 ID；
  ② `executeScript` 的 `script` 写成了 `(() => {...})()` 表达式（扩展用
  `new Function` 当函数体执行，没有 `return` 恒为 `null`）→ 改成带 `return` 的语句体；
  ③ 合成 `pressEnter` 小红书不认，始终停在 `/explore` → 改成新标签显式 `goto`
  搜索结果页，并加 `search_effective` 判据防假绿灯。
  仍受 Chrome 手动开关约束（见上「跑起来」段末）。
- **跨 run 会话复用**：P0 workflow 末尾仍 `browser.close`。`browser.attach` 的
  `sessionId` 是 run 内逻辑 id，能否跨 run 复用没实测；一天跑几十次时应当改成
  常驻标签页 + attach，否则又慢又像机器人。
- **快照库 / 前后比对告警 / 监控池状态机 / 本地 HTTP 与 UI**：截图里那套
  「739 件商品、告警 12、全暂停/全恢复、8 个 tab」都还没落。
- **门禁接线**：`apps/xhs-workbench/tests` 目前独立运行，`check_all.py` 还没带上它。
  接线时要一并加哨兵，证明测试真被跑到。
