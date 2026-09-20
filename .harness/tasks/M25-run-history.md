# M25 运行历史浏览与回放

状态：`active`

关联：ADR 0011（子进程 run host）、M21/M24（暂停/继续、断点与单步）、`docs/gui-run-control.md`
现状：每次 run 的证据已完整落盘在 `run_artifacts/<run_id>/`（`result.json`、`events.jsonl`、
`checkpoint.json`、`control.json`），但**没有入口**把它们列出来看——用户只能去翻目录。

## 目标

给「历史运行」一个一等入口（GUI 为主形态，ADR 0016），能：

1. **列出**本机（当前流程库对应的 artifacts 目录）的历史运行：run_id、流程名、开始/结束时间、
   状态（succeeded/failed/cancelled/paused/…）、耗时、失败错误码；
2. **打开一次历史运行**：读该 run 的 `events.jsonl` 还原**事件时间线**（与运行面板同款格式化：
   步骤耗时、输出值预览、暂停原因、失败详情），并支持「跳到该节点」定位画布；
3. **回放/复用**：从历史运行一键「用同样输入再跑一次」（`inputs` 已在 checkpoint/result 里），
   以及「从该 run 的检查点继续/单步」（M21/M24 已具备的能力，这里只补入口）。

## 关键设计（先行定案）

- **只读优先**：历史浏览不引入新的持久化格式，直接读现有 `run_artifacts`；不建数据库
  （AGENTS 规则 10）。
- **不加载 runtime**：列表/事件读取放能力层（devserver store 或 GUI 侧只读读取器），
  沿用 ADR 0011「GUI/编辑器不承载 runtime」的边界；GUI 直接读文件即可（同 `_poll_run`）。
- **扫描范围**：默认当前流程库的 artifacts 根（`<workflows_root>/../run_artifacts`），
  按 mtime 倒序；单条详情懒加载（列表只读 `result.json` 的头部字段，避免大目录全量解析）。
- **回放语义**：`inputs` 来自历史 run（`result.json`/`checkpoint.scopes.inputs`）；「再跑一次」
  是**新 run**（新 run_id），不是原地重放——避免与副作用契约（ADR 0002）冲突。
- **范围外**：跨机器/跨用户的运行中心、运行对比（diff）、视频回放、指标聚合。

## 任务（切片）

- [x] **S1 只读读取器（能力层）**（2026-09-20 done）
  - 新增 `run_artifacts` 扫描与解析：`list_runs(artifacts_root, *, limit)` → 摘要列表
    （runId/工作流/状态/起止时间/耗时/错误码/是否有检查点可恢复）；`read_run(artifacts_root, run_id)`
    → 事件列表 + 结果 + 检查点摘要（容错：损坏/半写文件跳过而不是抛错）。
  - CLI：`rpa-core runs list [--limit N]` / `rpa-core runs show <run_id>`（JSON 输出，通道对齐
    ADR 0006 §6）。
  - 验收：对既有 `run_artifacts` 目录可列出与查看；损坏文件不影响整体。
- [ ] **S2 GUI 历史运行面板**
  - 运行菜单/工具栏加「运行历史」dock：表格列出摘要（时间倒序），双击打开事件时间线
    （复用 `_format_event`），带「跳到节点」「用同样输入再跑一次」「继续/单步（若 paused）」。
  - 验收：GUI 可浏览历史、定位节点、一键再跑；离线（无 artifacts）时给出空态提示。
- [ ] **S3 测试与文档**
  - 单测：扫描/解析/容错/排序/limit；契约：CLI 输出形状、GUI 面板渲染与动作接线。
  - 文档：`docs/gui-run-control.md` 或新 `docs/run-history.md`；PROGRESS 记录。
  - 验收：full gate 通过。

## 验收

- 历史运行可列出、可查看事件时间线、可定位节点、可用同样输入再跑；
- 不新增持久化格式、不引入数据库、GUI 不承载 runtime；
- 每切片：GUI 合同测试（offscreen）+ `check_all.py` 全门禁通过；PROGRESS 追加一行。

## 风险 / 注意

- `run_artifacts` 会随时间增长（每次运行一个目录）：列表默认 limit（如 50）并按 mtime 倒序，
  避免首次打开就全量解析。
- 事件文件可能很大（长流程）：详情按需读取，必要时只读尾部 N 行（时间线倒序展示）。
- 「再跑一次」必须用**历史输入**而不是当前画布（否则语义漂移）；界面上要写清。
