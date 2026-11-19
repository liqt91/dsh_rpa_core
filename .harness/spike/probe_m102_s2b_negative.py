"""M102 S2b 负向验证：网页元素缩略图这条链上**七处**必须真拦得住的地方。

现场（2026-11-19）：桌面元素有截图、网页元素没有。一个字都没写错，是三段各错一点
叠出来的——viewport 在顶层被 pydantic 丢掉、窗口几何在 Edge 侧栏下算出负原点、
扩展根本没要过视口图。所以负向验证也要分三层打：

**Python 纯函数层**（改 screen_shot.py）
* ``selector-only``      viewport 退回只读 selector ⇒ 「真机形状」用例红
* ``space-ignored``      不认 ``space="viewport"``（全按窗口图算）⇒ 视口图用例红
* ``no-fallback``        去掉整图兜底 ⇒ 兜底用例红

**Python 接线层**（改 extension.py）
* ``host-drops-shot``    host 不挂扩展给的图 ⇒ 只有接线单测红（纯函数层全绿）

**JS 两端**（改 content.js / background.js）
* ``content-no-shot``    发消息不带上图 ⇒ 只有 node 通道判据红
* ``host-forgets``       background 转发时不透传 ⇒ 同上
* ``overlay-first``      先截图后收红框（框被拍进缩略图）⇒ 同上

每一轮都断言「实际失败集 == 期望失败集」，再按原字节写回并核 md5；带哨兵防叠注入。
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv/Scripts/python.exe"
PPX_PY = ROOT / ".harness/demo/ppx_probe/.venv/Scripts/python.exe"
SHOT = ROOT / "src/rpa_core/capture/screen_shot.py"
EXT = ROOT / "src/rpa_core/capture/extension.py"
CONTENT = ROOT / "extension/content.js"
BACKGROUND = ROOT / "extension/background.js"
SHOT_TESTS = ["tests/contract/test_element_shot.py",
              "tests/contract/test_extension_viewport_shot.py"]
T = "tests/contract/test_element_shot.py::"
E = "tests/contract/test_extension_viewport_shot.py::"
NODE_CHECK = "scripts/check_capture_shot.mjs"
S2B = ".harness/spike/probe_m102_s2b_web_shot.py"
WIRING = ".harness/spike/probe_m102_s1_wiring.py"
MARKER = "  # <<<M102B-INJECTED>>>"
JS_MARKER = "  /* <<<M102B-INJECTED>>> */"

CASES = [
    {
        "name": "selector-only",
        "file": SHOT,
        "edits": [('    for candidate in (descriptor.get("viewport"), sel.get("viewport")):',
                   '    for candidate in (sel.get("viewport"),):')],
        # 读不到 viewport 会连带压红视口图那条（它的缩放比分母就是视口宽/高）——
        # 这是**同一因**的两条断言，不是误红
        "expected": {
            "pytest": {T + "test_element_shot_browser_leg_uses_browser_geometry",
                       T + "test_element_shot_viewport_shot_uses_scale_only",
                       # 新用例的 viewport 也在顶层：读不到同样只能兜底整图，尺寸就不对
                       T + "test_element_shot_viewport_shot_without_image_size"},
            "s2b": True, "wiring": True, "node": set(),
        },
    },
    {
        "name": "space-ignored",
        "file": SHOT,
        "edits": [('        if str(shot.get("space") or "") == "viewport":', "        if False:")],
        "expected": {
            "pytest": {T + "test_element_shot_viewport_shot_uses_scale_only",
                       # 新用例的视口也只有 width/height：当成窗口图算会走错原点 ⇒ 红
                       T + "test_element_shot_viewport_shot_without_image_size"},
            "s2b": True, "wiring": True, "node": set(),
        },
    },
    {
        "name": "no-fallback",
        "file": SHOT,
        "edits": [("        if box is None:", "        if box is None and False:")],
        "expected": {
            "pytest": {T + "test_element_shot_browser_without_viewport_falls_back_to_full_image"},
            "s2b": True, "wiring": False, "node": set(),
        },
    },
    {
        "name": "host-drops-shot",
        "file": EXT,
        "edits": [('        if isinstance(shot, str) and shot.startswith("data:image/"):',
                   "        if False:")],
        # 纯函数层看不见：它直接吃原始描述符，根本不经过 host 那段
        "expected": {
            "pytest": {E + "test_viewport_shot_is_attached_with_space_marker"},
            "s2b": False, "wiring": False, "node": set(),
        },
    },
    {
        "name": "content-no-shot",
        "file": CONTENT,
        "edits": [("        viewportShot: viewportShot || undefined,",
                   "        viewportShot: undefined,")],
        "expected": {"pytest": set(), "s2b": False, "wiring": False, "node": {"S8 捕获结果里带上视口图"}},
    },
    {
        "name": "host-forgets",
        "file": BACKGROUND,
        "edits": [("      viewportShot: msg.viewportShot,", "      viewportShot: undefined,")],
        "expected": {"pytest": set(), "s2b": False, "wiring": False,
                     "node": {"S9 capture-result 透传 viewportShot 给 host"}},
    },
    {
        "name": "overlay-first",
        "file": CONTENT,
        "edits": [("    hideOverlay();\n    const viewportShot = await requestVisibleShot();",
                   "    const viewportShot = await requestVisibleShot();")],
        "expected": {"pytest": set(), "s2b": False, "wiring": False,
                     "node": {"S7 先收覆盖层、后截图（否则把红框拍进缩略图）"}},
    },
]


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def fail_set_from_pytest(output: str) -> set[str]:
    out: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"FAILED\s+(\S+)", line.strip())
        if match:
            out.add(match.group(1).replace("\\", "/"))
    return out


def fail_set_from_node(output: str) -> set[str]:
    out: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"FAIL\s+\|\s+(.+?)(?:\s+→.*)?$", line.strip())
        if match:
            out.add(match.group(1))
    return out


def _run(cmd: list[str], extract) -> set[str]:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return extract(proc.stdout + proc.stderr)


def run_pytest() -> set[str]:
    return _run([str(PY), "-m", "pytest", "-q", "--tb=no", "-rf", *SHOT_TESTS],
                fail_set_from_pytest)


def run_node() -> set[str]:
    return _run(["node", NODE_CHECK], fail_set_from_node)


def _probe(script: str) -> bool:
    """探针跑得通 = False（不红），跑不通 = True。"""
    proc = subprocess.run([str(PPX_PY), script], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return proc.returncode != 0


def main() -> int:
    for case in CASES:
        path: pathlib.Path = case["file"]
        text = path.read_text(encoding="utf-8")
        if "<<<M102B-INJECTED>>>" in text:
            print(f"[哨兵] {path.name} 里还有上次的注入标记，拒绝运行")
            return 2
        for old, _new in case["edits"]:
            if text.count(old) != 1:
                print(f"[锚点] {case['name']}: 在 {path.name} 出现 {text.count(old)} 次，必须恰好 1 次")
                return 2

    problems: list[str] = []
    for case in CASES:
        path = case["file"]
        original = path.read_bytes()
        before = md5(path)
        marker = JS_MARKER if path.suffix == ".js" else MARKER
        try:
            text = original.decode("utf-8")
            for old, new in case["edits"]:
                text = text.replace(old, new + marker, 1)
            path.write_bytes(text.encode("utf-8"))
            got = {
                "pytest": run_pytest(),
                "node": run_node(),
                "s2b": _probe(S2B),
                "wiring": _probe(WIRING),
            }
            expected = case["expected"]
            diffs: list[str] = []
            for key in ("pytest", "node"):
                if got[key] != expected[key]:
                    diffs.append(
                        f"{key} 缺失(应红未红)={sorted(expected[key] - got[key])} "
                        f"多出(不该红)={sorted(got[key] - expected[key])}")
            for key in ("s2b", "wiring"):
                if got[key] is not expected[key]:
                    diffs.append(f"{key}={'红' if got[key] else '绿'}")
            if diffs:
                problems.append(f"{case['name']}: " + "；".join(diffs))
                print(f"FAIL {case['name']}: " + "；".join(diffs))
            else:
                print(f"OK   {case['name']}: pytest {len(got['pytest'])} 条、"
                      f"node {len(got['node'])} 条、s2b/wiring "
                      f"{'红' if got['s2b'] else '绿'}/{'红' if got['wiring'] else '绿'}")
        finally:
            path.write_bytes(original)
            if md5(path) != before:
                print(f"[还原失败] {path}")
                return 3

    if problems:
        print("\n负向验证未通过：")
        for item in problems:
            print(" -", item)
        return 1
    print("\nM102 S2b NEGATIVE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
