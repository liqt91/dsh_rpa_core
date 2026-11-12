"""负向验证：元素资产库（预置入库 / 详情 / 删除护栏 / 重捕 mtime）的判据是否真会红。

每条用例都是「合法语法的精准放宽」：改一处实现，同时跑
  - ``tests/test_editor_element_library.py``（后端语义 + 静态接线 + 门禁接线 + 产物）
  - ``scripts/check_element_library.mjs``（HTML/CSS 结构 + 最小 DOM shim 真跑按钮）
断言 **实际失败集 == 按推理写下的期望失败集**（不是 >=，也不是「有红就行」）。
末尾三条是元验证：把判据放宽后，同一处注入里那条判据必须变绿。

跑完逐字节还原所有目标文件并核 sha256；任何异常都在 finally 里还原。
跑法：./.venv/Scripts/python.exe .harness/spike/negative_element_library.py
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps" / "xhs-workbench"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
NODE = shutil.which("node")

API = ".harness/demo/ppx_probe/app/api/api.py"
JS = ".harness/demo/ppx_probe/app/gui/src/main.js"
HTML = ".harness/demo/ppx_probe/app/gui/index.html"
CSS = ".harness/demo/ppx_probe/app/gui/src/style.css"
CHECK_ALL = ".harness/scripts/check_all.py"
TEST = "tests/test_editor_element_library.py"  # pytest nodeid（cwd=APP）
TEST_REL = "apps/xhs-workbench/tests/test_editor_element_library.py"  # 注入用仓库相对路径
UI_SCRIPT = "scripts/check_element_library.mjs"
T = "tests/test_editor_element_library.py::"

P_BRIDGE = T + "test_bridge_registers_asset_library_rpcs"
P_PRESET = T + "test_write_element_creates_unbound_preset_asset"
P_DETAIL = T + "test_element_detail_returns_full_descriptor"
P_BRANCH = T + "test_element_detail_walks_all_real_ast_branches"
P_BAD_DETAIL = T + "test_detail_rejects_bad_names"
P_DETAIL_ERR = T + "test_detail_reports_missing_and_broken_json"
P_DELETE_GUARD = T + "test_delete_refuses_referenced_asset_without_unlink"
P_DELETE_OK = T + "test_delete_removes_unreferenced_asset"
P_BAD_DELETE = T + "test_delete_rejects_bad_names"
P_MTIME = T + "test_recapture_needs_a_fresh_write_not_just_a_file"
P_WIRES = T + "test_frontend_wires_constant_entry_and_asset_actions"
P_GATE = T + "test_ui_check_is_wired_into_full_gate"
P_RUNJS = T + "test_ui_check_really_runs"
P_DIST = T + "test_dist_bundle_is_rebuilt_from_source"

N_STRUCT = "工具条 6 个控件在 index.html，且在 elemBody 之外"
N_CSS = "版式在 style.css，main.js 没有运行时注入 DOM 或样式"
N_WIRES = "main.js 真接了工具条与三个资产 RPC"
N_WRITE = "手填写选择器真发 rpa.writeElement，成功后列表出现新资产"


def case(
    cid: str,
    desc: str,
    rel: str,
    old: str,
    new: str,
    expect_pytest: set[str],
    expect_node: set[str],
    *,
    extra: tuple[tuple[str, str, str], ...] = (),
    green_pytest: str | None = None,
    green_node: str | None = None,
) -> dict[str, Any]:
    return {
        "id": cid, "desc": desc, "rel": rel, "old": old, "new": new,
        "expect_pytest": expect_pytest, "expect_node": expect_node,
        "extra": list(extra), "green_pytest": green_pytest, "green_node": green_node,
    }


CASES = [
    # ---- 后端：注册、名字口径、删除护栏、AST 递归、重捕判据 ----
    case(
        "P1", "rpa.elementDetail 没注册进桥层", API,
        '@api_method("rpa.elementDetail")', '@api_method("rpa.noSuchDetail")',
        {P_BRIDGE, P_DETAIL, P_BRANCH, P_BAD_DETAIL, P_DETAIL_ERR}, set(),
    ),
    case(
        "P2", "资产名校验被放宽成恒真（路径穿越也放行）", API,
        "    return bool(name and _ELEMENT_NAME_RE.fullmatch(name))",
        "    return True  # 放宽名校验",
        {P_BAD_DETAIL, P_BAD_DELETE}, set(),
    ),
    case(
        "P3", "删除护栏被摘掉（仍被引用的资产也删）", API,
        "    if referenced:", "    if False:",
        {P_DELETE_GUARD}, set(),
    ),
    case(
        "P4", "引用扫描只递归 children，控制流分支里的引用漏掉", API,
        "            hits.add(str(node[\"id\"]))\n        for key in _NESTED:",
        "            hits.add(str(node[\"id\"]))\n        for key in (\"children\",):",
        {P_BRANCH, P_DELETE_GUARD}, set(),
    ),
    case(
        "P5", "重捕判据退回只看文件在不在（旧资产立刻假成功）", API,
        "        captured = elem.is_file() and elem.stat().st_mtime_ns > entry.get(\"baselineMtimeNs\", 0)",
        "        captured = elem.is_file()  # 退回只看文件在不在",
        {P_MTIME}, set(),
    ),
    case(
        "P6", "预置资产被硬绑到节点（ref-only 契约作废）", API,
        '        "boundNodeId": (item or {}).get("nodeId"),',
        '        "boundNodeId": "root",',
        {P_PRESET}, set(),
    ),
    # ---- 前端：RPC、HTML 结构、CSS 版式 ----
    case(
        "P7", "常驻工具条改调一个不存在的写入 RPC", JS,
        "  if (!selector) {\n"
        "    showElemNote(pickText('assetManualBad'), true)\n"
        "    return\n"
        "  }\n"
        "  let res\n"
        "  try {\n"
        "    res = await ppx.call('rpa.writeElement', { path: currentPath, name, selector })",
        "  if (!selector) {\n"
        "    showElemNote(pickText('assetManualBad'), true)\n"
        "    return\n"
        "  }\n"
        "  let res\n"
        "  try {\n"
        "    res = await ppx.call('rpa.noSuchWrite', { path: currentPath, name, selector })",
        {P_RUNJS}, {N_WRITE},
    ),
    case(
        "P8", "HTML 工具条 id 漂移（按钮成了孤儿）", HTML,
        'id="elemNewCapture"', 'id="elemNewCaptureX"',
        {P_WIRES, P_RUNJS}, {N_STRUCT},
    ),
    case(
        "P10", "style.css 里 .elemTools 规则被改名（等值断言才拦得住）", CSS,
        ".elemTools {", ".assetTools {",
        {P_WIRES, P_RUNJS}, {N_CSS},
    ),
    # ---- 接线 ----
    case(
        "P9", "摘掉 check_all.py 里的资产库判据接线", CHECK_ALL,
        '    "check_element_library.mjs",',
        '    # 判据接线被摘掉',
        {P_GATE}, set(),
    ),
]


def md5(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_pytest(args: list[str]) -> tuple[set[str], str, int]:
    try:
        done = subprocess.run(
            # 不显式传 -q：pyproject addopts 已带一次，再传等于 -qq 会吞掉汇总行。
            [str(PY), "-m", "pytest", *args, "--tb=no", "-p", "no:cacheprovider", "-rf"],
            cwd=APP, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=240,
        )
    except subprocess.TimeoutExpired:
        return set(), "TIMEOUT: pytest 挂住超过 240s", -1
    out = (done.stdout or "") + (done.stderr or "")
    failed: set[str] = set()
    for line in out.splitlines():
        hit = re.match(r"^FAILED (\S+)", line.strip())
        if hit:
            failed.add(re.sub(r"\[.*\]$", "", hit.group(1).replace("\\", "/")))
    return failed, out, done.returncode


def run_node() -> tuple[set[str], str, int]:
    if not NODE:
        return set(), "SKIP: 本机没有 node", 0
    try:
        done = subprocess.run(
            [NODE, str(ROOT / UI_SCRIPT)], cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=180,
        )
    except subprocess.TimeoutExpired:
        return set(), "TIMEOUT: node 挂住超过 180s", -1
    out = (done.stdout or "") + (done.stderr or "")
    failed = {
        line[5:].split(" — ")[0].strip()
        for line in out.splitlines() if line.startswith("FAIL ")
    }
    return failed, out, done.returncode


def pytest_clean(failed: set[str], rc: int, out: str, expect: set[str]) -> bool:
    counts = re.search(r"(\d+) failed", out)
    raw_fail_lines = len(re.findall(r"^FAILED ", out, re.MULTILINE))
    has_error = re.search(r"\d+ error", out)
    has_passed = re.search(r"[1-9]\d* passed", out)
    if expect:
        return (
            rc != 0 and counts is not None
            and int(counts.group(1)) == raw_fail_lines
            and has_error is None and has_passed is not None
        )
    return rc == 0 and not failed and has_error is None and has_passed is not None


def node_clean(failed: set[str], rc: int, out: str, expect: set[str]) -> bool:
    mark = "ELEMENT LIBRARY CHECK FAILED" if expect else "ELEMENT LIBRARY CHECK PASSED"
    return (rc != 0 if expect else rc == 0) and mark in out and len(failed) == len(expect)


def main() -> int:
    if not NODE:
        print("MISSING node：先确保 node 在 PATH 里")
        return 2
    dist = next(
        (
            p for p in sorted((ROOT / ".harness/demo/ppx_probe/app/gui/dist/assets").glob("*.js"))
            if "rpa.elementDetail" in p.read_text(encoding="utf-8")
        ),
        None,
    )
    if dist is None:
        print("MISSING dist/assets/*.js：先重建前端产物再跑负向验证")
        return 2
    dist_rel = dist.relative_to(ROOT).as_posix()
    p1 = next(c for c in CASES if c["id"] == "P1")
    p8 = next(c for c in CASES if c["id"] == "P8")
    p9 = next(c for c in CASES if c["id"] == "P9")
    cases = [
        *CASES,
        case(
            "F11", "dist 产物里没有新 RPC（改了源码忘了 build）", dist_rel,
            "rpa.elementDetail", "rpa.noSuchDetail", {P_DIST}, set(),
        ),
        case(
            "M1", "放宽桥层注册判据后，RPC 未注册不再报红（元验证）", TEST_REL,
            '    for name in ("rpa.elementDetail", "rpa.deleteElement"):\n'
            '        assert name in registry, f"{name} 没注册进桥层"\n'
            '        assert callable(registry[name]), f"{name} 不是可调用的处理函数"',
            '    for name in ("rpa.elementDetail", "rpa.deleteElement"):\n'
            '        assert True  # 元验证放宽',
            set(), set(),
            extra=((p1["rel"], p1["old"], p1["new"]),), green_pytest=P_BRIDGE,
        ),
        case(
            "M2", "放宽门禁接线判据后，摘掉接线不再报红（元验证）", TEST_REL,
            '    hits = CHECK_ALL.read_text(encoding="utf-8").count(\'"check_element_library.mjs"\')',
            '    hits = 1  # 元验证放宽',
            set(), set(),
            extra=((p9["rel"], p9["old"], p9["new"]),), green_pytest=P_GATE,
        ),
        case(
            "M3", "放宽 HTML 结构判据后，工具条 id 漂移不再报红（元验证）", UI_SCRIPT,
            "check('工具条 6 个控件在 index.html，且在 elemBody 之外', () => {",
            "check('工具条 6 个控件在 index.html，且在 elemBody 之外', () => {\n  if (true) return",
            set(), set(),
            extra=((p8["rel"], p8["old"], p8["new"]),), green_node=N_STRUCT,
        ),
    ]

    injections = [(c["rel"], c["old"], c["new"]) for c in cases]
    for c in cases:
        injections.extend(c["extra"])
    paths = {rel: ROOT / rel for rel, _, _ in injections}
    for rel, path in paths.items():
        if not path.is_file():
            print(f"MISSING {rel}")
            return 2
    originals = {rel: path.read_bytes() for rel, path in paths.items()}
    digests = {rel: md5(data) for rel, data in originals.items()}

    for c in cases:
        for rel, old, new in [(c["rel"], c["old"], c["new"]), *c["extra"]]:
            text = originals[rel].decode("utf-8")
            if text.count(new) > 0:
                print(f"SENTINEL: {rel} 里残留注入态（{c['id']}），先还原再跑")
                return 2
            if text.count(old) != 1:
                print(f"ANCHOR: {c['id']} 的 old 在 {rel} 命中 {text.count(old)} 次，须恰好 1 次")
                return 2

    bad = 0
    try:
        for c in cases:
            todo = [(c["rel"], c["old"], c["new"]), *c["extra"]]
            texts = {rel: originals[rel].decode("utf-8") for rel, _, _ in todo}
            try:
                for rel, old, new in todo:
                    (ROOT / rel).write_bytes(texts[rel].replace(old, new, 1).encode("utf-8"))
                pf, pout, prc = run_pytest([TEST])
                nf, nout, nrc = run_node()

                if c["green_pytest"] or c["green_node"]:
                    ok = True
                    detail = []
                    if c["green_pytest"]:
                        gf, gout, grc = run_pytest([c["green_pytest"]])
                        ok &= grc == 0 and not gf
                        detail.append(f"单跑 {c['green_pytest'].split('::')[-1]} rc={grc} failed={sorted(gf)}")
                    if c["green_node"]:
                        ok &= c["green_node"] not in nf
                        detail.append(f"{c['green_node']} 不在失败集={c['green_node'] not in nf}")
                    print(f"[{c['id']}] {'GREEN-OK' if ok else 'BAD'} {c['desc']}")
                    for line in detail:
                        print("      " + line)
                else:
                    pc = pytest_clean(pf, prc, pout, c["expect_pytest"])
                    nc = node_clean(nf, nrc, nout, c["expect_node"])
                    ok = pc and nc and pf == c["expect_pytest"] and nf == c["expect_node"]
                    print(f"[{c['id']}] {'RED-OK' if ok else 'BAD'} {c['desc']}")
                    print(f"      pytest rc={prc} clean={pc} expect={sorted(c['expect_pytest'])}")
                    print(f"      pytest actual={sorted(pf)}")
                    print(f"      node   rc={nrc} clean={nc} expect={sorted(c['expect_node'])}")
                    print(f"      node   actual={sorted(nf)}")
                    if not pc:
                        print(f"      pytest tail={pout.strip()[-500:]}")
                    if not nc:
                        print(f"      node tail={nout.strip()[-500:]}")
                if not ok:
                    bad += 1
            finally:
                for rel in texts:
                    (ROOT / rel).write_bytes(originals[rel])
    finally:
        for rel, path in paths.items():
            path.write_bytes(originals[rel])
        for rel, path in paths.items():
            if md5(path.read_bytes()) != digests[rel]:
                print(f"RESTORE FAILED: {rel} 与基线 sha256 不一致")
                bad += 1

    print(f"\nNEGATIVE VERIFY {'PASSED' if bad == 0 else 'FAILED'} ({len(cases) - bad}/{len(cases)})")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
