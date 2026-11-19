"""M102 S1/S2 负向验证：元素缩略图（截→存→读）与左右两栏那批新判据。

做法同 M100/M101：对生产源码做一次**合法语法的精准放宽**，跑判据，断言
「实际失败集 == 期望失败集」，再按原字节写回并核 md5。

八条注入各自钉一个会静默退化的点：

* hash-len           文件名从 16 位缩到 8 位 ⇒ 内容寻址的键变了（改名/复制/重捕的去重失效）
* no-dedupe          同内容也重写 ⇒ mtime 每次都变（重捕即重写全量缩略图）
* traversal          读图不限目录 ⇒ screenshot 被手改成 ../../ 就是任意文件读取
* payload-prefix     去掉 data: 前缀 ⇒ <img src> 拿到裸 base64，图全裂
* row-act-visible    行内动作改回常显 ⇒ 删除重新变成随手可点
* meta-wraps         meta 改回折行 ⇒ 行高被撑开，一屏看不到几项
* thumb-eager        没有截图的资产也去拉图 ⇒ 每次刷新多发 N 个 RPC
* detail-render      右栏不渲染 ⇒ 点行没反应（截图与完整定位全看不到）
* commit-unwired     入库不写 screenshot ⇒ 图存了但资产里没有（接线层，不是纯函数层）

用法：``./.venv/Scripts/python.exe .harness/spike/probe_m102_negative.py``
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = sys.executable
SHOT = ROOT / "src/rpa_core/capture/screen_shot.py"
API = ROOT / ".harness/demo/ppx_probe/app/api/api.py"
MAIN_JS = ROOT / ".harness/demo/ppx_probe/app/gui/src/main.js"
CSS = ROOT / ".harness/demo/ppx_probe/app/gui/src/style.css"
SHOT_TESTS = ["tests/contract/test_element_shot.py"]
T = "tests/contract/test_element_shot.py::"
LIB_CHECK = "scripts/check_element_library.mjs"
WIRING = ".harness/spike/probe_m102_s1_wiring.py"
PPX_PY = ROOT / ".harness/demo/ppx_probe/.venv/Scripts/python.exe"

# 右栏那两条行为判据的完整名字（改 main.js 里 renderElementDetail 会一起红）
_R_DETAIL = "行为层：点一行把名字与**完整**定位放进右栏（不截断）"
_R_STALE = "行为层：资产没了右栏收敛，不挂着上一份详情"
_R_SHOT = "行为层：只有带截图的资产才去拉图，图回来真塞进 <img>"
_S_STYLE = "样式层：窄窗口回落单列、行内动作默认隐身、行是单行截断"

CASES = [
    {
        "name": "hash-len",
        "file": SHOT,
        "edits": [("_SHOT_HASH_LEN = 16", "_SHOT_HASH_LEN = 8")],
        "runner": "pytest",
        "expected": {T + "test_store_returns_a_relative_path_named_by_content_hash"},
    },
    {
        "name": "no-dedupe",
        "file": SHOT,
        "edits": [("        if not target.is_file():\n            target.write_bytes(bytes(png))",
                   "        if True:\n            target.write_bytes(bytes(png))")],
        "runner": "pytest",
        "expected": {T + "test_store_does_not_rewrite_identical_content"},
    },
    {
        "name": "traversal",
        "file": SHOT,
        "edits": [("    if root not in target.parents:", "    if False:")],
        "runner": "pytest",
        "expected": {T + "test_read_refuses_path_traversal"},
    },
    {
        "name": "payload-prefix",
        "file": SHOT,
        "edits": [('    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")',
                   '    return "" + base64.b64encode(png).decode("ascii")')],
        "runner": "pytest",
        "expected": {T + "test_payload_is_a_data_url_on_demand"},
    },
    {
        "name": "commit-unwired",
        "file": API,
        "edits": [('    if shot_path is not None:\n        doc["screenshot"] = shot_path',
                   '    if False:\n        doc["screenshot"] = shot_path')],
        "runner": "wiring",
        "expected": {"wiring"},
    },
    {
        "name": "row-act-visible",
        "file": CSS,
        "edits": [("  opacity: 0; transition: opacity .12s;", "  opacity: 1; transition: opacity .12s;")],
        "runner": "node",
        "expected": {_S_STYLE},
    },
    {
        "name": "meta-wraps",
        "file": CSS,
        "edits": [("  flex: 1 1 auto; order: 0; min-width: 0;\n  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;",
                   "  flex: 1 1 auto; order: 0; min-width: 0;\n  white-space: normal; overflow: hidden; text-overflow: ellipsis;")],
        "runner": "node",
        "expected": {_S_STYLE},
    },
    {
        "name": "thumb-eager",
        "file": MAIN_JS,
        "edits": [("  if (e.screenshot && !elemShotCache.has(key)) loadElementShot(e, key)",
                   "  if (!elemShotCache.has(key)) loadElementShot(e, key)")],
        "runner": "node",
        "expected": {_R_SHOT},
    },
    {
        "name": "detail-render",
        "file": MAIN_JS,
        "edits": [("function renderElementDetail() {\n  const pane = el.elemDetail\n  if (!pane) return",
                   "function renderElementDetail() {\n  const pane = el.elemDetail\n  if (pane) return\n  if (!pane) return")],
        "runner": "node",
        "expected": {_R_DETAIL, _R_STALE},
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
        match = re.match(r"FAIL\s+(.+?)(?:\s+—.*)?$", line.strip())
        if match:
            out.add(match.group(1))
    return out


def run_pytest() -> set[str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "--tb=no", "-rf", *SHOT_TESTS],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return fail_set_from_pytest(proc.stdout + proc.stderr)


def run_node() -> set[str]:
    proc = subprocess.run(
        ["node", LIB_CHECK], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return fail_set_from_node(proc.stdout + proc.stderr)


def run_wiring() -> set[str]:
    """接线探针：跑得通 = 空集，跑不通 = {"wiring"}。"""
    proc = subprocess.run(
        [str(PPX_PY), WIRING], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return set() if proc.returncode == 0 else {"wiring"}


RUNNERS = {"pytest": run_pytest, "node": run_node, "wiring": run_wiring}


def marker_for(path: pathlib.Path) -> str:
    """注入标记必须是**该语言合法**的注释：JS/CSS 里的 ``#`` 是语法错误，
    那会让整份 main.js 解析失败、连带压红全部渲染判据，看不出是哪条判据在拦。"""
    return "  # <<<M102-INJECTED>>>" if path.suffix == ".py" else "  /* <<<M102-INJECTED>>> */"


def main() -> int:
    for case in CASES:
        path = case["file"]
        text = path.read_text(encoding="utf-8")
        if "<<<M102-INJECTED>>>" in text:
            print(f"[哨兵] {path.name} 里还有上次的注入标记，拒绝运行")
            return 2
        for old, _new in case["edits"]:
            if text.count(old) != 1:
                print(f"[锚点] {case['name']}: 在 {path.name} 出现 {text.count(old)} 次，必须恰好 1 次")
                return 2

    problems: list[str] = []
    for case in CASES:
        path: pathlib.Path = case["file"]
        original = path.read_bytes()
        before = md5(path)
        marker = marker_for(path)
        try:
            text = original.decode("utf-8")
            for old, new in case["edits"]:
                text = text.replace(old, new + marker, 1)
            path.write_bytes(text.encode("utf-8"))
            got = RUNNERS[case["runner"]]()
            expected = case["expected"]
            if got != expected:
                diff = f"缺失(应红未红)={sorted(expected - got)} 多出(不该红)={sorted(got - expected)}"
                problems.append(f"{case['name']}: {diff}")
                print(f"FAIL {case['name']}: {diff}")
            else:
                print(f"OK   {case['name']}: 失败集相等（{len(got)} 条）")
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
    print("\nM102 NEGATIVE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
