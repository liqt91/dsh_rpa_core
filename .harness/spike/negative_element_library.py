"""负向验证：元素捕获/编辑判据（M68 → M71）是否真会红。

每条用例都是「合法语法的精准放宽」：改一处实现，同时跑四条判据——
  - ``scripts/check_element_pick_ui.mjs``（文案 / 样式 / 结构 / 渲染 / 契约）
  - ``scripts/check_element_library.mjs``（清单结构与详情、删除行为、Esc 行为）
  - ``.harness/scripts/check_element_pick_contract.py``（后端动态契约）
  - ``tests/unit/test_capture_kit.py``（kit 状态机与浮窗 Esc 定时器链路）
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
FLOAT = "src/rpa_core/capture_kit/float_window.py"
PICK_UI = "scripts/check_element_pick_ui.mjs"
LIBRARY = "scripts/check_element_library.mjs"
CONTRACT = ".harness/scripts/check_element_pick_contract.py"
KIT_TESTS = "tests/unit/test_capture_kit.py"

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
PY_EDIT_DIALOG = "详情走桌面编辑框：改名/重名/取消"
UI_COMMITTED = "渲染层：桌面确认入库（committed）刷新资产并收尾"
UI_DETAIL_DESKTOP = "渲染层：详情优先弹桌面编辑框，页面框只是兜底"
# M71 新增
LIB_REF_CHECK = "删前检查引用：查出引用就不发删除，说明框能退出"
LIB_ESC = "捕获中按 Esc 取消会话，桌面编辑态不抢"
UI_KIT_BOUNDARY = "契约层：删前引用检查在网页端，删除不借桌面浮窗"
UI_ESC = "契约层：捕获期 Esc 接取消，编辑态不抢桌面编辑框"
PY_DELETE_GUARD = "删除被引用：回引用节点、不弹桌面浮窗"
KIT_ESC_TIMER = "test_float_timer_drives_escape_poll"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_gate(name: str) -> set[str]:
    if name == "pick_ui":
        cmd = ["node", PICK_UI]
    elif name == "library":
        cmd = ["node", LIBRARY]
    elif name == "unit":
        # 不用 --basetemp：这个文件的用例不碰 tmp_path，固定 basetemp 反而让
        # pytest 每次 rm_rf，容易撞上删除守卫。
        cmd = [str(PY), "-m", "pytest", KIT_TESTS, "-q", "--tb=no", "-ra"]
    else:
        cmd = [str(PY), CONTRACT]
    done = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180,
    )
    failed = set()
    for line in done.stdout.splitlines():
        pattern = (
            r"^FAILED \S+::(\w+)" if name == "unit"
            else r"^FAIL (.+?)(?: — .*)?$"
        )
        hit = re.match(pattern, line.strip())
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
    # 四条 gate 全跑、全等断言：没写进 expect 的判据也必须「不红」，
    # 才能同时抓住「该红的没红」和「不该红的被误伤」。
    gates = {"pick_ui": set(), "library": set(), "contract": set(), "unit": set()}
    gates.update(expect)
    return {
        "id": cid, "desc": desc, "rel": rel, "old": old, "new": new,
        "expect": gates, "extra": list(extra), "green": green,
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
        "    discard: () => ppx.call('rpa.discardCapture', { path: currentPath, capture_id: captureId }),\n",
        "",
        # 删掉这行等于 main.js 里不再出现 discardCapture：行为判据与契约判据都会红。
        {"pick_ui": {UI_DISCARD, UI_CONTRACT}, "library": set(), "contract": set()},
    ),
    case(
        "N4", "删除失败也刷新（行会被刷掉，用户以为删成了）", JS,
        "  if (!res || !res.ok) {\n    // 服务端护栏还在",
        "  if (false) {  // " + SENTINEL + "\n    // 服务端护栏还在",
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
        # _rewrite_refs 是 RPC 改名与桌面详情框改名的共用权威：注入后两条用例都得红。
        {"pick_ui": set(), "library": set(), "contract": {PY_RENAME, PY_EDIT_DIALOG}},
    ),
    case(
        "N7", "名字规则退回「什么都放行」（路径穿越能写出去）", API,
        "    if any(ch in _NAME_BAD_CHARS or ord(ch) < 32 for ch in name):\n        return False",
        "    if False:  # " + SENTINEL + "\n        return False",
        {"pick_ui": set(), "library": set(), "contract": {PY_BADNAME}},
    ),
    case(
        "N8", "桌面确认入库后不再刷新资产（用户看不见新元素）", JS,
        "      endCapture()\n      await refreshElements()\n      return\n    }",
        "      endCapture()  // " + SENTINEL + "\n      return\n    }",
        # committed 分支同时服务捕获确认与详情改名：删掉刷新，两条用例都得红。
        {"pick_ui": {UI_COMMITTED, UI_DETAIL_DESKTOP}, "library": set(), "contract": set()},
    ),
    case(
        "N9", "详情不走桌面编辑框（退回纯页面弹窗）", JS,
        "    if (edit && edit.ok && edit.state === 'editing') {",
        "    if (false) {  // " + SENTINEL,
        {"pick_ui": {UI_DETAIL_DESKTOP}, "library": set(), "contract": set()},
    ),
    # ---- M71：kit 边界（只管捕获/编辑）+ 捕获期 Esc -------------------------
    case(
        "N10", "删前不查引用（直接发删除，被引用的资产会被抹掉）", JS,
        "  const refs = (detail && detail.referencedBy) || []",
        "  const refs = []  // " + SENTINEL,
        {"library": {LIB_REF_CHECK}},
    ),
    case(
        "N11", "查到引用仍发删除 RPC（网页端检查形同虚设）", JS,
        "  if (refs.length) {\n    showDeleteProblem(name, refs)\n    return\n  }",
        "  if (false) {  // " + SENTINEL
        + "\n    showDeleteProblem(name, refs)\n    return\n  }",
        # 静态判据钉了源码里的 refs.length 拦截；换成恒假后它也该红。
        {"pick_ui": {UI_KIT_BOUNDARY}, "library": {LIB_REF_CHECK}},
    ),
    case(
        "N12", "删除失败又弹桌面浮窗（那个关不掉的框回来了）", API,
        "    referenced = _element_references(path, name)\n"
        "    if referenced:\n        return {",
        "    referenced = _element_references(path, name)\n"
        "    if referenced:\n"
        '        _notify("删不掉", "还被引用着")  # ' + SENTINEL + "\n"
        "        return {",
        {"pick_ui": {UI_KIT_BOUNDARY}, "contract": {PY_DELETE_GUARD}},
    ),
    case(
        "N13", "捕获期 Esc 不再发取消（用户按 Esc 没反应）", JS,
        "    cancelCaptureByEsc()\n    return",
        "    // " + SENTINEL + "\n    return",
        {"pick_ui": {UI_ESC}, "library": {LIB_ESC}},
    ),
    case(
        "N14", "去掉 editing 守卫（桌面编辑框的 Esc 被网页端抢走）", JS,
        "  if (captureSession && capturePhase === 'pending' && !dialogMode) {",
        "  if (captureSession && !dialogMode) {  // " + SENTINEL,
        {"pick_ui": {UI_ESC}, "library": {LIB_ESC}},
    ),
    case(
        "N15", "说明框不设 dialogMode（Esc 关不掉，又是那个死框）", JS,
        "    mode: 'blocked',",
        "    mode: null,  // " + SENTINEL,
        {"library": {LIB_REF_CHECK}},
    ),
    case(
        "N16", "kit 定时器不再轮询 Esc（浮窗拿不到焦点，Esc 全链路失效）", FLOAT,
        "        if msg == win32con.WM_TIMER and wparam == _TIMER_AVOID:\n"
        "            self._avoid_cursor(hwnd)\n"
        "            self._poll_escape()\n"
        "            return 0\n",
        "        if msg == win32con.WM_TIMER and wparam == _TIMER_AVOID:\n"
        "            self._avoid_cursor(hwnd)\n"
        "            # " + SENTINEL + " 不轮询 Esc\n"
        "            return 0\n",
        {"unit": {KIT_ESC_TIMER}},
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
        for gate in ("pick_ui", "library", "contract", "unit"):
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
    for rel in (API, JS, FLOAT):
        if SENTINEL in (ROOT / rel).read_text(encoding="utf-8"):
            bad.append(f"哨兵残留：{rel}")
    if "startCapture('userField')" in (ROOT / JS).read_text(encoding="utf-8"):
        bad.append(f"注入没还原：{JS}")
    return bad


def main() -> int:
    targets = (API, JS, FLOAT)
    if any(SENTINEL in (ROOT / rel).read_text(encoding="utf-8") for rel in targets):
        print("源码里已有哨兵，拒绝运行（上次注入没还原干净）")
        return 2
    before = {
        rel: digest(ROOT / rel)
        for rel in (API, JS, HTML, FLOAT, KIT_TESTS, PICK_UI, LIBRARY, CONTRACT)
    }
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
