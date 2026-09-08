# rpa_core 指令优化规划

基于影刀72条指令（网页44 + 桌面28）的系统对比，制定以下优化计划。

---

## 一、sessionId 字段优化

### 现状

- 所有 session 类指令共用 `sessionId: string`，browser 和 desktop 无类型区分
- 编辑器属性面板已实现「引用创建会话的节点」下拉
- 但下拉不区分资源类型，browser sessionId 可传给 desktop 指令

### 优化项

| # | 优化 | 说明 | 优先级 |
|---|---|---|---|
| S1 | 资源类型标签 | `output_schema` 增加 `resourceType: "webPage" \| "windowHandle"`，editor 下拉按类型过滤 | P0 |
| S2 | 引用语法统一 | 统一为 `${steps.<node>.outputs.sessionId}`，无需改动 | — |

### 实现方式

```jsonc
// browser.launch output_schema
{
  "type": "object",
  "properties": {
    "sessionId": { "type": "string" },
    "resourceType": { "type": "string", "const": "webPage" }
  }
}

// desktop.attachWindow output_schema
{
  "type": "object",
  "properties": {
    "sessionId": { "type": "string" },
    "resourceType": { "type": "string", "const": "windowHandle" },
    "processId": { "type": "integer" }
  }
}
```

editor 侧：`schemaField` 的 `session-reference` 下拉读取已注册的 `resourceType`，只显示匹配类型的节点。

---

## 二、现有指令参数补齐

### browser.click（点击元素） ✅ 已完成

| 新增参数 | 类型 | 默认值 | 影刀参照 |
|---|---|---|---|
| `clickType` | `enum: ["single", "double"]` | `"single"` | 点击元素_web「点击方式」 |
| `button` | `enum: ["left", "right", "middle"]` | `"left"` | 点击元素_web「鼠标按钮」 |
| `modifiers` | `array[string]` | `[]` | 点击元素_web「辅助按键」(Alt/Ctrl/Shift/Win) |
| `clickPosition` | `enum: ["center", "random"]` | `"center"` | 点击元素_web「点击位置」 |
| `simulateHuman` | `boolean` | `true` | 点击元素_web「模拟人工点击」 |
| `postDelayMs` | `integer` | `0` | 点击元素_web「执行后延迟」 |

### browser.input（填写输入框）

| 新增参数 | 类型 | 默认值 | 影刀参照 |
|---|---|---|---|
| `mode` | `enum: ["setValue", "simulateHuman", "clipboard", "automation"]` | `"setValue"` | 填写输入框_web「输入模式」 |
| `append` | `boolean` | `false` | 填写输入框_web「追加输入」 |
| `pressEnter` | `boolean` | `false` | 填写输入框_web「输入后按Enter」 |
| `clearFirst` | `boolean` | `true` | 填写输入框_web「清空原有内容」 |

### browser.close（关闭网页）

| 新增参数 | 类型 | 默认值 | 影刀参照 |
|---|---|---|---|
| `forceKill` | `boolean` | `false` | 关闭网页「终止浏览器进程」 |
| `ignoreUnload` | `boolean` | `false` | 关闭网页「忽略确认离开对话框」 |

### desktop.attachWindow（获取窗口对象）

| 新增参数 | 类型 | 默认值 | 影刀参照 |
|---|---|---|---|
| `matchMode` | `enum: ["exact", "contains", "regex"]` | `"exact"` | 获取窗口对象「匹配方式」 |

### desktop.click / desktop.win32.click（点击元素-win）

| 新增参数 | 类型 | 默认值 | 影刀参照 |
|---|---|---|---|
| `clickType` | `enum: ["single", "double", "right"]` | `"single"` | 点击元素_win「点击方式」 |
| `button` | `enum: ["left", "right", "middle"]` | `"left"` | 点击元素_win「鼠标按钮」 |

### desktop.input / desktop.win32.input（填写输入框-win）

| 新增参数 | 类型 | 默认值 | 影刀参照 |
|---|---|---|---|
| `mode` | `enum: ["setValue", "simulateHuman", "clipboard"]` | `"setValue"` | 填写输入框_win「输入模式」 |
| `append` | `boolean` | `false` | 填写输入框_win「追加输入」 |

### desktop.closeSession / desktop.win32.closeSession（关闭软件窗口）

| 新增参数 | 类型 | 默认值 | 影刀参照 |
|---|---|---|---|
| `forceKill` | `boolean` | `false` | 关闭软件窗口「是否终止进程」 |

---

## 三、新增指令

### 浏览器类（影刀有、我们没有）

| 指令 | 说明 | 复杂度 |
|---|---|---|
| `browser.hover` | 鼠标悬停在元素上 | 低 |
| `browser.drag` | 拖拽元素 | 中 |
| `browser.select` | 设置下拉框 | 低 |
| `browser.screenshot` | 网页截图 | 中 |
| `browser.executeScript` | 执行JS脚本 | 中 |
| `browser.setCookie` | 设置Cookie | 低 |
| `browser.removeCookie` | 移除Cookie | 低 |
| `browser.getCookie` | 获取Cookie | 低 |
| `browser.startListening` | 开始监听网页请求 | 高 |
| `browser.stopListening` | 停止监听网页请求 | 高 |
| `browser.getListenResult` | 获取网页监听结果 | 中 |
| `browser.upload` | 上传文件 | 高 |
| `browser.download` | 下载文件 | 高 |
| `browser.handleDialog` | 处理网页对话框(alert/confirm/prompt) | 中 |

### 桌面类（影刀有、我们没有）

| 指令 | 说明 | 复杂度 |
|---|---|---|
| `desktop.activateWindow` | 激活软件窗口 | 低 |
| `desktop.setWindowState` | 设置窗口状态(最大化/最小化/正常) | 低 |
| `desktop.setWindowVisible` | 设置窗口是否显示 | 低 |
| `desktop.moveWindow` | 移动窗口位置 | 低 |
| `desktop.resizeWindow` | 调整窗口大小 | 低 |
| `desktop.getWindowList` | 获取窗口对象列表 | 低 |
| `desktop.hover` | 鼠标悬停在元素上 | 低 |
| `desktop.drag` | 拖拽元素 | 中 |
| `desktop.select` | 设置下拉框 | 低 |
| `desktop.screenshot` | 元素截图 | 中 |
| `desktop.getElementPosition` | 获取元素位置 | 低 |
| `desktop.getWindowTitle` | 获取窗口信息 | 低 |
| `desktop.getSelectedText` | 获取选中文本 | 低 |
| `desktop.runOrOpen` | 运行或打开文件/程序 | 中 |

### 优先级排序

**第一批（高价值、低复杂度）**：
- browser.hover / desktop.hover（悬停，常见交互）
- desktop.activateWindow / setWindowState / moveWindow / resizeWindow（窗口管理，桌面自动化基础）
- desktop.getWindowTitle / getSelectedText（数据提取，常见需求）
- desktop.runOrOpen（运行程序，桌面自动化入口）

**第二批（中等复杂度）**：
- browser.executeScript（JS执行，高级场景）
- browser.screenshot / desktop.screenshot（截图，常见需求）
- browser.select / desktop.select（下拉框选择，表单场景）
- desktop.drag（拖拽，滑块场景）

**第三批（高复杂度）**：
- browser.upload / browser.download（文件操作，涉及系统对话框）
- browser.startListening / stopListening / getListenResult（网络监听，数据抓取场景）

---

## 四、通用参数标准化

### 超时参数

| 现状 | 影刀 | 目标 |
|---|---|---|
| `timeoutMs` (毫秒) | `等待元素存在(s)` (秒) | **保留毫秒**，与系统 API 一致 |

### 等待参数

所有元素操作类指令统一增加：

```jsonc
"waitTimeout": {
  "type": "number",
  "default": 5000,
  "description": "等待元素出现/消失的超时时间(ms)"
},
"postDelay": {
  "type": "number",
  "default": 0,
  "description": "执行完成后的延迟时间(ms)"
}
```

### 错误处理参数

影刀每个指令都有「错误处理方式」下拉（继续执行/停止/重试）。rpa_core 已有 `errorPolicy`，覆盖此模式，无需额外参数。

---

## 五、实现路径

### Phase 1：参数补齐（不新增指令）

改动现有14个指令的 manifest，新增缺失参数。执行器侧按需实现（参数缺失时用默认值）。

工作量：14 个 manifest 改动 + 编辑器属性面板支持新参数类型（enum 下拉、boolean 复选框）

### Phase 2：第一批新指令

新增8个低复杂度指令：hover×2、窗口管理×5、数据提取×2。

工作量：8 个 manifest + 8 个 executor + 编辑器命令面板注册

### Phase 3：第二批新指令

新增6个中等复杂度指令：executeScript、screenshot×2、select×2、drag。

### Phase 4：第三批新指令

新增文件操作和网络监听等高复杂度指令。

---

## 六、验收标准

- [x] sessionId 按 resourceType 过滤，browser 和 desktop 不能互传
- [x] browser.click 支持 clickType/button/modifiers/position/simulateHuman
- [ ] browser.input 支持 mode/append/pressEnter/clearFirst
- [ ] browser.close 支持 forceKill/ignoreUnload
- [ ] desktop.attachWindow 支持 matchMode(exact/contains/regex)
- [ ] 所有元素操作指令支持 waitTimeout + postDelay
- [ ] 第一批8个新指令全部通过 E2E 测试
- [ ] 命令清单 HTML 同步更新
