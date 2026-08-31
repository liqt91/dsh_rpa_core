# M2 Windows 桌面自动化垂直切片

状态：`done`

## 目标

用第二种驱动类型证明 catalog、compiler、orchestrator、result 和 effect 契约能够复用，同时不向 Orchestrator 添加 Windows 专用逻辑。

## 最小命令集

- [x] `desktop.attachWindow`
- [x] `desktop.findElement`
- [x] `desktop.click`
- [x] `desktop.input`
- [x] `desktop.getText`
- [x] `desktop.closeSession`

## 任务

- [x] 定义类型化桌面 locator 和显式 session 模型。
- [x] 选择并记录 Windows 自动化依赖。
- [x] 强制程序化绑定窗口，禁止依赖当前焦点。
- [x] 定义零匹配和多匹配行为。
- [x] 实现 executor 清理、取消和超时语义。
- [x] 构建确定性的本地桌面测试程序。
- [x] 添加启动、绑定、输入、点击、回读、保存和关闭的 E2E workflow。
- [x] 引入 native binding 依赖前编写 ADR。

## 验收标准

- [x] Orchestrator 中不存在 Win32 或 UIA 分支。
- [x] 每个桌面操作都需要显式 session ID。
- [x] 零匹配必须失败，多匹配策略必须确定。
- [x] 桌面 E2E 无需依赖前台焦点即可通过。
- [x] Windows 上完整 harness 门禁通过。

## 范围外

- macOS 和 Linux 桌面驱动。
- 图像匹配和 OCR。
- 远程桌面与多用户 session。
- 旧元素库导入。

## 待定问题

- 直接使用 UI Automation、使用 pywinauto，还是采用其他 adapter？
- 进程重启后是否尝试恢复窗口 session？

## 完成证据

2026-08-31 | `desktop.uia` / `desktop.win32` 双后端桌面切片完成；Win32 记事本流程与合同测试通过，`uv run python .harness/scripts/check_all.py` 全门禁通过。
