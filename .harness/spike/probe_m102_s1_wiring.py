"""M102 S1 接线探针：**真跑** api.py 的入库路径，看缩略图到底有没有进资产。

纯函数层绿 ≠ 接线层绿（memory §1）。这里用 ppx 自己的 venv 导入 ``api.api``，
造一个临时流程 + 一个带 ``captureShot`` 的浏览器描述符，走
``_hold_captured`` → ``_do_commit`` → ``list_elements`` → ``element_shot``
全链路，最后把盘上的文件与 JSON 都读出来核对。

只读真实工作区之外的东西：所有写操作都落在 tempfile 造的临时目录里。
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".harness" / "demo" / "ppx_probe" / "app"))

from PIL import Image  # noqa: E402

from api import api as A  # noqa: E402
from rpa_core.capture import screen_shot as ss  # noqa: E402


def make_png(width: int, height: int) -> bytes:
    image = Image.new("RGBA", (width, height), (30, 140, 210, 255))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="m102_probe_"))
    try:
        A.WORKSPACE = tmp
        flow = tmp / "workflows" / "demo" / "flow.json"
        flow.parent.mkdir(parents=True, exist_ok=True)
        flow.write_text(
            json.dumps({"name": "demo", "root": {"kind": "sequence", "steps": []}},
                       ensure_ascii=False),
            encoding="utf-8",
        )

        # 浏览器腿：captureVisibleTab 的**视口图**（800×600，与视口 1:1），
        # viewport 放**顶层**——content.js::buildDescriptor 的真实形状。
        # 早先探针把 viewport 放在 ``selector.viewport``（host 的旧假设），真机网页
        # 元素却没有缩略图（M102 S2b）：形状错一层，判据就只在测试里成立。
        data_url = "data:image/png;base64," + base64.b64encode(make_png(800, 600)).decode()
        descriptor = {
            "kind": "browser",
            "selector": {"css": "#search"},
            "viewport": {"width": 800, "height": 600,
                         "outerWidth": 1920, "outerHeight": 1040},
            "verifyCount": 1,
            "metadata": {"rect": {"left": 100, "top": 100, "width": 100, "height": 20}},
            "captureShot": {"dataUrl": data_url, "imageSize": [800, 600],
                            "space": "viewport"},
        }

        entry = {"path": str(flow), "name": None, "suggestName": "元素1", "nodeId": None}
        A._hold_captured(entry, descriptor)
        shot = entry.get("shot")
        size = ((shot or {}).get("width"), (shot or {}).get("height"))
        # 只判「有没有图」不够：几何读错位置时也能出一张（兜底整图 512 长边），
        # 那正是 S2b 现场之前一直绿着的原因。尺寸必须**恰好**是元素特写。
        want = (100 + 2 * ss._SHOT_PAD, 20 + 2 * ss._SHOT_PAD)
        print("· _hold_captured 产出缩略图:", bool(shot), "尺寸:", size, "期望:", want)
        if size != want:
            print("FAIL: 捕获阶段没产出缩略图")
            return 1

        res = A._do_commit(entry, "元素1")
        print("· commit:", res.get("ok"), res.get("file"))
        if not res.get("ok"):
            print("FAIL: 入库失败", res)
            return 1

        asset = Path(res["file"])
        doc = json.loads(asset.read_text(encoding="utf-8"))
        print("· 资产 screenshot =", doc.get("screenshot"))
        shot_path = asset.parent / str(doc.get("screenshot") or "")
        print("· png 存在:", shot_path.is_file(), "字节:", shot_path.stat().st_size
              if shot_path.is_file() else 0)
        if not shot_path.is_file():
            print("FAIL: png 没落盘")
            return 1

        # 资产必须还能过内核模型（cli element validate 走同一条）
        from rpa_core.model.capture import validate_element_document

        validate_element_document(doc)
        print("· validate_element_document: OK（screenshot 是字符串）")

        listed = A.list_elements(str(flow))
        item = (listed.get("elements") or [{}])[0]
        print("· listElements:", {k: item.get(k) for k in ("name", "kind", "driver", "screenshot")})
        if item.get("screenshot") != doc.get("screenshot"):
            print("FAIL: listElements 没把 screenshot 透传出去")
            return 1

        payload = A.element_shot(str(flow), "元素1")
        shot_url = payload.get("shot") or ""
        print("· elementShot:", payload.get("ok"), "dataUrl 前缀:", shot_url[:30],
              "长度:", len(shot_url))
        if not shot_url.startswith("data:image/png;base64,"):
            print("FAIL: elementShot 没返回可用 data URL")
            return 1

        # 没图的资产：合法状态，不是错误
        ok_none = A.element_shot(str(flow), "不存在的元素")
        print("· 不存在的元素:", ok_none)
        print("RESULT: PASS")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
