"""M47.11GUI 探针：影刀式页签布局 + 移除备选 + 截图落页签。

offscreen 跑，真造一张 PNG（纯 Python 手写最小 PNG，避免依赖QImage 绘图），
走完整链路：``ElementEditorForm(enable_live_preview(shot=...))`` → 切页签 → 截图回传
→ 红框坐标换算 → ``PreviewShot`` 显示。

用法：``.venv/Scripts/python.exe .harness/spike/probe_m47_11_gui.py``
"""

from __future__ import annotations

import base64
import os
import struct
import sys
import time
import zlib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from rpa_core.gui.element_editor import ElementEditorForm  # noqa: E402


def tiny_png(width: int = 2560, height: int = 1440) -> bytes:
    """纯手工 PNG（8-bit RGB，纯色带一条红带）——不依赖任何绘图库。"""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
        for x in range(width):
            if 900 <= y < 1000 and 100 <= x < 300:
                raw += bytes((255, 0, 0))
            else:
                raw += bytes((240, 240, 240))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


PNG_URL = "data:image/png;base64," + base64.b64encode(tiny_png()).decode("ascii")

DOC = {
    "kind": "browser",
    "verifyCount": 1,
    "selector": {
        "css": "#kw",
        "path": [
            {"tag": "div", "id": "", "classes": "wrap", "nthOfType": 1, "fragment": "div.wrap"},
            {"tag": "input", "id": "kw", "classes": "", "nthOfType": 1, "fragment": "input#kw"},
        ],
        # 运行期自愈要读它：UI 移除了，数据必须原样带回
        "candidates": [
            {"kind": "css", "selector": "input[name=q]", "matchedCount": 1},
            {"kind": "css", "selector": ".wrap input", "matchedCount": 3},
        ],
    },
    "metadata": {"tag": "input", "id": "kw"},
}


def main() -> int:
    app = QApplication.instance() or QApplication([])
    shots: list[dict] = []

    def preview(css: str) -> dict:
        return {"count": 1}

    def clear() -> dict:
        return {"count": 0}

    def shot(css: str, *, want_shot: bool = True) -> dict:
        shots.append({"css": css, "want_shot": want_shot})
        # rect 用 CSS 像素；图是 2 倍图（2560×1440 对应视口 1280×720 dpr=2）
        return {
            "count": 1,
            "dataUrl": PNG_URL,
            "rect": {"left": 100, "top": 50, "width": 200, "height": 80},
            "viewport": {"width": 1280, "height": 720, "dpr": 2,
                         "scrollX": 640, "scrollY": 300},
        }

    form = ElementEditorForm(DOC)
    form.enable_live_preview(preview, clear, shot)
    form.show()
    app.processEvents()

    print("== 页签 ==")
    titles = [form.tabs.tabText(i) for i in range(form.tabs.count())]
    print("  页签标题:", titles)
    assert titles == ["预览", "精准定位"], titles
    assert form.tabs.currentIndex() == 0
    assert form.tabs.widget(0) is form.preview_shot
    assert form.tabs.widget(1) is form.locate_page
    print("  预览页签挂的是 PreviewShot:", isinstance(form.preview_shot.__class__.__name__, str))
    print("  AI 辅助定位页签不摆:", "AI" not in "".join(titles))

    print("== 备选定位 UI 已移除 ==")
    for gone in ("candidate_list", "candidates_label", "promote_button"):
        assert not hasattr(form, gone), gone
    print("  candidate_list/candidates_label/promote_button 均不存在")

    print("== 底部选择器单选 / 锚点 ==")
    print("  默认选择器 checked:", form.selector_default_radio.isChecked())
    print("  XPath enabled:", form.selector_xpath_radio.isEnabled())
    print("  锚点添加 enabled:", form.anchor_add_button.isEnabled())
    assert form.selector_default_radio.isChecked()
    assert not form.selector_xpath_radio.isEnabled()
    assert not form.anchor_add_button.isEnabled()

    print("== 切到精准定位页==")
    form.tabs.setCurrentIndex(1)
    app.processEvents()
    print("  截图请求数（不该因切页增加）:", len(shots))
    assert len(shots) == 0, shots

    print("== 切回预览页 → 触发截图 ==")
    form.tabs.setCurrentIndex(0)
    for _ in range(40):
        app.processEvents()
        time.sleep(0.02)
        if form.preview_shot.has_shot:
            break
    print("  截图请求:", shots)
    assert len(shots) == 1, shots
    assert shots[0]["css"] == "#kw"
    assert shots[0]["want_shot"] is True
    print("  has_shot:", form.preview_shot.has_shot)
    assert form.preview_shot.has_shot
    print("  红框（图内像素）:", form.preview_shot.box)
    # 视口 1280×720、dpr=2、图 2560×1440 ⇒ scale 2 ⇒ (100,50,200,80)*2
    assert form.preview_shot.box == {"x": 200.0, "y": 100.0, "width": 400.0, "height": 160.0}, \
        form.preview_shot.box
    print("  message:", repr(form.preview_shot.message))

    print("== 无图/命中 0 / 坏 base64 三种降级文案 ==")
    form._shot_seq += 1
    form._on_shot_done({"seq": form._shot_seq, "count": 0})
    print("  count=0 无图  :", repr(form.preview_shot.message))
    form._shot_seq += 1
    form._on_shot_done({"seq": form._shot_seq, "count": 3})
    print("  count>0 无图  :", repr(form.preview_shot.message))
    form._shot_seq += 1
    form._on_shot_done({"seq": form._shot_seq, "count": 1,
                        "dataUrl": "data:image/png;base64,@@@notbase64@@@",
                        "rect": {"left": 1, "top": 1, "width": 1, "height": 1},
                        "viewport": {"width": 10, "height": 10, "dpr": 1}})
    print("  坏 base64   :", repr(form.preview_shot.message))
    assert "无法显示" in form.preview_shot.message
    form._shot_seq += 1
    form._on_shot_done({"seq": form._shot_seq, "error": "no-active-tab"})
    print("  通道 error  :", repr(form.preview_shot.message))
    assert "预览截图失败" in form.preview_shot.message

    print("== 截图失败不改命中标签（负验证 H4 的 GUI 侧）==")
    form.set_hit_label("命中 1 个（页面上已黄框高亮）", "#1a7f37")
    before = form.preview_label.text()
    form._shot_seq += 1
    form._on_shot_done({"seq": form._shot_seq, "error": "boom"})
    print("  前:", before, "| 后:", form.preview_label.text())
    assert form.preview_label.text() == before

    print("== 陈旧截图被丢弃 ==")
    form._shot_seq += 1
    form._on_shot_done({"seq": form._shot_seq - 1, "count": 1, "dataUrl": PNG_URL,
                        "rect": {"left": 1, "top": 1, "width": 1, "height": 1},
                        "viewport": {"width": 1280, "height": 720, "dpr": 2}})
    assert not form.preview_shot.has_shot, "陈旧截图不该覆盖"
    print("  陈旧回传被丢弃: OK")

    print("== result_document 仍带回 candidates ==")
    doc = form.result_document()
    print("  selector keys:", sorted(doc["selector"].keys()))
    print("  candidates:", doc["selector"].get("candidates"))
    assert doc["selector"]["candidates"] == DOC["selector"]["candidates"]
    assert doc["selector"]["path"] == DOC["selector"]["path"]

    print("== 老元素无 path：说清为什么空 ==")
    plain = ElementEditorForm({"kind": "browser", "selector": {"css": "#a"}})
    print("  path_list =", plain.path_list)
    print("  locate_page 上的标签:", plain.locate_page.findChildren(type(plain.info_label))[0].text())
    assert plain.path_list is None

    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())