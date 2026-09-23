"""探针：把**真实捕获产物**喂进 ``ElementDialog``，实测它到底渲染出什么。

判据是「候选与语义特征被展示出来」。展示文本全部取自 ``descriptor`` 这个自由 dict ——
字段名差一个字符（``accessibleName`` vs ``accessible_name``）就静默什么都不显示，
**读代码看不出来，只有真渲染才有输出**。所以这里跑真入口、打印真文本。

两条腿的数据来源不同，如实标注：

- **桌面腿 = 真机**：编译并拉起靶子（``tests/e2e/desktop_fixture.compile_demo_app``），
  在真实控件上取坐标，走真实入口 ``desktop-capture-agent --point`` 产出描述符。
- **浏览器腿 = 形状取自扩展源码**（``extension/content.js`` 的 ``buildDescriptor`` 与
  ``candidatesFor``，并被 ``tests/contract/test_capture_contract.py`` 的真机往返钉住）。
  真机浏览器腿需要把未打包扩展装进真 Chrome 并接上 native host，本探针不做——
  但这里**不凭想象编字段**，键名逐条对照 ``content.js`` 第 234-259 行。

跑法（Windows；会短暂抢前台）::

    .venv/Scripts/python.exe .harness/spike/probe_element_dialog_display.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from PySide6.QtWidgets import QApplication  # noqa: E402

TITLE = "RPA Core Desktop Demo"
SEP = "=" * 72


def render(descriptor: dict) -> None:
    """走真实对话框渲染并打印全部展示面。"""
    from rpa_core.gui.element_panel import ElementDialog

    dialog = ElementDialog(descriptor, default_name="probe_el")
    print(f"  元素名    : {dialog.name_edit.text()}")
    print(f"  selector  : {dialog.selector_edit.text()}")
    print(f"  命中数    : {dialog.verify_label.text()!r}")
    print("  metadata  :")
    for line in dialog.meta_label.text().splitlines() or ["(空)"]:
        print(f"      {line}")
    candidates = dialog.candidates_label.text()
    print(f"  候选（hidden={dialog.candidates_label.isHidden()}）:")
    for line in candidates.splitlines() or ["(无候选，本节隐藏)"]:
        print(f"      {line}")
    _name, document = dialog.result_document()
    print(f"  写回后 selector: {json.dumps(document['selector'], ensure_ascii=False)}")


def browser_descriptor() -> dict:
    """按 ``content.js buildDescriptor`` 的产出形状构造（键名逐条对照源码）。

    特意混入两种边界：``matchedCount=3``（候选本身不唯一）与**缺 ``matchedCount``**
    （契约允许缺席，见 ``_candidate_errors``）。
    """
    return {
        "kind": "browser",
        "selector": {
            "css": "#sb_form_q",
            "candidates": [
                {"kind": "id", "selector": "#sb_form_q", "matchedCount": 1},
                {"kind": "attribute", "selector": 'input[name="q"]', "matchedCount": 3},
                {"kind": "attribute", "selector": "[data-testid=search]", "matchedCount": 1},
                {"kind": "attribute", "selector": 'input[aria-label="搜索"]'},  # 无 matchedCount
            ],
        },
        "verifyCount": 1,
        "metadata": {
            "tag": "textarea",
            "id": "sb_form_q",
            "classes": ["search", "box"],
            "text": "",
            "rect": {"x": 336, "y": 210, "width": 300, "height": 40},
            "role": "searchbox",
            "accessibleName": "搜索",
            "placeholder": "请输入搜索内容",
            "label": "搜索框",
            # 容器文本上限 200 字（content.js containerTextOf），这里给个长值看截断
            "containerText": "主页 搜索 " + "很长很长的容器文本 " * 20,
            "url": "https://www.bing.com/search?q=rpa&FORM=HDRSC1&extra=" + "x" * 80,
            "title": "Bing",
        },
    }


def desktop_descriptor() -> dict | None:
    """真机产出桌面描述符（靶子 + ``desktop-capture-agent --point``）。"""
    from tests.e2e.desktop_fixture import (
        compile_demo_app,
        force_foreground,
        kill_demo_apps,
        wait_for_window,
    )

    kill_demo_apps()
    tmp = Path(tempfile.mkdtemp(prefix="probe-dlg-"))
    exe = compile_demo_app(tmp)
    proc = subprocess.Popen([str(exe)])
    try:
        hwnd = wait_for_window(TITLE, timeout=20.0)
        force_foreground(TITLE)
        time.sleep(0.4)

        from pywinauto import Application

        app = Application(backend="win32").connect(handle=hwnd)
        win = app.window(handle=hwnd)
        target = None
        for child in win.descendants():
            try:
                if (child.window_text() or "") == "Submit":
                    target = child
                    break
            except Exception:  # noqa: BLE001 - 枚举期间的 COM 噪声
                continue
        if target is None:
            print("  !! 没找到 Submit 控件，桌面腿跳过")
            return None
        rect = target.rectangle()
        x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        print(f"  取点：Submit 控件中心 ({x},{y})，窗口句柄 {hwnd}")

        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src")
        out = subprocess.run(
            [sys.executable, "-m", "rpa_core.capture.desktop_agent",
             "--point", str(x), str(y), "--window-handle", str(hwnd)],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
        )
        if out.returncode != 0:
            print(f"  !! agent 退出码 {out.returncode}；stderr 尾部：\n{out.stderr[-800:]}")
            return None
        return json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
        kill_demo_apps()


def main() -> int:
    qapp = QApplication.instance() or QApplication([])
    assert qapp is not None

    print(SEP)
    print("浏览器腿（形状取自 content.js buildDescriptor；非真机）")
    print(SEP)
    browser = browser_descriptor()
    render(browser)

    print()
    print(SEP)
    print("桌面腿（真机：靶子 + desktop-capture-agent --point）")
    print(SEP)
    desktop = desktop_descriptor()
    if desktop is None:
        print("  真机腿不可用（见上方提示）")
    else:
        print("  真机描述符：")
        print(
            "    "
            + json.dumps(desktop, ensure_ascii=False)[:600]
        )
        render(desktop)
    print(SEP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
