# M31 GUI 跨平台观感诊断（macOS vs Windows）

状态：`planned`

**本里程碑只诊断、不改代码**（诊断先行）。

关联：ADR 0014（方案 E 原生 PySide6，§8.3 皮肤生态、§8.1 控件可行性）、ADR 0016（GUI 为唯一主力形态）、
M22（跨平台传输层 —— 非 GUI 部分已做过认真的平台分派，是本次的对照基准）、
`docs/desktop_backends.md`（非 GUI 的跨平台口径）

## 为什么有这一里程碑

维护者在 macOS 上使用 GUI 后反馈：**控件样式、字体大小、控件的显示/隐藏行为与 Windows 存在不一致**。

这条反馈有价值，但当前形态是**主观感受**，不能直接拿来立项。原因是：它可能指向三种成本量级完全不同的
根因，而三者的处置方式互相排斥：

| 假设 | 根因层 | 处置 | 量级 |
|---|---|---|---|
| H1 单位混用 | 本项目代码 | 统一单位 + 平台分派 | 小（一个切片） |
| H2 Qt 平台抽象层太薄 | 框架 | 换宿主（pywebview / Tauri 套壳） | 中 |
| H3 前端自绘成本 | 本项目架构 | 重写 Web 前端（Tauri 全量） | 大 |

**所以本里程碑只做一件事：把感受变成逐条可归因的差异清单。** 在没有这份清单之前，
任何「迁 Tauri」的评估都缺少前提材料（会重演 ADR 0016 刚定完就推翻的循环）。

## 已确认的静态线索（诊断前就能从代码读出来的）

**重要**：以下三条是**读代码得到的假设，不是实测结论**。诊断的任务就是验证或推翻它们。

### 线索 1：字号单位混用（怀疑是「字体大小不一致」的主因）

`gui/app.py:143 apply_theme()`：

```python
for family in ("Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC",
               "Source Han Sans SC", "SimHei"):
    if QFontDatabase.hasFamily(family):
        font = QFont(family, 9)      # ← 9 是 point size
        app.setFont(font)
        break
```

而 QSS 里散落的是 **px**：

```
app.py:1831   font-size: 13px
app.py:1837   font-size: 12px
home.py:97    font-size: 14px
home.py:161   font-size: 14px
```

同一个界面里 **pt（`app.setFont`）与 px（QSS）并存**，二者换算依赖 DPI/逻辑分辨率，
而 Windows 与 macOS 的基准不同。**假设：这条能解释大部分「字体大小不一致」。**

顺带一个独立缺陷（与平台无关，但会放大上面这条）：候选字体列表是 **Windows 优先**
（`Microsoft YaHei` 排第一）。macOS 上该族不存在，会命中 `PingFang SC`——结果**两台机器用了
不同字体族**，即使字号相同，**实际字面高度与行宽也不同**（中文字体的拉丁字母与数字宽度差异明显）。
这会让「表格列宽、卡片参数摘要」在 macOS 上错位。

### 线索 2：画布硬编码像素 + pt 字号混算（怀疑是「控件显示行为不一致」的主因）

`gui/canvas.py`：

```
65   _ROW_HEIGHT = 46            # 行高：硬编码像素
73   _DELETE_BTN_W = 36          # 删除按钮宽：硬编码像素
85   _GUTTER_ERROR_X = 46        # 错误徽标圆心 x：硬编码像素
644  return QSize(option.rect.width(), _ROW_HEIGHT)
814  font.setPointSizeF(max(7.0, option.font.pointSizeF() - 0.5))   # 字号：pt 相对偏移
953  mono.setPointSizeF(max(7.5, option.font.pointSizeF() - 1.5))
```

**行高是固定像素，字号是 pt（还带相对偏移）**，两者比例在两个平台上不同 →
卡片内容在某个平台上会显得挤或空，参数摘要可能截断或错位。

`_GUTTER_ERROR_X = 46` 与 `_ROW_HEIGHT = 46` **数值相同但语义无关**（一个是横坐标、一个是行高），
属巧合耦合——改一个容易误伤另一个，本身是隐患。

### 线索 3：零平台分派（结构性欠账）

全仓搜索 `src/rpa_core/gui/*.py` 的 `sys.platform` / `darwin` / `Q_OS_MAC` / `platform.system`
→ **命中 0 次**。

对照：`cli.py`、`executors/`、`capture/` 都有明确分派；`local_transport.py`（M22）甚至专门处理了
Windows 命名管道 vs POSIX socket，注释里还记了 macOS `AF_UNIX` 104 字节路径上限的坑。

**即：GUI 是目前唯一没做跨平台分派的模块。** 这不是技术选择，是欠账。

### 线索 4：窗口级行为依赖 Windows 语义（怀疑是「显示/隐藏行为不一致」的主因）

- `run_float.py` 用 `WindowStaysOnTop` + `setWindowFlags` —— macOS 的窗口层级由 WindowServer 管，
  `WindowStaysOnTopHint` **不保证置顶**（系统可能忽略或降级）。
- `app.py` / `home.py` 用 `hide()` / `showMinimized()` —— macOS 上「隐藏」（`Cmd+H`）与
  「最小化」（黄色按钮）是两种不同语义，Qt 的映射与 Windows 不同。捕获时「编辑器自动让位」
  这条链路（ADR 0014 §1 诉求 2）在 macOS 上是否成立，**必须真机验证**。
- `app.py` 有 `setStyleSheet` 局部硬编码颜色（`#1a7f37` / `#cf222e` / `#9a6700` / `#64707d`），
  这些是**照 QDarkStyle LightPalette 手配的**；若 macOS 上 Qt 落到 Fusion 兜底（QDarkStyle 缺失或
  不生效），这些硬编码色会与全局皮肤打架。

## 诊断清单（在 macOS 上执行，逐条记录）

### 准备

```bash
uv sync --all-groups --extra gui
uv run python -c "import sys, PySide6; from PySide6 import __version__; print(sys.platform, __version__)"
uv run python -c "
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFontMetrics
import sys
app = QApplication(sys.argv)
for f in ('Microsoft YaHei','PingFang SC','Noto Sans CJK SC','Source Han Sans SC','SimHei'):
    print(f, QFontDatabase.hasFamily(f))
fm = QFontMetrics(app.font())
print('app.font():', app.font().family(), app.font().pointSize(), 'pt')
print('height(px):', fm.height(), 'horizontalAdvance(中):', fm.horizontalAdvance('中'))
print('devicePixelRatio:', app.devicePixelRatio())
"
```

把输出记进下表 —— **Windows 侧也要跑同一段以便对照**。

### 表 A：基础环境差异（两端各填一行）

| 项 | Windows | macOS |
|---|---|---|
| `sys.platform` | | |
| PySide6 版本 | | |
| `app.font().family()` | | |
| `app.font().pointSize()` | | |
| `QFontMetrics.height()` (px) | | |
| `horizontalAdvance('中')` (px) | | |
| `devicePixelRatio` | | |
| QDarkStyle 是否加载成功 | | |
| 实际生效的 QStyle 名（`app.style().objectName()`） | | |

### 表 B：逐控件观感差异（每行填「一致 / 差异」，差异写具体表现）

启动 `uv run python -m rpa_core gui`，按下面清单逐项看：

| # | 检查项 | 关注点（对应线索） |
|---|---|---|
| B1 | 左侧指令树：字号、行高、缩进 | 线索 1（pt/px 混用）、1（字体族不同） |
| B2 | 左侧指令树：搜索框高度与文字垂直居中 | 线索 1 |
| B3 | 中部画布卡片：**行高是否装得下文字**（有无截断/贴边） | 线索 2（46px 固定行高） |
| B4 | 中部画布卡片：参数摘要是否被截断 / `+N` 是否出现得比 Win 早 | 线索 1+2 |
| B5 | 中部画布卡片：左侧 4px 深度色线粗细是否一致 | 线索 2 |
| B6 | 中部画布卡片：拖柄、序号、命名空间徽标是否对齐 | 线索 2 |
| B7 | 右栏参数表单：label 字号、输入框高度、tooltip 字号 | 线索 1 |
| B8 | 右栏参数表单：必填 `*` 与文字基线是否齐 | 线索 1 |
| B9 | 工具栏按钮：图标与文字间距、按钮高度 | 线索 1 |
| B10 | 状态栏：字号与边距 | 线索 1 |
| B11 | HOME 页（`rpa-core` 无参数启动）：标题 14px 是否明显偏大/偏小 | 线索 1 |
| B12 | 元素库 / 数据表面板：表格行高、列宽 | 线索 2 |
| B13 | 运行错误面板：`font-size: 13px` 与 `12px` 的层次感 | 线索 1 |
| B14 | 深/浅色配置是否有色差（与 QDarkStyle LightPalette 基准比） | 线索 4 |
| B15 | 中文是否有方框/缺字（尤其 mono 场景） | 线索 1（mono 回退） |

### 表 C：窗口行为差异（对应线索 4）

| # | 检查项 | 预期差异 |
|---|---|---|
| C1 | 悬浮运行窗（`run_float`）能否置顶 | macOS 可能忽略 `WindowStaysOnTopHint` |
| C2 | 悬浮窗在切换应用后是否仍在最前 | 同上 |
| C3 | 捕获时编辑器「自动隐藏/让位」是否按预期发生 | 窗口时序链路 |
| C4 | 捕获结束后编辑器是否恢复正常层级 | 同上 |
| C5 | 最小化 / 隐藏（`Cmd+H`）行为与恢复 | `hide()` vs `showMinimized()` 语义 |
| C6 | 无边框/自绘标题栏（若有）在 macOS 上的按钮位置 | macOS 交通灯位置固定，自绘易冲突 |
| C7 | 弹窗（`QFileDialog` / 元素编辑对话框）是否为原生样式 | macOS 上 Qt 可能用原生对话框 |
| C8 | 置顶窗被全屏应用覆盖时的行为 | 系统级约束 |

### 表 D：明确「不改代码能不能修」的判定

对每一条差异打标：

- **[U]** 单位/尺寸问题 → 改代码可修，与平台无关的欠账
- **[F]** 字体族问题 → 加平台分派可修
- **[W]** 窗口行为问题 → **Qt 平台约束，换 Tauri 也修不掉**（约束来自 macOS 而非 Qt）
- **[Q]** QDarkStyle/Fusion 皮肤问题 → 换皮肤或加大 QSS 覆盖

**这张表的 [U]+[F]+[Q] 与 [W] 的比例，就是「要不要迁 Tauri」的判据**：
若绝大多数是 [U]/[F]/[Q]，则 Tauri 收益极低（这些换个壳照样要修，甚至换成 CSS 单位问题复发）；
若 [W] 占主导，Tauri 也帮不上（约束在系统层）。

## 诊断产出（本里程碑的交付物）

1. 表 A–D 填写完整（两端数据）；
2. 一段结论：按 [U]/[F]/[Q]/[W] 分类统计，给出「修代码 vs 换宿主」的量化建议；
3. 若结论是「修代码」，把结论转成下一个里程碑的任务单（可与 M28/M30 同样形态）；
4. 若结论是「换宿主」，把表 A–D 作为**新前端的验收基线**（逐条对齐，而不是重新主观判断）。

## 明确不做什么

- **本里程碑不写生产代码**（诊断脚本可临时放在 `.harness/demo/`，不进 `check_all.py`，与 ADR 0014 §8 的
  demo 惯例一致）；
- 不在诊断阶段评估 Tauri 集成细节（sidecar/IPC/打包）——那是「若结论为换宿主」之后的独立里程碑；
- 不做主观的「哪个平台更好看」评判，只记录**可复核的差异**。

## 风险 / 注意

- **诊断必须两端同版本**：`pyproject.toml` 的 PySide6 是 `>=6.7,<7`，若两端装到不同 minor，
  差异可能来自 Qt 版本而非平台。先锁版本再比（必要时临时 pin 同一版本）。
- **先记原始输出，再下判断**：表 A 的脚本输出要原样保留（含 `devicePixelRatio`）——
  Retina 的 `devicePixelRatio=2` 会让「px 尺寸」的物理观感与 Windows 完全不同，
  这一条本身可能解释掉相当一部分「看起来不一致」。
- 不要用截图比对代替数据记录：截图受缩放/DPI 影响，无法区分「代码算错」与「显示缩放不同」。
