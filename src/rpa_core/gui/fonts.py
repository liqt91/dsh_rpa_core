"""GUI 字号（M49 P3，即 M31 GUI 跨平台审计里的**字号**切片）。

口径统一为**像素**。改之前的情况是两套口径混用：

- 基准字体走 pt：``QFont(family, 9)``，画布/命令面板的派生字号也走 pt
  （``option.font.pointSizeF() - 0.5`` 之类）；
- QSS 里写死 px：``font-size: 14px``（面板标题、错误码、splash 标题）。

两套口径只在 96dpi 下恰好对齐（9pt ≈ 12px），换个 DPI 或缩放比例就对不上；更要命的是
**基准字号一旦被设成像素，``pointSizeF()`` 会返回 -1**——派生字号不再是「小一号」，
而是「没有字号」，Qt 会回落到系统默认大小。所以基准与派生必须同口径，且只从这里取。

说明：这里只处理**字号**。M31 记录的其他跨平台问题（无 ``sys.platform`` 分派等）
仍按 M31 单独评估，不在本切片内。
"""

from __future__ import annotations

from PySide6.QtGui import QFont

from rpa_core.gui.theme import FONT_MONO

# 基准字号：与旧基准 9pt（96dpi 下 12px）等价，保证观感不变
BASE_PX = 12
# 次级说明（画布参数摘要、命令面板副标题等）
SUB_PX = 10
# 等宽文本（命令 id、快捷键提示）
MONO_PX = 10
# 面板小节标题
HEADING_PX = 14
# 启动画面标题
TITLE_PX = 15
# 错误码等需要强调的正文
CODE_PX = 13
# 下限：再小就看不清了（派生字号不得低于它）
MIN_PX = 10

# 等宽族名。**不再写第二遍字面量**：QSS 要的是带回退的列表（theme.FONT_MONO =
# "Consolas, monospace"），而 QFont.setFamily 只认单个族名 —— 从同一份事实里取第一个。
MONO_FAMILY = FONT_MONO.split(",")[0].strip()


def base_font(family: str | None = None) -> QFont:
    """基准字体（像素口径）。``family`` 为空时用系统默认族。"""
    font = QFont(family) if family else QFont()
    font.setPixelSize(BASE_PX)
    return font


def scaled(base: QFont, *, delta: int) -> QFont:
    """在基准字体上偏移 ``delta`` 像素（下限 :data:`MIN_PX`）。

    基准字体没有有效像素字号时（例如被 QSS/系统字体覆盖），回落到 :data:`BASE_PX`，
    保证派生字体永远是「有字号」的，而不是像 ``pointSizeF() = -1`` 那样静默失效。
    """
    font = QFont(base)
    size = base.pixelSize()
    if size <= 0:
        size = BASE_PX
    font.setPixelSize(max(MIN_PX, size + delta))
    return font


def mono_font(base: QFont | None = None) -> QFont:
    """等宽字体（命令 id / 代码用），同样走像素口径。"""
    font = QFont(base) if base is not None else QFont()
    font.setFamily(MONO_FAMILY)
    font.setPixelSize(MONO_PX)
    return font
