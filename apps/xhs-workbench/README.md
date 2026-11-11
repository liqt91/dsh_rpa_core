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

## 本地服务与界面（P1）

```bash
cd apps/xhs-workbench
../../.venv/Scripts/python.exe -m src.run_server   # 127.0.0.1:8790，数据默认落 apps/xhs-workbench/data
```

接口（全部只监听 `127.0.0.1`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 存活探测 |
| GET | `/api/snapshots`、`/api/snapshots/<关键词>` | 快照列表 / 单关键词历史 |
| GET | `/api/products`、`/api/alerts`、`/api/monitor` | 商品库、告警、监控池 |
| GET | `/api/workflow` | 流程文档摊平：节点 id / 命令 / 参数 / 表达式标记 |
| GET | `/api/commands` | 指令目录（manifest 摊平 + 中文标签），编辑器指令栏/属性表单的事实源 |
| GET | `/api/runs`、`/api/runs/<run_id>` | 运行记录列表 / 单次步骤时间线（耗时、输出、错误） |
| GET | `/api/runs/<run_id>/shot` | 该次采集的页面截图（PNG） |
| GET | `/api/editor/status`、`/api/editor/log?since=N` | 编辑器运行状态 / 增量执行日志 |
| POST | `/api/collect` | 触发采集（后台线程 + 单 run 锁，并发返回 409） |
| POST | `/api/pause`、`/api/resume`、`/api/pause-all`、`/api/resume-all` | 监控池暂停/恢复 |
| POST | `/api/validate` | 跑 `rpa-core validate` 校验当前流程 |
| POST | `/api/workflow/update`、`/insert`、`/delete`、`/move` | 编辑流程（改参数 / 插节点 / 删节点 / 上下移），原子写 + 引用护栏 |
| POST | `/api/editor/run`、`/pause`、`/resume`、`/stop` | 编辑器执行控制（暂停=节点边界收口成 paused；继续=resume 从检查点续跑；停止=杀进程树） |

界面「编辑器」页是完整的可视化编辑器：左侧指令栏（89 条指令按分组列出、可搜索、点击
插入到当前步骤之后）、中间画布（节点卡片，支持选中 / 上移 / 下移 / 删除，运行中当前
步骤高亮）、右侧属性表单（按 manifest 的 schema 生成：必填标记、枚举下拉、多行文本、
fx 表达式标记；类型收拢在服务端做，非法值 400 不落盘）、下方执行日志（执行 / 暂停 /
继续 / 停止按钮 + 实时日志滚动）。每次编辑后自动跑 `rpa-core validate` 并亮校验结果。

「流程」页保留为只读诊断视图（看历史 run 的步骤耗时、输出与现场截图）。

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

## P0/P1 验收与欠账

已交付：配置化频率、稳定错峰排程、CLI 接线与终态解析、采集 workflow、数据层三块
（快照库 / 前后比对告警 / 监控池状态机）、本地 HTTP 服务与界面、应用层单测接进仓库
全门禁（`check_xhs_workbench_tests.py`，带接线哨兵与收集下限）。

两个真机踩到、已修并写成判据的坑：

- **`browser.waitLoad` 不等于「结果渲染好了」**：它只认 `tab.status === "complete"`，
  小红书的搜索结果页是 SPA——外壳加载完（约 1.1 s）时列表还没渲染，`grabItems`
  取到 `[]`，于是「采集完成但没有任何数据」。历史 run 里三份有两份是 0 条。
  修法是取数前显式 `browser.waitFor`（`state=attached`，选择器
  `section.note-item, a[href*="/goods/"]`），实测该步耗时 0.02 s ~ 1.9 s 波动，
  0 条的那几次正是卡在这段。判据在 `test_workflow_contract.py`。
- **`${...}` 只认整串引用**：写成 `"${inputs.workspace}/results.png"` 不会插值，
  会原样落成一个叫 `${inputs.workspace}` 的目录（真机踩到）。截图路径因此单独开
  `inputs.shotPath`，由服务端传入绝对路径。
- 采集脚本会命中不含链接的容器节点；缺 `href` 的记录现在被丢弃并计入
  `Snapshot.skipped`（UI 显示「跳过 N 条无链接」），不再让整轮采集报错。

还欠：

- **跨 run 会话复用**：workflow 末尾仍 `browser.close`。`browser.attach` 的
  `sessionId` 是 run 内逻辑 id，能否跨 run 复用没实测；一天跑几十次时应当改成
  常驻标签页 + attach，否则又慢又像机器人。
- **编辑器尚无容器节点**：「编辑器」页只编辑 action 步骤，if/loop/try 这类容器
  节点暂不支持（仓库里的命令清单有，画布还没做结构编辑）。
- **真机采集仍要手动开浏览器**：Agent 会话内起 Chrome 会被 Crashpad 拒访。
