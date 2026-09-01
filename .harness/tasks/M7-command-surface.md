# M7 命令面小扩展

状态：`active`

## 目标

收敛 BACKLOG 中已记录的两个命令面候选，保持小步快跑：`desktop.win32` 补齐 `timeoutMs` 实现与 `desktop.uia` 的对称契约；新增 `data.format` 模板化渲染命令（源自"搜索词写进 txt 头部"这类真实诉求）。

前置：M6 已交付调用方文档；命令面扩展全部走 manifest + 合同测试。

## 任务

- [ ] `desktop.win32.attachWindow` / `findElement` manifest 声明 `timeoutMs`，executor 实现与 `desktop.uia` 相同的轮询语义（含 0 匹配等待、歧义立即失败）
- [ ] 合同测试：win32 超时语义（超时 ELEMENT_NOT_FOUND、timeoutMs 缺省行为不变）
- [ ] 新增 `data.format`：模板字符串渲染（`{placeholders}` 由 inputs 提供的映射替换），executor 走 `python.worker`，`effect: pure/safe`
- [ ] 模板安全边界：未知占位符的处理策略（保留原样 or 报错，倾向报错 fail-fast，与不变量 14 精神一致）
- [ ] 合同测试：format 正常渲染 / 未知占位符拒绝 / 非字符串值 str 化
- [ ] 完整 harness 门禁通过

## 验收标准

- [ ] `desktop.win32` 与 `desktop.uia` 的 attachWindow / findElement 输入契约对称（共享字段集一致）
- [ ] `data.format` 有契约测试且未知占位符 fail-fast
- [ ] 完整门禁通过

## 范围外

- 通用表达式引擎、字符串内插到 AST 引用（不变量 13 不动）
- 其他落盘格式（CSV / Excel）

## 待定问题

- `data.format` 占位符语法用 `{name}` 还是复用 `${name}`？（倾向 `{name}`，避免与 resolver 引用混淆）

## 完成证据

仅在全部验收标准通过后填写。
