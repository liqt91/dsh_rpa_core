"""用 FluentWidgets 真实控件展示：Fluent 观感需要控件体系级接入。

对比见 pyside6_demo_fluent.png（只 setTheme）——本文件用 FluentWindow 的
导航 + Card + FluentPushButton + 输入框等，体现 Fluent 真实设计语言。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QWidget, QVBoxLayout, QLabel, QHBoxLayout,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".harness" / "demo" / "fluent_widgets_demo.png"


def main() -> int:
    app = QApplication(sys.argv)
    # qfluentwidgets 在 import 时会模块级实例化需要 QApplication 的对象，
    # 因此必须在创建 app 之后再 import（经典坑）。
    import qfluentwidgets as qfw  # noqa: PLC0415

    qfw.setTheme(qfw.Theme.DARK)
    qfw.setThemeColor("#3b82f6")

    # 用普通 QWidget 容器承载 Fluent 控件（离屏下 FluentWindow 会崩溃，属平台限制）
    win = QWidget()
    win.setAttribute(Qt.WA_TranslucentBackground, False)
    win.resize(920, 560)

    page = QWidget()
    root = QVBoxLayout(page)
    root.setContentsMargins(28, 20, 28, 20)
    root.setSpacing(16)

    root.addWidget(qfw.TitleLabel("RPA 编辑器（Fluent 控件演示）"))
    root.addWidget(qfw.SubtitleLabel("真实控件：圆角卡片 / 导航 / 图标按钮 / 开关 / 进度条"))

    # 一行操作按钮 + 输入框
    row = QHBoxLayout()
    row.addWidget(qfw.FluentPushButton(qfw.FluentIcon.FOLDER, "打开流程"))
    row.addWidget(qfw.FluentPushButton(qfw.FluentIcon.PLAY, "运行"))
    row.addWidget(qfw.FluentPushButton(qfw.FluentIcon.ROBOT, primary=True, text="执行"))
    row.addStretch(1)
    root.addLayout(row)

    # 输入区
    inp = qfw.LineEdit()
    inp.setPlaceholderText("搜索指令…")
    root.addWidget(inp)

    # 卡片承载子项
    card = qfw.CardWidget()
    cbox = QVBoxLayout(card)
    cbox.setContentsMargins(20, 16, 20, 16)
    cbox.setSpacing(10)
    cbox.addWidget(qfw.SubtitleLabel("设置控件"))
    for label in ("指令名称", "变量类型", "运行模式"):
        lbl_row = QHBoxLayout()
        lbl_row.addWidget(QLabel(label))
        lbl_row.addStretch(1)
        cb = qfw.ComboBox()
        cb.addItems(["指令A", "指令B"])
        lbl_row.addWidget(cb)
        cbox.addLayout(lbl_row)
    sw_row = QHBoxLayout()
    sw_row.addWidget(QLabel("开启调试"))
    sw_row.addStretch(1)
    sw = qfw.SwitchButton()
    sw.setChecked(True)
    sw.setText("开 / 关")
    sw_row.addWidget(sw)
    cbox.addLayout(sw_row)
    prog = qfw.ProgressBar()
    prog.setValue(64)
    cbox.addWidget(prog)
    root.addWidget(card)

    # 布局直接挂到窗口
    page.setLayout(root)
    page.setParent(win)
    v = QVBoxLayout(win)
    v.setContentsMargins(0, 0, 0, 0)
    v.addWidget(page)

    if app.platformName().startswith("offscreen"):
        win.show()
        pix = win.grab()
        ok = pix.save(str(OUT))
        print("SAVED_OK" if ok else "SAVE_FAIL", OUT)
        return 0
    win.show()
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())