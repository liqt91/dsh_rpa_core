# BACKLOG — 指令优化规划

基于影刀72条指令对比分析，分四个 Phase 推进。

---

## Phase 1：参数补齐（command-params-phase1）

### P1-S1：sessionId resourceType 标签 ✅

**改动范围**：
- `commands/browser.launch.json` output_schema 加 `"resourceType": {"type": "string", "const": "webPage"}`
- `commands/desktop.attachWindow.json` output_schema 加 `"resourceType": {"type": "string", "const": "windowHandle"}`
- `commands/desktop.win32.attachWindow.json` 同上
- `src/rpa_core/executors/browser.py` launch 两处产出加 `"resourceType": "webPage"`
- `src/rpa_core/executors/desktop.py` attachWindow 产出加 `"resourceType": "windowHandle"`
- `src/rpa_core/executors/desktop_win32.py` 同上
- `src/rpa_core/devserver/static/app.js` collectSessionNodes 按 resourceType 过滤下拉
- i18n.js 补 resourceType 标签
- `tests/contract/test_desktop_contract.py` 更新断言

**验收**：browser 类节点下拉只显示 webPage 节点，desktop 类只显示 windowHandle 节点

### P1-S2：browser.click 参数补齐 ✅

**改动范围**：
- `commands/browser.click.json` input_schema 新增：
  - `clickType`: `enum ["single", "double"]`, default `"single"`
  - `button`: `enum ["left", "right", "middle"]`, default `"left"`
  - `modifiers`: `array[string]`, items enum `["Alt", "Ctrl", "Shift", "Win"]`
  - `clickPosition`: `enum ["center", "random"]`, default `"center"`
  - `simulateHuman`: `boolean`, default `true`
  - `postDelayMs`: `integer`, default `0`
- `commands/desktop.click.json` / `commands/desktop.win32.click.json` 同步新增（button 仅 left/right）
- `src/rpa_core/executors/browser.py` click handler 实现新参数
- `src/rpa_core/executors/desktop.py` click handler 实现新参数
- `src/rpa_core/executors/desktop_win32.py` click handler 实现新参数
- i18n.js 补字段标签
- `src/rpa_core/executors/browser_bsk.py` 同上（bsk 支持的参数）
- 合同测试补充新参数验证
- i18n.js 补标签

**验收**：browser.click 支持双击、右键、辅助键组合

### P1-S3：browser.input 参数补齐 ✅

**改动范围**：
- `commands/browser.input.json` input_schema 新增：
  - `mode`: `enum ["fill", "type", "clipboard"]`, default `"fill"`
  - `append`: `boolean`, default `false`
  - `pressEnter`: `boolean`, default `false`
  - `keyIntervalMs`: `integer`, default `50`
  - `clickBeforeInput`: `boolean`, default `false`
  - `postDelayMs`: `integer`, default `0`
- `commands/desktop.input.json` / `commands/desktop.win32.input.json` 同步（mode: simulateHuman/clipboard/automation）
- executor 实现各 mode（browser: fill/type/clipboard；desktop: set_edit_text/type_keys/clipboard）
- i18n.js 补标签

**验收**：browser.input 支持追加输入、模拟人工输入、剪贴板粘贴、输入后按 Enter

### P1-S4：browser.close 参数补齐 ✅

**改动范围**：
- `commands/browser.close.json` input_schema 新增：
  - `forceKill`: `boolean`, default `false`
  - `ignoreUnload`: `boolean`, default `true`
- executor 实现 forceKill（SIGTERM 进程）

**验收**：browser.close 支持强制终止进程

### P1-S5：desktop.closeSession forceKill ✅

**改动范围**：
- `commands/desktop.closeSession.json` / `commands/desktop.win32.closeSession.json` input_schema 新增：
  - `forceKill`: `boolean`, default `false`
- executor 实现 forceKill（TerminateProcess）

**验收**：desktop.closeSession 支持强制终止进程

### P1-S6：desktop.attachWindow matchMode ✅

**改动范围**：
- `commands/desktop.attachWindow.json` / `commands/desktop.win32.attachWindow.json` input_schema 新增：
  - `matchMode`: `enum ["exact", "contains", "regex"]`, default `"exact"`
  - `className`: `string` 描述补充
- executor 实现 matchMode（_filter_windows + _windows_by_title_fallback 支持 contains/regex）

**验收**：desktop.attachWindow 支持精确/包含/正则匹配窗口标题

### P1-S4：browser.close 参数补齐

**改动范围**：
- `commands/browser.close.json` input_schema 新增：
  - `forceKill`: `boolean`, default `false`
  - `ignoreUnload`: `boolean`, default `false`
- executor 实现 forceKill（browser.close + process kill）

**验收**：browser.close 支持强制终止进程

### P1-S5：desktop.attachWindow 参数补齐

**改动范围**：
- `commands/desktop.attachWindow.json` input_schema 新增：
  - `matchMode`: `enum ["exact", "contains", "regex"]`, default `"exact"`
- `commands/desktop.win32.attachWindow.json` 同上
- executor 按 matchMode 实现不同匹配逻辑

**验收**：desktop.attachWindow 支持正则匹配窗口标题

### P1-S6：desktop.click 参数补齐

**改动范围**：
- `commands/desktop.click.json` + `desktop.win32.click.json` 新增：
  - `clickType`: `enum ["single", "double", "right"]`, default `"single"`
  - `button`: `enum ["left", "right", "middle"]`, default `"left"`
- executor 实现

**验收**：desktop.click 支持双击、右键

### P1-S7：desktop.input 参数补齐

**改动范围**：
- `commands/desktop.input.json` + `desktop.win32.input.json` 新增：
  - `mode`: `enum ["setValue", "simulateHuman", "clipboard"]`, default `"setValue"`
  - `append`: `boolean`, default `false`
- executor 实现

**验收**：desktop.input 支持追加输入、模拟人工输入

### P1-S8：desktop.closeSession 参数补齐

**改动范围**：
- `commands/desktop.closeSession.json` + `desktop.win32.closeSession.json` 新增：
  - `forceKill`: `boolean`, default `false`
- executor 实现

**验收**：desktop.closeSession 支持强制终止进程

---

## Phase 2：第一批新指令（command-new-batch1）

低复杂度，8条指令。

### P2-S1：browser.hover

**Manifest**：
```json
{
  "kind": "browser.hover",
  "name": "鼠标悬停",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "selector": {"type": "string", "minLength": 1},
    "waitTimeout": {"type": "number", "default": 5000}
  },
  "outputs": {}
}
```

**Executor**：Playwright `page.hover(selector)` / bsk `hover(selector)`
**参照**：影刀「鼠标悬停在元素上_web」

### P2-S2：desktop.activateWindow

**Manifest**：
```json
{
  "kind": "desktop.activateWindow",
  "name": "激活软件窗口",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1}
  },
  "outputs": {}
}
```

**Executor**：UIA `element.SetFocus()` / Win32 `SetForegroundWindow`
**参照**：影刀「激活软件窗口」

### P2-S3：desktop.setWindowState

**Manifest**：
```json
{
  "kind": "desktop.setWindowState",
  "name": "设置窗口状态",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "state": {"type": "enum", "enum": ["maximized", "minimized", "normal"], "default": "normal"}
  },
  "outputs": {}
}
```

**Executor**：Win32 `ShowWindow(SW_MAXIMIZE/SW_MINIMIZE/SW_RESTORE)`
**参照**：影刀「设置窗口状态」

### P2-S4：desktop.setWindowVisible

**Manifest**：
```json
{
  "kind": "desktop.setWindowVisible",
  "name": "设置窗口是否显示",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "visible": {"type": "boolean", "default": true}
  },
  "outputs": {}
}
```

**Executor**：Win32 `ShowWindow(SW_SHOW/SW_HIDE)`
**参照**：影刀「设置窗口是否显示」

### P2-S5：desktop.moveWindow

**Manifest**：
```json
{
  "kind": "desktop.moveWindow",
  "name": "移动窗口位置",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "x": {"type": "integer"},
    "y": {"type": "integer"}
  },
  "outputs": {}
}
```

**Executor**：Win32 `MoveWindow(x, y, width, height, TRUE)` — 保持宽高不变
**参照**：影刀「移动窗口位置」

### P2-S6：desktop.resizeWindow

**Manifest**：
```json
{
  "kind": "desktop.resizeWindow",
  "name": "调整窗口大小",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "width": {"type": "integer"},
    "height": {"type": "integer"}
  },
  "outputs": {}
}
```

**Executor**：Win32 `MoveWindow(x, y, width, height, TRUE)` — 保持位置不变
**参照**：影刀「调整窗口大小」

### P2-S7：desktop.getWindowTitle

**Manifest**：
```json
{
  "kind": "desktop.getWindowTitle",
  "name": "获取窗口信息",
  "effect": {"kind": "read", "replay": "safe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1}
  },
  "outputs": {
    "title": {"type": "string"},
    "className": {"type": "string"},
    "processId": {"type": "integer"}
  }
}
```

**Executor**：Win32 `GetWindowText` / `GetClassName` / `GetWindowThreadProcessId`
**参照**：影刀「获取窗口信息」

### P2-S8：desktop.getSelectedText

**Manifest**：
```json
{
  "kind": "desktop.getSelectedText",
  "name": "获取选中文本",
  "effect": {"kind": "read", "replay": "safe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1}
  },
  "outputs": {
    "text": {"type": "string"}
  }
}
```

**Executor**：UIA `GetSelection()` / Win32 `EM_GETSEL` + `VM_GETTEXT`
**参照**：影刀「获取选中文本」

---

## Phase 3：第二批新指令（command-new-batch2）

中等复杂度，6条指令。

### P3-S1：browser.executeScript

**Manifest**：
```json
{
  "kind": "browser.executeScript",
  "name": "执行JS脚本",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "script": {"type": "string", "minLength": 1},
    "args": {"type": "array", "items": {"type": "any"}, "default": []},
    "waitTimeout": {"type": "number", "default": 5000}
  },
  "outputs": {
    "result": {"type": "any"}
  }
}
```

**Executor**：Playwright `page.evaluate(script, args)` / bsk `evaluate(script)`
**参照**：影刀「执行JS脚本」

### P3-S2：browser.screenshot

**Manifest**：
```json
{
  "kind": "browser.screenshot",
  "name": "网页截图",
  "effect": {"kind": "idempotent-write", "replay": "idempotent", "idempotency": "derived"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "selector": {"type": "string"},
    "savePath": {"type": "string", "minLength": 1},
    "fullPage": {"type": "boolean", "default": false}
  },
  "outputs": {
    "filePath": {"type": "string"}
  }
}
```

**Executor**：Playwright `page.screenshot(path, fullPage)` / element `element.screenshot(path)`
**参照**：影刀「网页截图」

### P3-S3：browser.select

**Manifest**：
```json
{
  "kind": "browser.select",
  "name": "设置下拉框",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "selector": {"type": "string", "minLength": 1},
    "selectBy": {"type": "enum", "enum": ["value", "label", "index"], "default": "value"},
    "value": {"type": "string"},
    "waitTimeout": {"type": "number", "default": 5000}
  },
  "outputs": {}
}
```

**Executor**：Playwright `page.selectOption(selector, value/label/index)`
**参照**：影刀「设置下拉框_web」

### P3-S4：desktop.screenshot

**Manifest**：
```json
{
  "kind": "desktop.screenshot",
  "name": "元素截图",
  "effect": {"kind": "idempotent-write", "replay": "idempotent", "idempotency": "derived"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "elementId": {"type": "string"},
    "savePath": {"type": "string", "minLength": 1}
  },
  "outputs": {
    "filePath": {"type": "string"}
  }
}
```

**Executor**：UIA `element.CaptureElementImage()` / Win32 `PrintWindow`
**参照**：影刀「元素截图_win」

### P3-S5：desktop.select

**Manifest**：
```json
{
  "kind": "desktop.select",
  "name": "设置下拉框",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "elementId": {"type": "string", "minLength": 1},
    "selectBy": {"type": "enum", "enum": ["value", "label", "index"], "default": "value"},
    "value": {"type": "string"},
    "waitTimeout": {"type": "number", "default": 5000}
  },
  "outputs": {}
}
```

**Executor**：UIA `SelectionItemPattern.Select()` / ComboBox 专用
**参照**：影刀「设置下拉框_win」

### P3-S6：desktop.drag

**Manifest**：
```json
{
  "kind": "desktop.drag",
  "name": "拖拽元素",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "elementId": {"type": "string", "minLength": 1},
    "targetX": {"type": "integer"},
    "targetY": {"type": "integer"},
    "waitTimeout": {"type": "number", "default": 5000}
  },
  "outputs": {}
}
```

**Executor**：Win32 `mouse_event(MOUSEEVENTF_LEFTDOWN/MOVE/LEFTUP)`
**参照**：影刀「拖拽元素_win」

---

## Phase 4：第三批新指令（command-new-batch3）

高复杂度，4条指令。需额外调研文件对话框处理方案。

### P4-S1：browser.upload

**Manifest**：
```json
{
  "kind": "browser.upload",
  "name": "上传文件",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "selector": {"type": "string", "minLength": 1},
    "files": {"type": "array", "items": {"type": "string"}},
    "waitTimeout": {"type": "number", "default": 5000}
  },
  "outputs": {}
}
```

**Executor**：Playwright `page.setInputFiles(selector, files)` — 不需要处理系统对话框
**参照**：影刀「上传文件」

### P4-S2：browser.download

**Manifest**：
```json
{
  "kind": "browser.download",
  "name": "下载文件",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "saveDir": {"type": "string", "minLength": 1},
    "waitTimeout": {"type": "number", "default": 30000}
  },
  "outputs": {
    "filePath": {"type": "string"}
  }
}
```

**Executor**：Playwright download event + `download.path()`
**参照**：影刀「下载文件」

### P4-S3：browser.handleDialog

**Manifest**：
```json
{
  "kind": "browser.handleDialog",
  "name": "处理网页对话框",
  "effect": {"kind": "unsafe-write", "replay": "unsafe", "idempotency": "none"},
  "inputs": {
    "sessionId": {"type": "string", "minLength": 1},
    "action": {"type": "enum", "enum": ["accept", "dismiss"], "default": "accept"},
    "promptText": {"type": "string"}
  },
  "outputs": {}
}
```

**Executor**：Playwright `page.on('dialog', dialog => dialog.accept/dismiss(promptText))`
**参照**：影刀「处理网页对话框」

### P4-S4：desktop.getWindowList

**Manifest**：
```json
{
  "kind": "desktop.getWindowList",
  "name": "获取窗口对象列表",
  "effect": {"kind": "read", "replay": "safe", "idempotency": "none"},
  "inputs": {
    "titlePattern": {"type": "string"},
    "matchMode": {"type": "enum", "enum": ["exact", "contains", "regex"], "default": "contains"}
  },
  "outputs": {
    "windows": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "sessionId": {"type": "string"}}}}
  }
}
```

**Executor**：Win32 `EnumWindows` + `GetWindowText` 按 pattern 过滤
**参照**：影刀「获取窗口对象列表」

---

## Phase 5：用户变量体系（var-system）

### 背景

当前 `sessionId` 等会话参数暴露了内部实现细节（`${steps.xxx.outputs.sessionId}`），用户需要理解引用路径语法。影刀的体验是：用户给输出命名（如 `web_page1`），后续节点直接选变量名。

### 方案

#### V1-S1：Model — ActionNode 加 output_name

**文件**：`src/rpa_core/model/workflow.py`

```python
class ActionNode(NodeBase):
    output_name: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_]\w*$",
        description="用户可读的输出变量名，如 web_page1"
    )
```

- 默认 `None` → 向后兼容，走原有 `${steps.xxx.outputs.yyy}` 逻辑
- 可选填 → 享受变量引用 `${var_name}`

#### V1-S2：Runtime — 输出存 scopes.variables

**文件**：`src/rpa_core/runtime/orchestrator.py`

```python
# 原有逻辑（保持兼容）：
scopes["steps"][node_id] = {"outputs": result.outputs, ...}

# 新增：如果有 output_name，同时存到 variables
output_name = getattr(node, "output_name", None)
if output_name:
    scopes.setdefault("variables", {})[output_name] = result.outputs
```

#### V1-S3：Resolver — 支持 ${var_name} 引用

**文件**：`src/rpa_core/runtime/resolver.py`

```python
_REFERENCE_VAR = re.compile(r"^\$\{([A-Za-z_]\w*)\}$")

def _lookup(path, scopes):
    # 优先查 variables
    variables = scopes.get("variables", {})
    if path in variables:
        return variables[path]
    # 原有逻辑：查 steps/inputs/loop/error
    parts = path.split(".")
    ...
```

引用语法优先级：`${var_name}` > `${steps.xxx.outputs.yyy}`

#### V1-S4：Editor — 节点属性加 output_name

**文件**：`src/rpa_core/devserver/static/app.js`

- 节点属性面板加「输出变量名」字段（可选填）
- 字段校验：`^[A-Za-z_]\w*$`

#### V1-S5：Editor — 下拉引用用变量名

**文件**：`src/rpa_core/devserver/static/app.js`

sessionId 下拉逻辑改为：
1. 收集所有有 `output_name` 的节点
2. 下拉显示：`变量名（节点中文名）`
3. 引用语法：`${var_name}`

无 `output_name` 的节点 fallback：`${steps.xxx.outputs.sessionId}`

#### V1-S6：迁移测试文件

**文件**：`tests/fixtures/` 下所有 workflow JSON

- 为现有节点补充 `output_name`（可选）
- 或保持 `None` 走兼容逻辑

#### V1-S7：全量测试 + gate

```bash
uv run pytest tests/ -x -q --ignore=tests/e2e
uv run ruff check src/ commands/
uv run python .harness/scripts/check_all.py
```

### 验收标准

1. `browser.launch` 可配置 `output_name: "web_page1"`
2. `browser.close` 下拉可选 `web_page1`，引用显示 `${web_page1}`
3. 运行时正确解析 `${web_page1}` → `scopes["variables"]["web_page1"]`
4. 无 `output_name` 的节点走原有 `${steps.xxx.outputs.yyy}` 逻辑
5. 所有测试通过

### 落地记录（2026-09-08，Phase 5 done）

> 与维护者确认方向后实现，对原方案 V1-S3/V1-S5 有一处技术性增强，其余照方案落地。

**关键增强：变量子路径引用 `${var_name.field}`**

- 背景：`output_name` 存的是**整个 outputs dict**（如 browser.launch → `{sessionId, resourceType}`）。若照 V1-S3 字面只支持单段 `${web_page1}`，解析结果是整个 dict，下游 `sessionId`（string）字段会收到 dict 而失败。
- 决策：resolver 支持变量前缀 + 子路径 —— `${web_page1}` 返回整个 dict；`${web_page1.sessionId}` 返回 `outputs["sessionId"]`。编辑器 sessionId 下拉对已命名节点填 `${name}.sessionId`。
- 残留：`${var_name}` 整体引用仍可用（需 object 型字段时）。

**编译期前向/未知变量校验（强于 V1 的"运行时校验"）**

- compiler 预扫描 `_collect_output_names`（全流程 output_name 集合），与现有 `${steps.*}` 前向引用语义一致：
  - `root` ∈ 前序 output_names → 放行（单段或子路径）
  - `root` ∈ 全流程但靠后声明 → `Forward variable reference`
  - `root` 不在任何处且为单段裸名 / 多段未知根 / error_var 越界 → `Unsupported reference root`
- 保持 error_var 词法作用域（只在 catch 子树内可引用），回归测试 `test_try_error_variable_is_lexically_scoped` 通过。

**V1-S1~S7 落地清单**：S1 model（output_name 字段）✅ / S2 runtime（scopes.variables 存储）✅ / S3 resolver（整值+子路径）✅ / S4 editor（属性面板「输出变量名」，data-field 定位）✅ / S5 editor（sessionId 下拉变量名显示，change 分支生成引用）✅ / S6 现有 workflow 保持 None 兼容、无需迁移 ✅ / S7 测试 + gate ✅。

**新增测试**：resolver 单测 5（整值/子路径/缺失字段/未定义/变量优先）；protocols 编译 3（后向放行/前向拒绝/未声明拒绝）；runtime 集成 1（producer output_name → consumer `${var.field}` 解析贯穿）；e2e 1（editor 命名 launch → click 下拉引用 `${web_page1.sessionId}`）。
