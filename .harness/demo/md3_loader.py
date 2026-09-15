"""加载 md3_demo.qml（QtQuick.Controls.Material）并离屏渲染截图。

用法：
    <venv>/Scripts/python.exe .harness/demo/md3_loader.py [out.png]
真窗口（去掉 offscreen）会弹窗交互；离屏走软件后端抓图。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QUICK_BACKEND", "software")  # 离屏截图用；真窗口会走 GPU

from PySide6.QtCore import QEventLoop, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
QML = ROOT / ".harness" / "demo" / "md3_demo.qml"
OUT = ROOT / ".harness" / "demo" / "md3_demo.png"


def main() -> int:
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(QML)))
    if not engine.rootObjects():
        print("QML_LOAD_FAIL")
        return 1
    win = engine.rootObjects()[0]  # ApplicationWindow 顶层对象即窗口
    offscreen = app.platformName().startswith("offscreen")
    if not offscreen:
        # 真窗口：保持打开供交互
        win.show()
        return app.exec()
    # 离屏：抓图存证（Qt Quick 依赖真实渲染，离屏可能为 null）
    win.show()
    loop = QEventLoop()
    QTimer.singleShot(400, loop.quit)  # 等布局/动画帧定型
    loop.exec()
    img = win.grabWindow()
    if img.isNull():
        print("GRAB_NULL")
        return 1
    out = sys.argv[1] if len(sys.argv) > 1 else str(OUT)
    ok = img.save(out)
    print("SAVED_OK" if ok else "SAVE_FAIL", out, img.size())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())