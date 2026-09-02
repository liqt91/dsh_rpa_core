# 编辑器美化与中文化设计方案

状态：方案定稿（待实现，建议立 M12）
关联：M9 编辑器 v1、M11 树形画布、隔壁项目 `rpa_script/src/ui/workflow-editor` 设计调研

## 1. 现状问题

| 问题 | 根因 |
|---|---|
| 左侧命令面板出现横向+纵向滚动条，拖命令时手抖 | 条目单行布局（命令 id + kind 徽标），`browser.win32.attachWindow` 这类长 id 把条目撑超宽，flex 未设 `min-width: 0` |
| 画布节点、属性面板全英文（`desktop.win32.attachWindow`、`automationId`、`timeoutMs`） | 直接展示 manifest 的英文 id 与 schema 键 |
| if 条件操作符是 `eq/gt/lte` 缩写 | 直接展示 Condition op 枚举值 |
| `action / transform / lifecycle / effect / capability` 等术语无解释 | 没有任何中文化层与术语表 |

## 2. 设计原则

1. **中文优先，英文兜底**：所有面向使用者的文本给中文显示名；英文 id/键名保留为小字副标（定位问题、对照文档时有用）。
2. **不动协议**：命令 manifest（ADR 0002 契约）不加中文字段；中文化是**编辑器本地映射层**，与契约解耦。
3. **零构建边界不破**（ADR 0008）：无 npm/框架/CDN；图标不用 FontAwesome，改用少量内联 SVG。
4. **参考隔壁、不抄实现**：借鉴布局与配色体系（分类图标、卡片形态、悬浮操作条），代码全新。
5. **契约同步有门禁**：命令目录是单一事实来源，i18n 映射层用合同测试锁同步（见 §4.3），不允许静默漂移。

## 3. 视觉系统

### 3.1 布局（三栏 + 底栏，与隔壁一致）

```
┌──────────────────────────────────────────────────┐
│ header：品牌 · 文件名 · 保存/编译 · 撤销/重做/批量 │ 48px
├──────────┬─────────────────────────┬─────────────┤
│ 命令面板  │ 画布（树形，当前实现）    │ 属性面板      │ 280 / 自适应 / 320
│ （分类图标）│                        │（中文字段名）  │
├──────────┴─────────────────────────┴─────────────┤
│ footer：编译结果                                  │ 自适应
└──────────────────────────────────────────────────┘
```

### 3.2 配色 token（参考隔壁命名，浅色主题）

```css
--bg: #f6f8fa;        /* 页面底 */
--surface: #ffffff;   /* 卡片/面板 */
--surface-2: #f0f3f6; /* 次级面 */
--border: #d8dee4;
--accent: #0969da;    /* 主色 */
--accent-soft: #ddf0ff;
--ok: #1a7f37;  --bad: #cf222e;  --warn: #bf8700;
--muted: #656d76;  --faint: #8c959f;
```

命令分类色（与画布深度色带复用同色系）：
- 浏览器 = accent 蓝 / 数据 = ok 绿 / 桌面 UIA = 紫 / 桌面 Win32 = 青 / 控制流 = 橙

### 3.3 节点卡片（画布行重构）

```
┌─ ⠿  1  ┌──┐ 打开网页                    https://...  ↑ ↓ ✕ ─┐
│        │蓝│  ← 类型图标块（accent-soft 底 + SVG 图标）  摘要等宽字体
└─────────────────────────────────────────────────────────────┘
```

- 左 3px 类型色带（action=accent、query=ok、transform=紫、lifecycle=橙、控制流=分类色）
- 类型图标块 20×20 圆角，内嵌 SVG（浏览器/文档/桌面/分支/循环/盾牌/返回）
- 中文显示名为主标题，英文命令 id 为小字副标（`--faint`）
- 多选悬浮操作条（画布底部居中悬浮，参考隔壁：N 个已选 · 上移 · 下移 · 复制 · 删除）
- 拖拽插入线保持 2px accent 色；空分支落点虚线卡
- 分支标签中文：then→「满足时」、else→「否则」、catch→「出错时」、children（try）→「尝试执行」

### 3.4 命令面板条目（修复滚动条）

改为**两行卡片**：

```
┌────────────────────────────┐
│ [图标] 打开网页        query │ ← 中文名 + kind 中文徽标
│        browser.navigate    │ ← 英文 id，小字等宽
└────────────────────────────┘
```

- `li { min-width: 0 }`，英文 id 行 `overflow: hidden; text-overflow: ellipsis`——**根除横向滚动条**
- 分类组头带 SVG 图标；过滤框不变

## 4. 中文化与术语体系

### 4.1 实现位置

新增 `static/i18n.js`（加入 server.py 静态 allowlist），三张映射表 + 一个术语表，全部以命令 id / 枚举值为主键，**未映射项回退英文原文**（保证新命令无需改前端也能用）：

```js
// static/i18n.js
window.RPA_I18N = {
  commands: {
    "browser.launch": "启动浏览器",
    "browser.navigate": "打开网页",
    "browser.click": "点击元素",
    "browser.input": "输入文本",
    "browser.waitFor": "等待元素出现",
    "browser.getText": "读取元素文本",
    "browser.queryAll": "抓取列表文本",
    "browser.close": "关闭浏览器",
    "data.writeJson": "写入 JSON 文件",
    "data.writeText": "写入文本文件",
    "data.limit": "截取前 N 条",
    "data.format": "格式化文本",
    "desktop.attachWindow": "附着窗口（UIA）",
    "desktop.findElement": "查找控件（UIA）",
    "desktop.click": "点击控件（UIA）",
    "desktop.input": "控件输入（UIA）",
    "desktop.getText": "读取控件文本（UIA）",
    "desktop.closeSession": "关闭会话（UIA）",
    "desktop.win32.attachWindow": "附着窗口（Win32）",
    "desktop.win32.findElement": "查找控件（Win32）",
    "desktop.win32.click": "点击控件（Win32）",
    "desktop.win32.input": "控件输入（Win32）",
    "desktop.win32.getText": "读取控件文本（Win32）",
    "desktop.win32.closeSession": "关闭会话（Win32）",
    "desktop.win32.hotkey": "组合按键（Win32）",
    "desktop.win32.menuSelect": "菜单选择（Win32）"
  },
  kinds:   { action: "动作", query: "查询", transform: "变换", lifecycle: "生命周期" },
  effects: { "pure": "无副作用", "read": "只读", "idempotent-write": "幂等写入",
             "unsafe-write": "不可重试写入", "session": "会话" },
  ops:     { eq: "等于", ne: "不等于", gt: "大于", gte: "大于等于",
             lt: "小于", lte: "小于等于", contains: "包含", truthy: "为真（非空）" },
  flow:    { sequence: "顺序执行", if: "条件分支", forEach: "循环", try: "异常捕获", return: "返回结果" },
  glossary: {
    "action": "动作：对外部世界执行一次操作（点击、写入等）",
    "query": "查询：只读获取数据，不改变任何东西",
    "transform": "变换：纯数据加工（截取、格式化），不触碰外部系统",
    "lifecycle": "生命周期：创建或关闭一个会话资源（浏览器、桌面窗口）",
    "manifest": "命令契约：每条命令的说明书（输入输出、副作用、能否重试）",
    "catalog": "命令目录：一次运行时全部命令契约的不可变快照",
    "executor": "执行器：真正干活的组件（浏览器/桌面/子进程）",
    "session": "会话：一次浏览器或窗口附着，命令间靠 sessionId 引用",
    "capability": "能力授权：命令声明需要的权限（如 desktop.control），编译时校验",
    "effect": "副作用：命令对外部世界的实际影响（只读/幂等写/危险写）",
    "checkpoint": "检查点：每个步骤完成后的进度快照，崩溃/暂停后可从这里恢复"
  }
};
```

### 4.2 应用点

| 位置 | 处理 |
|---|---|
| 命令面板条目 | 中文名主标题 + 英文 id 副标 + kind 中文徽标 |
| 画布节点 | 中文名 + 英文副标 + 类型图标；分支标签中文化 |
| 属性面板 | 字段 label 中文映射（`timeoutMs`→超时(毫秒)、`sessionId`→会话 ID、`selector`→选择器、`locator`→定位器、`text`→文本…）；schema 键名保留英文小字 |
| if 条件表单 | op 下拉显示中文（等于/大于/包含…） |
| 页脚 / 状态栏 | 术语速查：把 kind/effect/session 等做成 `title` 悬浮解释 + 页脚「术语表」折叠区 |

### 4.3 契约同步保障（防漂移机制）

命令目录变更（新增/删除/改名/改 kind）时，前端展示层靠两道机制保证不失步：

**硬门禁（合同测试，M12 切片 0 落地）**：`tests/contract/test_editor_i18n.py` 加载真实 catalog 并断言——

```python
def test_i18n_mapping_covers_entire_catalog():
    catalog = load_catalog(ROOT / "commands")
    mapping = i18n_commands()  # 解析 static/i18n.js 的映射表
    assert set(mapping) == set(catalog)          # 新增命令没配中文名 → 门禁红
    assert set(mapping) - set(catalog) == set()  # 删除命令残留映射 → 门禁红

def test_i18n_kind_and_op_keys_match_model_enums():
    assert set(i18n_kinds()) == {k.value for k in CommandKind}
    assert set(i18n_ops()) == {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "truthy"}
```

即：任何人改命令目录而不更新 `i18n.js`，`check_all.py` 立刻红。这与 `test_protocols.py` 里 catalog 数量触发器（26 条硬编码断言）是同一文化。

**软回退（展示层兜底）**：映射缺失时前端显示英文原文——功能不破，只是退化成英文。门禁保证它“响”，回退保证它“不炸”。

**属性字段名**（`sessionId`/`timeoutMs`/`selector` 等）走同一原则：公共键映射表 + 合同测试断言每个 manifest 的 `required` 键都有中文标签；非 required 的陌生键允许回退英文（技术参数）。

## 5. 交互修正清单

1. 命令面板横向滚动条根除（两行卡片 + min-width:0）
2. 多选操作条从 header 移到画布底部悬浮（选中 ≥2 时出现；header 保留撤销/重做）
3. 属性面板字段按 schema `description` 或映射表给中文提示（title 悬浮）
4. 空画布引导文案中文化并加示例链接
5. 编译错误行保留英文错误码，附中文提示前缀

## 6. 图标方案（零构建）

10 个内联 SVG（~1KB 总计）：browser、edit(数据写入)、layers(数据变换)、desktop、window、branch(if)、loop(forEach)、shield(try)、return、grip。存放 `static/icons.js`（allowlist 同步加），`icon(name)` 返回 SVG 字符串。

## 7. 实施切片（建议 M12）

| 切片 | 内容 | 验收 |
|---|---|---|
| 0 | `i18n.js` + `icons.js` + server allowlist 扩展 + 合同测试（新静态文件 200/类型正确） | gate |
| 1 | 命令面板两行卡片 + 分类图标 + 滚动条根除 | E2E：无横向滚动（`scrollWidth <= clientWidth`）、条目显示中文名 |
| 2 | 画布节点卡片重构（图标块 + 中文标题 + 英文副标 + 分支标签中文） | E2E：节点渲染中文、拖拽/多选回归全绿 |
| 3 | 属性面板中文化 + if 操作符中文下拉 + 术语表（页脚折叠区） | E2E：表单 label 中文、保存读回不变 |
| 4 | 多选悬浮操作条（替代 header 批量按钮） | E2E：Ctrl/Shift 选择 + 悬浮条操作 |

全程：零构建边界不破（无 npm）、既有 E2E 向后兼容、full gate。

## 8. 范围外

- 深色主题（token 已预留，后续可切）
- 面板宽度拖拽调节（隔壁有，非必要不引入）
- 命令级别的详细使用文档面板（接 docs/api-usage.md 即可）
- 既有 workflow JSON 的任何字段变更（中文化纯展示层，文件格式零改动）
