"""负向验证：元素捕获改版（M68）的三条判据是否真会红。

每条用例都是「合法语法的精准放宽」：改一处实现，同时跑三条判据——
  - ``scripts/check_element_pick_ui.mjs``（文案 / 样式 / 结构 / 渲染 / 契约）
  - ``scripts/check_element_library.mjs``（清单结构与详情、删除行为）
  - ``.harness/scripts/check_element_pick_contract.py``（后端动态契约）
断言 **实际失败集 == 按推理写下的期望失败集**（不是「有红就行」，也不是 `>=`）。
末尾一条是元验证：把判据本身放宽后，同一处注入必须**变绿**——证明那条红真是它报的。

跑完逐字节还原所有目标文件并核 sha256；任何异常都在 finally 里还原。
跑法：./.venv/Scripts/python.exe .harness/spike/negative_element_library.py
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SENTINEL = "NV-M68-INJECTED"

API = ".harness/demo/ppx_probe/app/api/api.py"
JS = ".harness/demo/ppx_probe/app/gui/src/main.js"
HTML = ".harness/demo/ppx_probe/app/gui/index.html"
PICK_UI = "scripts/check_element_pick_ui.mjs"
LIBRARY = "scripts/check_element_library.mjs"
CONTRACT = ".harness/scripts/check_element_pick_contract.py"

# 判据里的用例名（原样抄自脚本输出，改了那边这里必须同步，否则期望集会永远对不上）
UI_NO_NAME = "渲染层：点唯一的按钮就起捕获，不预填名字、不先探测"
UI_COMMIT = "渲染层：确认才入库，中文名照发"
UI_COMMIT_FAIL = "渲染层：入库失败把原因写在框里，不关框也不刷新"
UI_DISCARD = "渲染层：取消编辑框就丢弃这次捕获，不入库"
UI_STRUCT = "结构层：元素面板只有一个捕获按钮，没有预填名/选类型/写选择器"
UI_CONTRACT = "契约层：前端不再手写选择器、不再二次探测"
LIB_WIRES = "main.js 真接了清单与四个资产 RPC"
LIB_DELETE = "删除成功才刷新，失败保留行"
LIB_STRUCT = "工具区在 elemBody 之外、只有一个按钮"
PY_RENAME = "改名联动流程引用"
PY_BADNAME = "入库拒绝非法名与重名"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_gate(name: str) -> set[str]:
    if name == "pick_ui":
        cmd = ["node", PICK_UI]
    elif name == "library":
        cmd = ["node", LIBRARY]
    else:
        cmd = [str(PY), CONTRACT]
    done = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180,
    )
    failed = set()
    for line in done.stdout.splitlines():
        hit = re.match(r"^FAIL (.+?)(?: — .*)?$", line.strip())
        if hit:
            failed.add(hit.group(1).strip())
    if done.returncode != 0 and not failed:
        # 判据自己崩了也要能让期望集对不上，而不是静默当作「没红」。
        failed.add(f"<崩溃 rc={done.returncode}>")
        sys.stderr.write(done.stdout[-2000:] + done.stderr[-2000:])
    return failed


def apply(rel: str, old: str, new: str) -> tuple[Path, bytes]:
    path = ROOT / rel
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    count = text.count(old)
    if count != 1:
        raise AssertionError(f"锚点在 {rel} 里命中 {count} 次（必须恰好 1）")
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    return path, raw


def case(cid, desc, rel, old, new, expect, *, extra=(), green=None):
    return {
        "id": cid, "desc": desc, "rel": rel, "old": old, "new": new,
        "expect": expect, "extra": list(extra), "green": green,
    }


CASES: list[dict[str, Any]] = [
    case(
        "N1", "按钮替前端编了一个名字（又变成「先填名字再抓」）", JS,
        "el.elemCapture?.addEventListener('click', () => startCapture(null))",
        "el.elemCapture?.addEventListener('click', () => startCapture('userField'))",
        {"pick_ui": {UI_NO_NAME}, "library": set(), "contract": set()},
    ),
    case(
        "N2", "确认入库改调一个不存在的 RPC", JS,
        "ppx.call('rpa.commitElement'",
        "ppx.call('rpa.noSuchCommit'",
        {
            "pick_ui": {UI_COMMIT, UI_COMMIT_FAIL, UI_CONTRACT},
            "library": {LIB_WIRES},
            "contract": set(),
        },
    ),
    case(
        "N3", "取消编辑框不再丢弃这次捕获（暂存泄漏）", JS,
        "    discard: () => ppx.call('rpa.discardCapture', { path: currentPath, captureId }),\n",
        "",
        # 删掉这行等于 main.js 里不再出现 discardCapture：行为判据与契约判据都会红。
        {"pick_ui": {UI_DISCARD, UI_CONTRACT}, "library": set(), "contract": set()},
    ),
    case(
        "N4", "删除失败也刷新（行会被刷掉，用户以为删成了）", JS,
        "  if (!res || !res.ok) return\n  await refreshElements()",
        "  if (false) return\n  await refreshElements()",
        {"pick_ui": set(), "library": {LIB_DELETE}, "contract": set()},
    ),
    case(
        "N5", "元素面板又长出第二个按钮", HTML,
        '<button type="button" id="elemCapture" class="elemCapture">捕获元素</button>',
        '<button type="button" id="elemCapture" class="elemCapture">捕获元素</button>'
        '<button type="button" id="elemCapture2" class="elemCapture">手写</button>',
        {"pick_ui": {UI_STRUCT}, "library": {LIB_STRUCT}, "contract": set()},
    ),
    case(
        "N6", "改名不再联动流程引用（资产与节点脱钩）", API,
        "    target = _flow_path(path)\n    if not target.is_file():\n        return []",
        "    return []  # " + SENTINEL + "\n    target = _flow_path(path)\n"
        "    if not target.is_file():\n        return []",
        {"pick_ui": set(), "library": set(), "contract": {PY_RENAME}},
    ),
    case(
        "N7", "名字规则退回「什么都放行」（路径穿越能写出去）", API,
        "    if any(ch in _NAME_BAD_CHARS or ord(ch) < 32 for ch in name):\n        return False",
        "    if False:  # " + SENTINEL + "\n        return False",
        {"pick_ui": set(), "library": set(), "contract": {PY_BADNAME}},
    ),
]

# 元验证：同一处注入 + 把判据本身放宽 ⇒ 必须变绿（证明这条红是那条判据报的）
META = case(
    "M1", "注入 N1 的同时放宽「不预填名字」判据 ⇒ 应变绿", JS,
    "el.elemCapture?.addEventListener('click', () => startCapture(null))",
    "el.elemCapture?.addEventListener('click', () => startCapture('userField'))",
    {"pick_ui": set(), "library": set(), "contract": set()},
    extra=(
        (
            PICK_UI,
            "  assert(started[0].args.name == null, `还要求预填名字：${JSON.stringify(started[0].args)}`)",
            "  // 放宽：不再断言名字为空",
        ),
    ),
)


def run_case(item: dict[str, Any]) -> list[str]:
    touched: list[tuple[Path, bytes]] = []
    problems: list[str] = []
    try:
        touched.append(apply(item["rel"], item["old"], item["new"]))
        for rel, old, new in item["extra"]:
            touched.append(apply(rel, old, new))
        for gate in ("pick_ui", "library", "contract"):
            actual = run_gate(gate)
            want = item["expect"][gate]
            if actual != want:
                problems.append(
                    f"  {gate}: 期望红 {sorted(want)} / 实际红 {sorted(actual)}"
                )
    finally:
        for path, raw in reversed(touched):
            path.write_bytes(raw)
    return problems


def verify_clean() -> list[str]:
    bad = []
    for rel in (API, JS):
        if SENTINEL in (ROOT / rel).read_text(encoding="utf-8"):
            bad.append(f"哨兵残留：{rel}")
    if "startCapture('userField')" in (ROOT / JS).read_text(encoding="utf-8"):
        bad.append(f"注入没还原：{JS}")
    return bad


def main() -> int:
    if any(SENTINEL in (ROOT / rel).read_text(encoding="utf-8") for rel in (API, JS)):
        print("源码里已有哨兵，拒绝运行（上次注入没还原干净）")
        return 2
    before = {rel: digest(ROOT / rel) for rel in (API, JS, HTML, PICK_UI, LIBRARY, CONTRACT)}
    print("基线:", {k: v[:8] for k, v in before.items()})

    failures = 0
    for item in [*CASES, META]:
        problems = run_case(item)
        if problems:
            failures += 1
            print(f"FAIL {item['id']} {item['desc']}")
            for line in problems:
                print(line)
        else:
            print(f"GREEN (negative verified) {item['id']} {item['desc']}")

    after = {rel: digest(ROOT / rel) for rel in before}
    if before != after:
        failures += 1
        print("FAIL 还原不彻底：")
        for rel in before:
            if before[rel] != after[rel]:
                print(f"  {rel} {before[rel][:8]} -> {after[rel][:8]}")
    for note in verify_clean():
        failures += 1
        print(f"FAIL {note}")
    print(f"NEGATIVE VERIFICATION {'FAILED' if failures else 'PASSED'} "
          f"({len(CASES) + 1 - failures}/{len(CASES) + 1})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
