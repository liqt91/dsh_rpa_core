"""GUI 语义色 token（M49 P0-1）。

全仓 GUI **只允许本模块出现十六进制颜色字面量**：其余 ``gui/*.py`` 一律引用这里的
语义常量，由 ``tests/contract/test_gui_theme_tokens.py`` 用 AST 扫字符串常量断根
（注释与文档串不在扫描面内，但也不要往里塞色值）。

存在的理由：全局皮肤是 QDarkStyle LightPalette 的整表 QSS，而各文件又散着内联
``setStyleSheet`` 硬编码色，两套体系并存时「换肤/暗色模式」无处下手。收敛成 token 后
唯一开关点就是本模块——代价为零，收益是后续所有样式工作的前置。

命名按**语义**而不是色相：同一个绿在「成功」「在线」「通过」三处都该是 SUCCESS。
色值取自 GitHub Light 色板（与 LightPalette 协调）；改值前先读用途注释。
"""

# ---- 语义状态色 -------------------------------------------------------------
SUCCESS = "#1a7f37"  # 成功 / 在线 / 命中唯一 / 校验通过
DANGER = "#cf222e"  # 失败 / 离线 / 错误 / 命中不唯一
WARNING = "#9a6700"  # 警告 / 已取消 / 跳过 / 待补充
CAUTION = "#bf8700"  # 暂停（比 WARNING 更弱的一档提示）
INFO = "#0969da"  # 进行中 / 链接 / 选中态描边
ACCENT = "#8250df"  # 桌面通道 / 恢复中（紫）
ORANGE = "#bc4c00"  # 画布深度色第 5 档
TEAL = "#0598bc"  # 画布深度色第 6 档
CYAN = "#0a7ea4"  # 流程控制类指令（catalog 之外的内置指令）

# ---- 文本 -------------------------------------------------------------------
TEXT = "#19232d"  # 主文本
TEXT_HEADING = "#4d5564"  # 表单分组标题
TEXT_SECONDARY = "#64707d"  # 次级说明 / 参数摘要
TEXT_MUTED = "#57606a"  # 弱化文本
TEXT_FAINT = "#8b929e"  # 计数 / 极弱标注
NEUTRAL = "#8c959f"  # 未知状态的兜底色
FALLBACK = "#6e7781"  # 无法归类（catalog 外的未知指令族）

# ---- 边框与分隔 -------------------------------------------------------------
BORDER = "#c0c4c8"
BORDER_HOVER = "#8b959e"
BORDER_SELECTED = INFO
BORDER_SOFT = "#d0d7de"  # 虚线 / 浅分隔
GRIP = "#9da9b5"  # 拖拽把手

# ---- 面 ---------------------------------------------------------------------
SURFACE = "#ffffff"
SURFACE_HOVER = "#f3f6f9"
SURFACE_SELECTED = "#daedff"
SURFACE_SUNKEN = "#f6f8fa"  # 画布 / 树底色
SURFACE_GROUP = "#eef1f4"  # 分组条底色
SURFACE_GROUP_HOVER = "#eaeef2"
SURFACE_HIGHLIGHT = "#eef5ff"  # 已覆盖 / 已绑定的输入框底色

# ---- 字体 -------------------------------------------------------------------
FONT_MONO = "Consolas, monospace"

# ---- 自绘叠加层（rgba 元组，供 QColor(...) 用，非 QSS 字符串）-----------------
OVERLAY_BORDER = (25, 35, 45, 70)  # 卡片 hover 描边
OVERLAY_SHADOW = (25, 35, 45, 28)  # 卡片投影

# ---- 派生集合 ---------------------------------------------------------------
# 画布左侧深度色线：按树深度取模着色（原 canvas._DEPTH_COLORS）
DEPTH_COLORS = (INFO, SUCCESS, WARNING, ACCENT, ORANGE, TEAL)
