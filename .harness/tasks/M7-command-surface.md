# M7 命令面小扩展

状态：`done`

## 附带调研结论（S0 登录态复用实验）

- 实验 C（CDP attach 用户日常 Chrome）实测失败：Chrome 152 默认用户目录上的 `--remote-debugging-port` 被上游安全策略静默忽略（136+ 反 infostealer 改动），显式 `--user-data-dir` 指向默认目录同样无效，无策略可绕过。
- 重评结论：live 复用日常浏览器登录态**只有浏览器扩展路线**；非扩展备选 = 专用持久 profile（自动化浏览器内每站点登录一次）。最终决策并入 ADR 0007（M8）。
- 顺带修复：UIA 桌面枚举在系统繁忙时抛 COM `RPC_E_SERVERCALL_RETRYLATER` 被兜底成 `EXECUTOR_FAILED` 的健壮性缺口——`timeoutMs` 轮询窗口内改为视为"未找到"继续重试。

## 目标

收敛 BACKLOG 中已记录的两个命令面候选，保持小步快跑：`desktop.win32` 补齐 `timeoutMs` 实现与 `desktop.uia` 的对称契约；新增 `data.format` 模板化渲染命令（源自"搜索词写进 txt 头部"这类真实诉求）。

前置：M6 已交付调用方文档；命令面扩展全部走 manifest + 合同测试。

## 任务

- [x] `desktop.win32.attachWindow` / `findElement` manifest 声明 `timeoutMs`，executor 实现与 `desktop.uia` 相同的轮询语义（含 0 匹配等待、歧义立即失败；`findElement` 此前声明未实现，一并修复）
- [x] 合同测试：win32 超时语义（超时 ELEMENT_NOT_FOUND、timeoutMs 缺省行为不变）
- [x] 新增 `data.format`：模板字符串渲染（`{placeholders}` 由 inputs 提供的映射替换），executor 走 `python.worker`，`effect: pure/safe`，非字符串值 JSON 渲染
- [x] 模板安全边界：未知占位符 fail-fast（`INVALID_INPUT` + `missing` 列表，与不变量 14 fail-fast 精神一致）
- [x] 合同测试：format 正常渲染 / 未知占位符拒绝 / 列表值 JSON 渲染 / 无占位符透传
- [x] 完整 harness 门禁通过

## 验收标准

- [x] `desktop.win32` 与 `desktop.uia` 的 attachWindow / findElement 输入契约对称（双方均声明 `timeoutMs`，合同测试锁定）
- [x] `data.format` 有契约测试且未知占位符 fail-fast
- [x] 完整门禁通过

## 范围外

- 通用表达式引擎、字符串内插到 AST 引用（不变量 13 不动）
- 其他落盘格式（CSV / Excel）

## 待定问题

- ~~`data.format` 占位符语法~~ 已结论：`{name}`，与 resolver 引用 `${...}` 天然区分

## 完成证据

- `commands/desktop_win32/attachWindow.json` / `findElement.json`：`timeoutMs` 声明
- `src/rpa_core/executors/desktop_win32.py`：attach/find 轮询；`desktop.py`：COM 繁忙重试容错
- `commands/data/format.json` + `src/rpa_core/workers/python_worker.py`：format 分支（fail-fast 占位符校验）
- `tests/contract/`：win32 超时测试、对称性 timeoutMs 断言、format 4 组断言；catalog 26 条
- 完整门禁通过：72 tests × 3 轮（Chrome 运行环境下）+ ruff + architecture + task check（`FULL GATE PASSED`）
