# ADR 0022：小红书专用工作台作为旁路应用层

状态：**定案（2026-11-11 维护者拍板）**

日期：2026-11-11
关联：ADR 0015（Native Messaging 单通道）、ADR 0020（旁路形态退役门）、
`docs/branch-ownership.md`、`.harness/branch-scope.json`

编号说明：ADR 0020 §2.1 与 §6 已把 **0021** 预留给「退役 Qt gui」，本决策顺延为 0022。

## 0. 维护者拍板

> 「没有源码，是网上看到的，从零开始做，采集频率可自定义，
> 基于咱们已有的 rpa 能力来做采集能力支撑。」

分支形态另有一票：**新起 `xhs-workbench` 分支**，不重命名 `ppx-only`。

## 1. 背景

参考对象是一张「小红书 RPA 工作台」截图：本地 `127.0.0.1:8790` 的 Web 台，
盯的是小红书虚拟资料 / 知识付费商品（739 件、最高销量 221,000），
每天 8:00 起把 120 件商品错峰铺进 12 小时窗口，一件一个固定时刻，
一批 20 条、间隔 3~6 秒；另有告警 12、采集失败日志、全暂停/全恢复、8 个 tab。

关键判断：那不是「RPA 编辑器」，而是**用 RPA 跑垂直业务的数据运营台**。
设计重心不在采得多，而在长期不被风控地采。本仓 `ppx-only` 上是通用可视化
流程编辑器，两者产品形态不同，所以不走「改造 ppx 客户端」的路。

## 2. 决策

### 2.1 新分支 + 独占域

从 `ppx-only` 的 `cd74295` 起 `xhs-workbench`，独占 `apps/xhs-workbench/`。
`split_point` 仍是 `ff36ed21`，三条分支共同基线不变；契约与闸门从两分支
泛化为按 `branches` 键遍历，跨分支比对逐条进行、缺 ref 明确 `skipped`。

### 2.2 采集执行复用能力层，工作台不重写浏览器逻辑

工作台只经 `rpa-core run` 子进程调用，接口是：
`--inputs`（喂 `${inputs.keyword}`）、`--artifacts`、`elementRefs` +
`<flowDir>/elements/*.json`、`browser.queryAll` / `browser.executeScript`、
`result.json.outputs.<step>.outputs.*`、`events.jsonl`、
`pause` / `resume` / `runs list|show`。

**不 import `src/rpa_core` 内部模块。** 因此 README `scope.out` 里排除的
FastAPI / 数据库 / 调度器 / React UI 仍只针对运行时核心，本决策不改变
ADR 0020 的定案，也不需要改 `scope.out`。

### 2.3 频率可自定义，并发上限不可自定义

`config/schedule.json` 的 mode / startAt / windowHours / cron / intervalMinutes /
batchSize / perItemIntervalSeconds 全可改。截图那组数字只是默认预设。

`maxConcurrentRuns` 恒为 1：ADR 0015 明确「扩展侧必须串行化 `connectNative`，
否则并发调用会拉起多个 host 进程」。所以「频率」只改触发时刻与间隔，
不改为并发数；调度器产出有序时刻，执行层一次取一个。
该键写成别的值或被删掉都判配置非法，不静默纠正。

固定时刻用 `sha256(item_id) % window_seconds`，保证同一商品每天同一时刻采，
跨天快照可对齐；随机 jitter 只作用于批内间隔。

## 3. 后果

正面：三条分支职责清楚，`ppx-only` / `plugin-only` 的既有定案一行未动；
核心运行时零改动，工作台挂了不会污染能力层门禁；频率调整不碰代码。

代价与欠账（详见 `apps/xhs-workbench/README.md`）：真机 E2E 未验证（登录态、
选择器、`executeScript` 实际返回值）；跨 run 会话复用未实测，P0 仍每次
`browser.close`；快照库、告警规则、监控池状态机、HTTP 与 UI 都在 P1/P2；
应用层单测暂未接进 `check_all.py`，接线时要补哨兵证明真被跑到。

## 4. 合规边界

采集频率默认值是为降低平台风控风险而设的保守预设，不是对抗平台规则的手段。
实际运行的频率、范围与数据使用由维护者自行把关；把间隔调到 0 之前，
UI 应先显示 `estimate()` 给出的每日请求数与峰值 QPS。
