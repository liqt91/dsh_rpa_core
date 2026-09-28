"""M44（P1 一整片）负向验证探针：对**本轮新增判据**逐处注入破坏，确认它们真的拦得住。

流程（对每处注入）：

1. **对照跑**：注入前用原始文件跑同一命令，必须绿 —— 排除「无论跑什么都红」的环境；
2. **注入**：字节级精确替换（`old` 必须在文件里恰好出现一次）；
3. **判定**：
   - pytest 类：退出码非 0，且**必须**出现 ``N failed``、**不得**出现 ``N error``
     （`returncode != 0` 分不清「断言失败」与「语法/收集错误」，必须看汇总行）；
   - node 门禁类：退出码非 0，且输出里出现**指定的那条**检查名（精确命中，不许「随便红」）；
4. **还原**：写回原始字节并核对 md5 —— 逐字节一致才算还原成功；
5. 全部注入结束后，再跑一遍对照（全绿收口）。

超时按「挂住」如实上报（判据被破坏后走进真模态对话框会永久阻塞，那既不是红也不是绿）。

跑法（Windows；offscreen，不抢前台）::

    .venv/Scripts/python.exe .harness/spike/probe_m44_negative.py
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable
NODE = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"

Q = ["-o", "addopts=", "-q", "-p", "no:randomly"]

REL = lambda p: str(Path(p).as_posix())  # noqa: E731

CASES: list[dict] = [
    # ---- S1：content.js 光标与祖先链（node 门禁） -------------------------
    {
        "name": "S1 光标 keyup 监听被摘除 → lifecycle 场景 11 红",
        "file": "extension/content.js",
        "old": 'on(document, "keyup", onKeyUp, true);',
        "new": ";",
        "cmd": [NODE, "scripts/check_capture_lifecycle.mjs"],
        "expect": "S11",
        "kind": "gate",
    },
    {
        "name": "S1 回传丢 path → lifecycle 场景 12 红",
        "file": "extension/content.js",
        "old": "selector: { css, path: pathFor(el), candidates: candidatesFor(el, css) },",
        "new": "selector: { css, candidates: candidatesFor(el, css) },",
        "cmd": [NODE, "scripts/check_capture_lifecycle.mjs"],
        "expect": "S12 捕获回传 selector.path",
        "kind": "gate",
    },
    {
        "name": "S1 cursorKeyOf 把 Ctrl 认成无键 → helpers 红",
        "file": "extension/content.js",
        "old": 'if (key === "Control") return "ctrl";',
        "new": 'if (key === "Control") return null;',
        "cmd": [NODE, "scripts/check_capture_helpers.mjs"],
        "expect": "cursorKeyOf",
        "kind": "gate",
    },
    # ---- S1：模型侧 path 校验 ---------------------------------------------
    {
        "name": "S1 _path_errors 被短路 → 模型判据红",
        "file": "src/rpa_core/model/capture.py",
        "old": "errors.extend(_path_errors(element))",
        "new": "errors.extend([])",
        "cmd": [PY, "-m", "pytest", "tests/unit/test_element_descriptor.py", "-k",
                "malformed_path", *Q],
        "expect_re": r"\d+ failed",
        "kind": "pytest",
    },
    # ---- S3/S4：确认框出口与 app 接线 -------------------------------------
    {
        "name": "S3 重新捕获被改成要校验 → 判据红",
        "file": "src/rpa_core/gui/element_panel.py",
        "old": '        self._close_with("recapture")',
        "new": '        if self._validate_or_report():\n            self._close_with("recapture")',
        "cmd": [PY, "-m", "pytest",
                "tests/contract/test_gui_panels.py::test_element_dialog_recapture_discards_without_validation",
                *Q],
        "expect_re": r"\d+ failed",
        "kind": "pytest",
    },
    {
        "name": "S3 保存并继续的意图被记成保存 → 判据红",
        "file": "src/rpa_core/gui/element_panel.py",
        "old": '            self._close_with("save_and_continue")',
        "new": '            self._close_with("save")',
        "cmd": [PY, "-m", "pytest",
                "tests/contract/test_gui_panels.py::test_element_dialog_reports_which_exit_was_used",
                *Q],
        "expect_re": r"\d+ failed",
        "kind": "pytest",
    },
    {
        "name": "S4 保存并继续不再重启捕获 → 判据红",
        "file": "src/rpa_core/gui/app.py",
        "old": '        if intent == "save_and_continue":',
        "new": "        if False:",
        "cmd": [PY, "-m", "pytest",
                "tests/contract/test_gui_capture.py::test_capture_save_and_continue_restarts_capture",
                *Q],
        "expect_re": r"\d+ failed",
        "kind": "pytest",
    },
    {
        "name": "S4 重新捕获不再重启捕获 → 判据红",
        "file": "src/rpa_core/gui/app.py",
        "old": (
            '        if intent is None:\n'
            '            return\n'
            '        if intent == "recapture":\n'
            '            # 「重新捕获」= 立刻开下一轮，用户不必关窗再点「捕获元素」。\n'
        ),
        "new": (
            '        if intent is None:\n'
            '            return\n'
            '        if False:\n'
            '            # 「重新捕获」= 立刻开下一轮，用户不必关窗再点「捕获元素」。\n'
        ),
        "cmd": [PY, "-m", "pytest",
                "tests/contract/test_gui_capture.py::test_capture_recapture_discards_and_restarts",
                *Q],
        "expect_re": r"\d+ failed",
        "kind": "pytest",
    },
    # ---- S5：节点树 --------------------------------------------------------
    {
        "name": "S5 末级不再强制勾回 → 判据红",
        "file": "src/rpa_core/gui/element_editor.py",
        "old": "        if self.path_list.item(last_row).checkState() != Qt.CheckState.Checked:",
        "new": "        if False:",
        "cmd": [PY, "-m", "pytest",
                "tests/contract/test_gui_element_editor.py::test_path_tree_leaf_cannot_be_unchecked",
                *Q],
        "expect_re": r"\d+ failed",
        "kind": "pytest",
    },
    {
        "name": "S5 建树不再按 css 反推勾选态 → 判据红",
        "file": "src/rpa_core/gui/element_editor.py",
        "old": '                if " > ".join(fragments[k:]) == css',
        "new": "                if False:",
        "cmd": [PY, "-m", "pytest",
                "tests/contract/test_gui_element_editor.py::test_path_tree_derives_checks_from_shortened_css",
                *Q],
        "expect_re": r"\d+ failed",
        "kind": "pytest",
    },
]


def run(cmd: list[str], timeout: float = 240.0) -> tuple[int, str, str]:
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired:
        return -99, "", "TIMEOUT"
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    return proc.returncode, out, err


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def green(case: dict, code: int, out: str, err: str) -> bool:
    text = out + "\n" + err
    if case["kind"] == "gate":
        return code == 0
    if code != 0:
        return False
    # pytest 对照：退出码 0 且无收集错误痕迹（少量 known 噪声不影响判断）
    return not re.search(r"\d+ errors?\b", text) and "INTERNALERROR" not in text


def red(case: dict, code: int, out: str, err: str) -> tuple[bool, str]:
    text = out + "\n" + err
    if case["kind"] == "gate":
        ok = code != 0 and case["expect"] in text
        return ok, (f"exit={code}" + (f"，命中 {case['expect']!r}" if ok else "，未精确命中"))
    if re.search(r"\d+ errors?\b", text) or "INTERNALERROR" in text:
        return False, "出现 error（收集/语法错），不是干净的断言红"
    failed = re.search(r"(\d+) failed", text)
    if code != 0 and failed:
        return True, f"exit={code}，{failed.group(1)} failed"
    return False, f"exit={code}，未见 N failed"


def main() -> int:
    failures: list[str] = []
    files = sorted({case["file"] for case in CASES})
    originals: dict[str, bytes] = {}
    for rel in files:
        data = (ROOT / rel).read_bytes()
        originals[rel] = data
        print(f"备份 {REL(rel)}  md5={md5(data)}")

    print("\n== 第 0 步：对照跑（原始文件必须全绿） ==")
    for case in CASES:
        code, out, err = run(case["cmd"])
        ok = green(case, code, out, err)
        print(f"[{'绿' if ok else '红!'}] 对照 {case['name']}")
        if not ok:
            failures.append(f"对照不绿：{case['name']}（exit={code}）")

    if failures:
        print("\n!! 环境对照不绿，注入无意义，如实退出。")
        for item in failures:
            print("  -", item)
        return 1

    print("\n== 第 1 步：逐处注入 → 判红 → 还原核 md5 ==")
    try:
        for case in CASES:
            rel = case["file"]
            path = ROOT / rel
            data = originals[rel]
            text = data.decode("utf-8")
            count = text.count(case["old"])
            if count != 1:
                failures.append(f"{case['name']}：锚点出现 {count} 次（需恰好 1 次），未注入")
                print(f"[跳过] {case['name']}：锚点出现 {count} 次")
                continue
            path.write_bytes(text.replace(case["old"], case["new"]).encode("utf-8"))
            code, out, err = run(case["cmd"])
            if code == -99:
                print(f"[挂住] {case['name']}：超时（既不是红也不是绿，需单查判据）")
                failures.append(f"挂住：{case['name']}")
            else:
                ok, why = red(case, code, out, err)
                print(f"[{'红✓' if ok else '没红!'}] {case['name']}（{why}）")
                if not ok:
                    failures.append(f"没红：{case['name']}（{why}）")
            # 还原（无论结果如何），逐字节核对
            path.write_bytes(data)
            if md5((ROOT / rel).read_bytes()) != md5(data):
                failures.append(f"还原后 md5 不一致：{REL(rel)}")
                print(f"[还原失败!] {REL(rel)}")
            else:
                print(f"        还原 {REL(rel)} md5 一致")
    finally:
        print("\n== 收尾：还原所有文件（兜底） ==")
        for rel, data in originals.items():
            (ROOT / rel).write_bytes(data)
            print(f"  已写回 {REL(rel)} md5={md5(data)}")

    print("\n== 第 2 步：再对照（全部还原后必须全绿） ==")
    for case in CASES:
        code, out, err = run(case["cmd"])
        ok = green(case, code, out, err)
        print(f"[{'绿' if ok else '红!'}] 复查 {case['name']}")
        if not ok:
            failures.append(f"还原后仍红：{case['name']}（exit={code}）")

    print("\n" + "=" * 72)
    if failures:
        print(f"负向验证 {len(CASES)} 处，{len(failures)} 处未达预期：")
        for item in failures:
            print("  -", item)
        return 1
    print(f"负向验证 {len(CASES)} 处全部达成：对照绿 → 注入精确红 → 逐字节还原。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
