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
| GET | `/api/runs`、`/api/runs/<run_id>` | 运行记录列表 / 单次步骤时间线（耗时、输出、错误） |
| GET | `/api/runs/<run_id>/shot` | 该次采集的页面截图（PNG） |
| GET | `/editor-ppx/`、`/editor-ppx/bridge.js`、`/editor-ppx/assets/*` | PPX 编辑器页面 / 桥替身 / 构建产物 |
| POST | `/api/collect` | 触发采集（后台线程 + 单 run 锁，并发返回 409） |
| POST | `/api/pause`、`/api/resume`、`/api/pause-all`、`/api/resume-all` | 监控池暂停/恢复 |
| POST | `/editor-ppx-rpc` | 编辑器 RPC（`{method, params, requestId}` → `{ok,data,error,requestId}`） |

**编辑器页复用 PPX 原型**（ADR 0022-R1）：`/editor-ppx/` 托管 `.harness/demo/ppx_probe`
的 `gui/dist` 构建产物，注入一个 `window.pywebview.api.call` 的 fetch 替身，后端由
`src/ppx_bridge.py` 装载原型的 `app/api/api.py`（补一个 `ppx_py` 语义替身，不装原型的
venv）。所以左侧指令栏 / 中间画布 / 右侧结构属性 / 下方日志、if/forEach/try 的结构编辑、
元素资产面板、运行-停止-编译校验，都是原型那一份，能力层升级自动对齐。

工作台在这层只加了三件事（都在 `ppx_bridge.py`，不动原型一行）：

1. `RPA_WORKSPACE` 指到 `apps/xhs-workbench`：扫到的是本目录 `workflows/`，元素资产目录
   也随之对齐；`rpa.bootstrap` 追加扁平布局的 `workflows/*.json` 并排最前。
2. `xhs.createFlow`：新建流程骨架（原型没有「新建」），落到 `workflows/<名>.json`。
3. 终态落库钩子：编辑器跑完的 run 也要进快照 / 告警 / 监控池（否则「在编辑器里跑完，
   商品库没数据」）。关键词取流程文档的 `inputs.keyword`，与批量采集同一套口径。

run 目录是**并集**：控制台采集写 `<data>/run_artifacts/`，编辑器写
`<data>/_sandbox/runs/run-N/artifacts/`（原型布局），「流程」页两边都列。

「流程」页是只读诊断视图（看历史 run 的步骤耗时、输出与现场截图）。

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
（快照库 / 前后比对告警 / 监控池状态机）、本地 HTTP 服务与界面、**复用 PPX 原型的
可视化编辑器**（ADR 0022-R1）、应用层单测接进仓库全门禁
（`check_xhs_workbench_tests.py`，带接线哨兵与收集下限）。

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
- **PPX 编辑器是「运行期消费原型」**：原型在 `.harness/demo/ppx_probe`（归 `ppx-only`，
  ADR 0019 冻结判据仍生效）。原型大改时桥层要跟着修——这是 ADR 0022-R1 明确接受的代价。
  浏览器里 `studio.pollSession`（Agent 推流程进窗口）返回「离线」，那个通道只有桌面宿主有。
- **真机采集仍要手动开浏览器**：Agent 会话内起 Chrome 会被 Crashpad 拒访。
