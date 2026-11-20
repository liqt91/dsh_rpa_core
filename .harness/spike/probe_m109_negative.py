"""M109 负向验证：8 处注入，逐条确认「新判据真的会红」并逐字节还原。

每条注入只改一处、跑目标判据、断言目标用例出现在失败列表里（不是数阈值）。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv/Scripts/python.exe")
BASETEMP = "C:/Users/Administrator/AppData/Local/Temp/rpa_neg_m109"

CASES = [
    {
        "file": "src/rpa_core/capture_kit/selector_tree.py",
        "anchor": '    picked = [i for i, level in enumerate(levels) if level["included"]]\n',
        "replace": "    picked = list(range(len(levels)))\n",
        "runner": "pytest",
        "target": "test_unchecked_level_contributes_nothing_even_with_attributes_on",
        "label": "compose 无视层级勾选",
    },
    {
        "file": "src/rpa_core/capture_kit/desktop_editing.py",
        "anchor": '        if node.get("readonly") or not node.get("selected"):\n',
        "replace": '        if node.get("readonly"):\n',
        "runner": "pytest",
        "target": "test_unchecked_step_drops_out_of_path_even_with_attributes_on",
        "label": "桌面 compose 无视 selected",
    },
    {
        "file": "src/rpa_core/capture_kit/edit_dialog.py",
        "anchor": '            if meta[0] not in ("level", "level_check")\n',
        "replace": '            if meta[0] != "level"\n',
        "runner": "pytest",
        "target": "test_rebuild_attr_pane_keeps_level_boxes_and_gates_attributes",
        "label": "属性重建清掉 level_check 映射",
    },
    {
        "file": "src/rpa_core/capture_kit/edit_dialog.py",
        "anchor": "            win32gui.EnableWindow(box, level_on)\n",
        "replace": "            win32gui.EnableWindow(box, True)\n",
        "runner": "pytest",
        "target": "test_rebuild_attr_pane_keeps_level_boxes_and_gates_attributes",
        "label": "未勾层属性框仍可编辑",
    },
    {
        "file": "src/rpa_core/capture_kit/edit_dialog.py",
        "anchor": '            meta[2]["on"] = bool(win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0))\n',
        "replace": (
            '            meta[2]["on"] = bool(win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0))\n'
            '            self._levels[self._selected]["included"] = True\n'
        ),
        "runner": "pytest",
        "target": "test_attr_check_never_pulls_its_level_back_in",
        "label": "网页层级手写回勾",
    },
    {
        "file": "src/rpa_core/capture_kit/edit_dialog.py",
        "anchor": (
            '            row["required"] = bool(win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0))\n'
        ),
        "replace": (
            '            row["required"] = bool(win32gui.SendMessage(box, win32con.BM_GETCHECK, 0, 0))\n'
            '            self._nodes[self._selected_node]["selected"] = True\n'
        ),
        "runner": "pytest",
        "target": "test_desktop_attr_check_never_pulls_its_node_back_in",
        "label": "桌面节点手写回勾",
    },
    {
        "file": ".harness/demo/ppx_probe/app/gui/src/main.js",
        "anchor": (
            "      obj.on = on.checked\n"
            "      // 只改「这层用哪些字段」，不改「这层参不参与」（M109）。\n"
        ),
        "replace": (
            "      obj.on = on.checked\n"
            "      level.included = true\n"
        ),
        "runner": "node",
        "target": "渲染层：未勾层的属性区只读，勾属性不回勾层级",
        "label": "Web 属性勾选回勾层级",
    },
    {
        "file": ".harness/demo/ppx_probe/app/gui/src/main.js",
        "anchor": "    on.disabled = !levelOn\n",
        "replace": "    on.disabled = false\n",
        "runner": "node",
        "target": "渲染层：未勾层的属性区只读，勾属性不回勾层级",
        "label": "Web 未勾层属性框仍可勾",
    },
]


def run(case: dict) -> tuple[int, str]:
    if case["runner"] == "pytest":
        cmd = [PY, "-m", "pytest", f"tests/unit/{case['target_file']}",
               "-q", f"--basetemp={BASETEMP}", "-k", case["target"]]
    else:
        cmd = ["node", "scripts/check_element_pick_ui.mjs"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout + proc.stderr


TARGET_FILE = {
    "compose 无视层级勾选": "test_selector_tree.py",
    "桌面 compose 无视 selected": "test_desktop_editing.py",
    "属性重建清掉 level_check 映射": "test_capture_kit.py",
    "未勾层属性框仍可编辑": "test_capture_kit.py",
    "网页层级手写回勾": "test_capture_kit.py",
    "桌面节点手写回勾": "test_capture_kit.py",
}


def main() -> None:
    for case in CASES:
        case["target_file"] = TARGET_FILE.get(case["label"], "")
        path = ROOT / case["file"]
        orig = path.read_bytes()
        digest = hashlib.md5(orig).hexdigest()
        text = orig.decode("utf-8")
        count = text.count(case["anchor"])
        assert count == 1, f"{case['label']}: 锚点命中 {count} 次，不是唯一"
        try:
            path.write_bytes(text.replace(case["anchor"], case["replace"]).encode("utf-8"))
            rc, out = run(case)
            hit = case["target"] in out
            failed = out.count("FAILED ") + out.count("FAIL ")
            print(f"{case['label']}: rc={rc} 目标命中={hit} 失败条数={failed}")
            assert rc != 0, f"{case['label']}: 注入后仍退出 0（判据没拦住）"
            assert hit, f"{case['label']}: 红的是别的用例，目标判据没打中\n{out[-800:]}"
        finally:
            path.write_bytes(orig)
        assert hashlib.md5(path.read_bytes()).hexdigest() == digest, f"{case['label']}: 还原不一致"
    print("M109 NEGATIVE OK: 8/8 注入均按预期报红，md5 全部还原")


if __name__ == "__main__":
    sys.exit(main())
