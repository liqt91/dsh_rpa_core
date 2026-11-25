# M107 — 捕获框界面 A 档改造：布局自适应 + 统一对齐 + 状态条

Status: `done`

2026-11-25 维护者反馈「原生框速度挺快，就是界面土土的」；先给三档取舍（A 布局与样式 /
B 加自绘皮肤 / C 换 UI 栈），拍板 **只做 A**。B/C 不在本单范围，换栈要把 IME、Tab/Enter/Esc、
F10 热键、截图自绘整条重接，与「原生快」的诉求冲突。

## 取证：六个「土」的具体来源

1. 层级只有 4 行，窗口按 10 行固定高度排（`_TREE_BASE_H=552`），近半屏空白；
2. 截图区一开始就占 280px，空态也是一大块灰白；
3. 属性区有/无模式下拉时值框分别从 x=480 / x=396 起，同列参差；
4. 层级行文字 `<div class="search-inp…` 截断且没有全文提示；
5. 校验结果「找到 1 个」与提示灰字同权重，看不出成功；
6. 「校验元素」在最左、保存/取消在最右，中间空一大段。

## 落地

只改 `capture_kit/edit_dialog.py` 的布局与绘制分支，**不动编辑模型、消息流、校验逻辑**。

- **内容自适应**：新增纯函数 `_visible_rows` / `_content_shift` / `_base_height_for`。
  窗口高度按 `min(实际层数, 满屏行数)` 算，超出部分仍靠既有滚动条；
  4 层网页框客户区 552 → 360，桌面 3 层 614 → 482。
- **截图区折叠**：建窗时 `_shot_h=0`；校验真拿到 `shot` 才由 `_ensure_shot_view`
  建 `ShotView` 并把外框加高「截图高度 + 间距」，`page.call` 签名不变。
- **统一对齐**：属性值框一律 x=480；桌面标签列 96 → 150px（容纳「（该层未参与定位）」），
  下拉/值框顺延；底部三个按钮聚拢到右下。
- **去灰底**：`WM_CTLCOLORSTATIC` 对所有 STATIC 统一透明底 + 白刷兜底。
- **状态条**：结果条写 `●  <message>` 并整行染色。★ 曾试父窗口 `WM_PAINT` 自绘圆点，
  WM_PRINT 下 HDC 原点与客户区不一致会错位 28px（真机取证），改回交给 STATIC 自己画。
- **悬停全文**：层级行/桌面节点行挂 `tooltips_class32`（`TTF_SUBCLASS`），原控件仍按
  固定宽度省略。★ `ctypes.WinDLL` 每次调用返回新包装对象，`argtypes` 必须在本次调用里设。

## 验证

- 真机截图（不进门禁）：`.harness/spike/probe_edit_check_ui.py` 产空态 796×399 /
  校验后 796×697（截图区展开）；`probe_edit_layouts.py` 产桌面 / 简式两态，均无溢出。
- 新增几何不变量 2 条；负向探针 `.harness/spike/probe_edit_skin_negative.py`：
  基线 11 passed → 注入行高 32→30 得 2 failed/9 passed 无 error → 关掉新判据回 9 passed
  （元验证）→ 两文件逐字节 md5 还原。
- 相关单测 + `check_element_pick_ui.mjs`（44/44）绿；全门禁
  **FULL GATE PASSED + rc=0，1189 passed / 20 skipped / 2 xfailed**，ruff 全绿。

## 已知未做

B 档自绘皮肤（owner-draw 扁平按钮、分区卡片、选中行高亮、截图空态插画）与 C 档换栈
均未做；原生按钮面仍是系统灰底，但已无立体边框（`BS_FLAT`）。

## 后续修复（2026-11-25，用户真机截图反馈）

维护者发来截图说「显示上有点问题」。逐项取证后修 **2 处真 bug**、排除 2 处环境伪影。

### 真 bug 1：行文字硬切，看着像被灰块压掉一截

BUTTON 的文字由系统绘制、**不认** `DT_END_ELLIPSIS`，宽度不够就硬切在边框上。
实测 10 层样例里 `<div class="main-content with-sidebar">` 要 300px、
`textarea-container…` 要 413px，而行控件只有 252px。

- 新增模块级纯函数 `_clip_to_width(text, width, measure)`：二分出「补 `…` 后仍放得下」
  的最长前缀；`measure` 由调用方给（真机 `_win32.text_width`，单测传 `len`）。
- 新增 `_win32.text_width(hdc, hfont, text)`（走 gdi32 `GetTextExtentPoint32W`，
  `win32gui` 没有这个 API）。
- 树行与桌面节点行建控件前都过 `_fit_text`；全文仍由既有 tooltip 给。
- 常量 `_LEVEL_ROW_W` / `_NODE_ROW_W` / `_ROW_TEXT_PAD` / `_CHECK_TEXT_PAD` 收口，
  不再写裸数字。

### 真 bug 2：滚动条吃掉客户区 17px，右侧控件被窗口边缘裁掉

`AdjustWindowRectEx` 的文档明文：**不**把 `WS_VSCROLL`/`WS_HSCROLL` 算进客户区。
11 层触发滚动条时客户区实测 **763** 而布局按 `_TREE_W=780` 摆 ⇒ 属性值框、
「取消」按钮被右边缘裁掉（探针 `client=763x552` 取证）。10 层（无滚动条）正常，
所以只在长链路上暴露。

在 `_win32.adjust_window_size` 里按 `SM_CXVSCROLL` / `SM_CYHSCROLL` 补回，
修复后 `client=780x552`。

### 排除的伪影（不是代码问题，记下来免得下次再追）

- 截图里「资产名称 / 组合选择器」像缺了中间的字：用户图是 95.5% 缩放后的裁切，
  「产 / 合」这类细笔画字在缩小时丢笔画；本机 15px 雅黑原图完整。像素扫描确认过
  那两处没有覆盖控件。
- 顶部「捕获到元素」只剩半截：那是窗口标题栏，用户截图框选的上边界落在标题栏中间。
- 抓图里的浅绿横带只是这台机器的 accent 色（名称框全选高亮），不是界面底色。

### 验证

- 诊断探针 `.harness/spike/probe_dialog_geom.py`：dump 每个子控件的 rect + 文字实测
  像素宽，直接看出「文字比控件宽」；按层数产图（10 层无滚动条 / 11 层有滚动条）。
- 四张真机截图核对：10 层、11 层、桌面 3 层、简式，右侧控件均完整、行文字均以 `…` 收尾。
- 新增判据 2 条：`test_clip_adds_ellipsis_only_when_too_wide`、
  `test_scrollbar_width_is_added_back_to_frame`。
- 负向探针 `.harness/spike/probe_edit_textclip_negative.py`：基线 13 passed →
  注入「不再补省略号」1 failed、注入「宽度 += 0」1 failed（各自打中的测试名已校验）→
  两个注入 + 关掉新判据回 11 passed（元验证）→ 三文件逐字节 md5 还原。
- 全门禁 **FULL GATE PASSED + rc=0，1191 passed / 20 skipped / 2 xfailed**，ruff 全绿。
- 顺手修了上一轮提交里 `probe_edit_check_ui.py` 被脚本替换写坏的 `\n` → `/n`
  （语法错误，探针本身不进门禁所以没被拦住）。
