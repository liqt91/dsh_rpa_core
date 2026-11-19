"""M102 S2b 探针：**网页元素**的缩略图能不能算出来（真机形状 / 真机数值）。

现场（2026-11-19 维护者反馈）：桌面元素入库后有截图，**网页元素没有**。

两处叠加的根因，这条探针各钉一条：

1. ``content.js::buildDescriptor`` 把 ``viewport`` 放在描述符**顶层**，而 ppx 的
   ``_hold_captured`` 会先归一化成 ``ElementDescriptor``（``extra="ignore"``）——顶层
   ``viewport`` 在那一步被静默丢掉，几何算不出，整张图放弃。
2. 就算 geometry 拿到了，浏览器腿原来的口径（窗口截图 + ``outerWidth - width`` 猜边框）
   在 Edge 开侧栏时算出 **负的 origin_y**，元素被判成「滚出窗口」。真机 trace 的数
   （视口 1628 / 窗口 1920 / 元素 rect 1543,44,54,54）就是这条的第 4 组判据。

四条判据：
1. **真机视口图**（``space="viewport"`` + 上面那组真实数字）⇒ 出图 58×58；
2. 窗口图 + 顶层 viewport（第 1 处修的那个形状）⇒ 仍要出图；
3. 既没有图也没有窗口句柄 ⇒ ``None``（no_window，不许编）；
4. 有图但没有视口信息 ⇒ **兜底整图**（长边 512），不是 None，也不许猜坐标。
"""

from __future__ import annotations

import base64
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".harness" / "demo" / "ppx_probe" / "app"))

from api import api as A  # noqa: E402
from PIL import Image  # noqa: E402

from rpa_core.capture import screen_shot as ss  # noqa: E402

PAD = ss._SHOT_PAD
# 真机数据（2026-11-19 11:39:49 的 trace，Edge 开着侧栏）
REAL_VIEWPORT = {"width": 1628, "height": 996, "dpr": 1,
                 "outerWidth": 1920, "outerHeight": 1040}
REAL_RECT = {"left": 1543, "top": 44, "width": 54, "height": 54}
# 理想窗口（左右边框各 8、视口底贴窗口底）：窗口图口径在这组数上是对的
TIDY_VIEWPORT = {"width": 800, "height": 600, "outerWidth": 810, "outerHeight": 640}
TIDY_RECT = {"left": 100, "top": 100, "width": 100, "height": 20}


def _shot(width: int, height: int) -> dict:
    image = Image.new("RGBA", (width, height), (30, 140, 210, 255))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return {
        "dataUrl": "data:image/png;base64," + base64.b64encode(out.getvalue()).decode(),
        "windowOrigin": [0, 0],
        "imageSize": [width, height],
    }


def _hold(descriptor: dict) -> dict | None:
    """走 ppx 真实的捕获暂存路径（归一化 + 裁图都在里面）。"""
    entry: dict = {"path": "", "name": None, "suggestName": "元素1", "nodeId": None}
    A._hold_captured(entry, descriptor)
    return entry.get("shot")


def _browser(rect: dict, viewport: dict | None, shot: dict | None) -> dict:
    descriptor: dict = {
        "kind": "browser",
        "selector": {"css": "#search", "path": [], "candidates": []},
        "verifyCount": 1,
        "metadata": {"rect": dict(rect), "tag": "input"},
    }
    if viewport is not None:
        descriptor["viewport"] = dict(viewport)  # content.js 的真实位置：与 selector 平级
    if shot is not None:
        descriptor["captureShot"] = shot
    return descriptor


def _size(shot: dict | None) -> tuple:
    return (shot or {}).get("width"), (shot or {}).get("height")


def main() -> int:
    failures: list[str] = []

    # 1. 真机视口图：图就是视口（1628×996），rect 是视口坐标 ⇒ 只差缩放比 1。
    #    ★ 只给 dataUrl + space，与 host::_take_viewport_shot 的产出逐字段一致——
    #    它**不**给 imageSize（2026-11-20「还是没有截图」的根因就藏在这）。
    real = _shot(1628, 996)
    real = {"dataUrl": real["dataUrl"], "space": "viewport"}
    got = _hold(_browser(REAL_RECT, REAL_VIEWPORT, real))
    print("1. 真机视口图:", _size(got), "期望", (54 + 2 * PAD, 54 + 2 * PAD))
    if _size(got) != (54 + 2 * PAD, 54 + 2 * PAD):
        failures.append("真机视口图没裁出元素特写（不是兜底就是几何错）")

    # 2. 窗口图 + 顶层 viewport：第一处修复（viewport 被 ElementDescriptor 吃掉）
    tidy = _shot(810, 640)
    got = _hold(_browser(TIDY_RECT, TIDY_VIEWPORT, tidy))
    print("2. 窗口图 + 顶层 viewport:", _size(got), "期望", (100 + 2 * PAD, 20 + 2 * PAD))
    if _size(got) != (100 + 2 * PAD, 20 + 2 * PAD):
        failures.append("窗口图口径回退被弄断了（顶层 viewport 读不到）")

    # 3. 没有图也没有句柄：只能放弃
    got = _hold(_browser(TIDY_RECT, TIDY_VIEWPORT, None))
    print("3. 无图无句柄:", got, "期望 None")
    if got is not None:
        failures.append("没有图也出了缩略图 = 凭空造图")

    # 4. 有图没视口：兜底整图，不许猜坐标
    got = _hold(_browser(TIDY_RECT, None, tidy))
    print("4. 有图无视口:", _size(got), "期望长边", ss._SHOT_MAX_EDGE)
    if not got or max(_size(got)) != ss._SHOT_MAX_EDGE:
        failures.append("算不出几何时没兜底成整图")

    if failures:
        for item in failures:
            print("FAIL:", item)
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="m102b_") as tmp:
        A.WORKSPACE = Path(tmp)  # 探针不写盘，但别让它落到真实工作区
        raise SystemExit(main())
