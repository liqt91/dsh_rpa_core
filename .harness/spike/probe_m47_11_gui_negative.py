"""M47.11 GUI 侧负向验证：影刀式页签 / 移除备选 / 截图落页签的判据是否真拦得住。

跑受影响的三 个测试文件，要求：``FAILED`` 行 > 0（用行而不是摘要——本机 pytest在
teardown 被批量删除守卫SystemExit，摘要行永远打不出来，见 probe_m47_11_shot_negative.py
的说明）、``ERROR`` 行 == 0（挂住/收集失败不算拦住）、命中期望 nodeid；跑完逐字节还原核 md5。

注入清单（7 条）：
  G1 备选 UI 复活（摆回候选列表）⇒ 「备选已移除」判据要红。
  G2 result_document 丢 candidates ⇒ 「删界面≠删数据」判据要红。
  G3 XPath 单选变成可选 ⇒ 「置灰」判据要红（能点但运行期只认 css = 假功能）。
  G4 页签拆掉「精准定位」⇒ 页签判据要红。
  G5 截图失败改写命中标签 ⇒ 「截图失败不动真判据」要红。
  G6 红框不按图实际宽换算（scale 恒1）⇒ 坐标判据要红。
  G7 切页签不再触发截图（截图入口删掉）⇒ 「切到预览才截图」要红。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EDITOR = ROOT / "src" / "rpa_core" / "gui" / "element_editor.py"
VERIFY = ROOT / "src" / "rpa_core" / "capture" / "verify.py"
TESTS = [
    ROOT / "tests" / "contract" / "test_gui_element_editor.py",
    ROOT / "tests" / "contract" / "test_gui_panels.py",
    ROOT / "tests" / "contract" / "test_verify_element.py",
]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")

EXPECTED = (
    "test_browser_form_has_no_candidate_widgets",
    "test_candidates_are_still_written_back",
    "test_candidates_written_back_even_after_editing_css",
    "test_selector_choice_and_anchor_are_greyed_out",
    "test_browser_form_has_preview_and_locate_tabs",
    "test_shot_failure_does_not_touch_hit_label",
    "test_preview_box_scales_by_image_size_not_assumed_dpr",
    "test_screenshot_only_requested_when_switching_to_preview",
    "test_element_dialog_has_no_candidate_ui_but_keeps_data",
    "test_stale_shot_reply_is_discarded",
    "test_preview_shot_without_channel_says_so_instead_of_blank",
)


def run_pytest(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", "-p", "no:cacheprovider", "--tb=line", "-q", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout + proc.stderr


def failed_lines(output: str) -> list[str]:
    return [
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith(("FAILED ", "ERROR "))
    ]


def counts(output: str) -> tuple[int, int]:
    lines = failed_lines(output)
    return (
        sum(1 for line in lines if line.startswith("FAILED ")),
        sum(1 for line in lines if line.startswith("ERROR ")),
    )


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


INJECTIONS: list[tuple[str, Path, str, str, str]] = [
    (
        "G1",
        EDITOR,
        '        self.tabs.addTab(self.locate_page, "精准定位")',
        '        self.tabs.addTab(self.locate_page, "精准定位")\n'
        "        self.candidate_list = QListWidget()",
        "备选 UI 复活：test_browser_form_has_no_candidate_widgets 要红",
    ),
    (
        "G2",
        EDITOR,
        '            if self._candidates:\n                selector["candidates"] = [dict(item) for item in self._candidates]',
        "            pass",
        "写回时丢 candidates：test_candidates_are_still_written_back 要红",
    ),
    (
        "G3",
        EDITOR,
        "        self.selector_xpath_radio.setEnabled(False)",
        "        self.selector_xpath_radio.setEnabled(True)",
        "XPath 变成可选：test_selector_choice_and_anchor_are_greyed_out 要红",
    ),
    (
        "G4",
        EDITOR,
        '        self.tabs.addTab(self.locate_page, "精准定位")',
        '        self.locate_page.setVisible(True)\n        layout.addWidget(self.locate_page)',
        "精准定位页签被拆掉：页签判据要红",
    ),
    (
        "G5",
        EDITOR,
        '        if data.get("error"):\n            # 截图失败**不**改命中标签：那是「校验失败」，而截图只是观感增强。\n            self.preview_shot.clear(f"预览截图失败：{data[\'error\']}")\n            return',
        '        if data.get("error"):\n            self.set_hit_label(f"预览失败：{data[\'error\']}", DANGER)\n            self.preview_shot.clear(f"预览截图失败：{data[\'error\']}")\n            return',
        "截图失败改写命中标签：test_shot_failure_does_not_touch_hit_label 要红",
    ),
    (
        "G6",
        VERIFY,
        "        if img_w > 0 and vp_w > 0:\n            scale_x = img_w / vp_w",
        "        if img_w > 0 and vp_w > 0:\n            scale_x = 1.0",
        "红框不按图实际宽换算：preview_box 缩放判据要红",
    ),
    (
        "G7",
        EDITOR,
        "        if index == 0:\n            self._run_shot()",
        "        if index == 99:\n            self._run_shot()",
        "切页不再触发截图：test_screenshot_only_requested_when_switching_to_preview 要红",
    ),
]


def main() -> int:
    originals = {entry[1]: entry[1].read_bytes() for entry in INJECTIONS}
    tests = [str(p) for p in TESTS]

    code, out = run_pytest(*tests)
    if counts(out) != (0, 0):
        print("BASELINE 红了，先修判据/被测代码再谈注入：")
        print(out[-4000:])
        return 2
    print(f"BASELINE 绿（failed=0 error=0；exit={code}，删除守卫 SystemExit 不计判据）")

    verdicts: list[tuple[str, bool, str]] = []
    try:
        for entry in INJECTIONS:
            tag, target, old, new = entry[:4]
            text = originals[target].decode("utf-8")
            if text.count(old) != 1:
                print(f"[{tag}] SKIP：锚点命中 {text.count(old)} 次（要求恰好 1）")
                verdicts.append((tag, False, f"锚点命中 {text.count(old)} 次"))
                continue
            target.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            code, out = run_pytest(*tests)
            lines = failed_lines(out)
            failed, error = counts(out)
            hit = [key for key in EXPECTED if any(key in line for line in lines)]
            ok = failed > 0 and error == 0 and bool(hit)
            detail = f"failed={failed} error={error} 命中={hit}"
            print(f"[{tag}] {'红' if ok else '未拦住'}：{detail}")
            verdicts.append((tag, ok, detail))
    finally:
        for path, data in originals.items():
            path.write_bytes(data)

    bad = [t for t, ok, _ in verdicts if not ok]
    for path, data in originals.items():
        same = md5(path) == hashlib.md5(data).hexdigest()
        print(f"还原核 md5 {path.name}: {'一致' if same else '不一致!!'}")
        if not same:
            bad.append(f"restore:{path.name}")
    for tag, ok, detail in verdicts:
        print(f"  {tag}: {'PASS' if ok else 'FAIL'} {detail}")
    print("全部注入均被拦住" if not bad else f"未拦住: {bad}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())