# M2 Windows 桌面测试 fixture 方案

## 目标

提供一个最小、确定性的 Windows 桌面流程，用于验证 `desktop.win32.attachWindow`、`desktop.win32.menuSelect`、`desktop.win32.findElement`、`desktop.win32.input`、`desktop.win32.hotkey`、`desktop.win32.closeSession` 的端到端语义。

## 设计原则

1. **固定窗口标题**：程序启动后窗口标题必须稳定，便于 `attachWindow` 精确匹配。
2. **固定菜单路径**：记事本菜单文字必须稳定，便于 `menuSelect` 精确触发。
3. **无焦点依赖**：输入、点击、读取都应通过 Win32 消息或控件方法完成。
4. **最小交互闭环**：通过打开对话框载入文件，再回读打开后的窗口标题。
5. **可重复执行**：多次运行不依赖外部状态，不产生随机性。

## 建议目录结构

```text
examples/windows-desktop/
  workflow.json
  README.md
  fixture/
    README.md
    app/
      (WinForms source files)
```

## 推荐控件约定

- 主窗口标题：`无标题 - 记事本`
- 打开对话框标题：`打开`
- 文件名输入框：`Edit` / `controlId=1148`
- 打开按钮：`打开(&O)`

## 交互流程

1. 启动记事本并显示主窗口。
2. `desktop.win32.attachWindow` 按标题绑定主窗口。
3. `desktop.win32.menuSelect` 打开文件对话框。
4. `desktop.win32.attachWindow` 绑定对话框。
5. `desktop.win32.findElement` 定位文件名输入框。
6. `desktop.win32.input` 输入文件路径。
7. `desktop.win32.hotkey` 回车确认打开。
8. `desktop.win32.attachWindow` 绑定打开后的窗口。
9. `desktop.win32.closeSession` 释放会话引用。

## 验收预期

- 零匹配返回 `ELEMENT_NOT_FOUND`
- 多匹配返回 `ELEMENT_AMBIGUOUS`
- 绑定窗口不依赖当前前台焦点
- 打开后的窗口标题与文件名一致

## 后续实现顺序

1. 补 fixture README
2. 新增 WinForms 示例程序
3. 把 E2E 测试接到该程序
4. 再跑 full gate 和桌面验证
