"""负向验证：全局参数写入、底栏三模块、原型目录共享治理的判据是否真会红。

每条用例都是「合法语法的精准放宽」：改一处实现，同时跑
  - ``tests/test_editor_globals.py``（后端语义 + 静态接线 + 治理）
  - ``scripts/check_editor_bottom_dock.mjs``（HTML/CSS 结构 + 真跑 DOM 绑定）
断言 **实际失败集 == 按推理写下的期望失败集**（不是 >=，也不是「有红就行」）。
末尾两条是元验证：把判据放宽后，同一处注入里那条判据必须变绿，证明报红的是它本身。

跑完逐字节还原所有目标文件并核 md5；任何异常都在 finally 里还原。
跑法：./.venv/Scripts/python.exe .harness/spike/negative_globals_dock.py
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
SCOPE = ".harness/branch-scope.json"
CHECK_ALL = ".harness/scripts/check_all.py"
TEST = "tests/test_editor_globals.py"  # pytest nodeid（cwd=APP）
TEST_REL = "apps/xhs-workbench/tests/test_editor_globals.py"  # 仓库相对路径，注入用
DOCK_SCRIPT = "scripts/check_editor_bottom_dock.mjs"
T = "tests/test_editor_globals.py::"

P_BRIDGE = T + "test_bridge_registers_save_flow_inputs"
P_ROUND = T + "test_save_flow_inputs_round_trips_all_json_types"
P_REJECT = T + "test_save_flow_inputs_rejects_invalid_values_without_writing"
P_SHA = T + "test_save_flow_inputs_honors_sha_lock"
P_IDEMPOTENT = T + "test_save_flow_inputs_is_idempotent"
P_WIRES = T + "test_frontend_wires_globals_panel_to_rpc"
P_GATE = T + "test_bottom_dock_check_is_wired_into_full_gate"
P_DIST = T + "test_dist_bundle_is_rebuilt_from_source"
P_SCOPE = T + "test_prototype_dir_is_shared_runtime"
P_RUNJS = T + "test_bottom_dock_check_script_passes"

N_HTML = "index.html 里三个模块各就各位，折叠面板都搬进底栏"
N_KEYS = "HTML 的 tab/panel key 与 main.js 的 DOCK_TABS 不漂移"
N_CSS = "版式在 style.css，main.js 不再运行时注入样式或 DOM"
N_BIND = "真跑绑定：三个 tab / panel 都接上，默认展示全局参数"
N_TABS = "tab 切换与折叠按钮真的改变状态"
N_ROWS = "渲染出行、增删行、非法值拦在保存前"
N_SAVE = "保存走 rpa.saveFlowInputs，values 是整份声明且按类型编码"
N_FAIL = "保存失败时保留编辑态与错误文案"


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
        "id": cid,
        "desc": desc,
        "rel": rel,
        "old": old,
        "new": new,
        "expect_pytest": expect_pytest,
        "expect_node": expect_node,
        "extra": list(extra),
        "green_pytest": green_pytest,
        "green_node": green_node,
    }


CASES = [
    # ---- 后端：接线、权威校验、乐观锁 ----
    case(
        "P1", "新 RPC 没注册进桥层（只改 api_method 名）", API,
        '@api_method("rpa.saveFlowInputs")', '@api_method("rpa.noSuchSave")',
        {P_BRIDGE, P_ROUND, P_REJECT, P_SHA, P_IDEMPOTENT, P_WIRES}, set(),
    ),
    case(
        "P2", "绕过 rpa_core.model.inputs 的权威声明校验", API,
        "        declaration = declaration_from_entries(entries)",
        # 把 JSON 文本原样当值：名称不校验、默认值不解析，语法合法且不会抛异常。
        "        declaration = dict(values)",
        {P_ROUND, P_REJECT, P_WIRES}, set(),
    ),
    case(
        "P3", "sha 乐观锁被摘掉", API,
        '    if entry is not None and entry["sha"] != _sha256(raw):\n'
        '        return reject("FLOW_CHANGED", "文件在打开后被其他程序改过，已拒绝覆盖（重新打开流程再改）")',
        '    if False:\n'
        '        return reject("FLOW_CHANGED", "文件在打开后被其他程序改过，已拒绝覆盖（重新打开流程再改）")',
        {P_SHA}, set(),
    ),
    # ---- 前端：RPC、HTML 结构、CSS 版式 ----
    case(
        "P4", "保存按钮改调一个不存在的 RPC", JS,
        "    const res = await ppx.call('rpa.saveFlowInputs', { path: currentPath, values })",
        "    const res = await ppx.call('rpa.noSuchRpc', { path: currentPath, values })",
        {P_WIRES, P_DIST, P_RUNJS}, {N_SAVE, N_FAIL},
    ),
    case(
        "P5", "HTML 的 tab key 与 DOCK_TABS 漂移", HTML,
        'data-tab="globals"', 'data-tab="global"',
        {P_WIRES, P_RUNJS}, {N_HTML, N_KEYS, N_BIND, N_TABS, N_ROWS},
    ),
    case(
        "P6", "style.css 里两行栅格被摘掉", CSS,
        "  grid-template-rows: minmax(0, 1fr) auto;",
        "  /* grid-template-rows 被摘掉 */",
        {P_WIRES, P_RUNJS}, {N_CSS},
    ),
    # ---- 接线与治理 ----
    case(
        "P7", "摘掉 check_all.py 里的底栏判据接线", CHECK_ALL,
        '    "check_editor_bottom_dock.mjs",',
        '    # 判据接线被摘掉',
        {P_GATE}, set(),
    ),
    case(
        "P8", "原型目录退回 ppx-only 独占（治理放宽作废）", SCOPE,
        '    ".harness/demo/ppx_probe/"\n  ],',
        '    "bin/rpa-skill.mjs"\n  ],',
        {P_SCOPE}, set(),
    ),
]


def md5(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_pytest(args: list[str]) -> tuple[set[str], str, int]:
    try:
        done = subprocess.run(
            # 不显式传 -q：pyproject 的 addopts 已带一次，再传一次等于 -qq，
            # 会吞掉「N passed / N failed」汇总行，环境级非零就没法判了。
            [str(PY), "-m", "pytest", *args, "--tb=no", "-p", "no:cacheprovider", "-rf"],
            cwd=APP,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
        )
    except subprocess.TimeoutExpired:
        return set(), "TIMEOUT: pytest 挂住超过 240s", -1
    out = (done.stdout or "") + (done.stderr or "")
    failed: set[str] = set()
    for line in out.splitlines():
        hit = re.match(r"^FAILED (\S+)", line.strip())
        if hit:
            nodeid = hit.group(1).replace("\\", "/")
            failed.add(re.sub(r"\[.*\]$", "", nodeid))  # 参数化 id 归一到函数名
    return failed, out, done.returncode


def run_node() -> tuple[set[str], str, int]:
    if not NODE:
        return set(), "SKIP: 本机没有 node", 0
    try:
        done = subprocess.run(
            [NODE, str(ROOT / DOCK_SCRIPT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return set(), "TIMEOUT: node 挂住超过 180s", -1
    out = (done.stdout or "") + (done.stderr or "")
    failed = {
        line[5:].split(" — ")[0].strip()
        for line in out.splitlines()
        if line.startswith("FAIL ")
    }
    return failed, out, done.returncode


def pytest_clean(failed: set[str], rc: int, out: str, expect: set[str]) -> bool:
    counts = re.search(r"(\d+) failed", out)
    # failed 集合把参数化 id 归一成了函数名（5 条参数化 → 1 个名字），
    # 但汇总行的数字是展开后的条数；拿 len(failed) 去比会误判成环境级非零。
    raw_fail_lines = len(re.findall(r"^FAILED ", out, re.MULTILINE))
    has_error = re.search(r"\d+ error", out)
    has_passed = re.search(r"[1-9]\d* passed", out)
    if expect:
        return (
            rc != 0
            and counts is not None
            and int(counts.group(1)) == raw_fail_lines
            and has_error is None
            and has_passed is not None
        )
    return rc == 0 and not failed and has_error is None and has_passed is not None


def node_clean(failed: set[str], rc: int, out: str, expect: set[str]) -> bool:
    mark = "EDITOR BOTTOM DOCK CHECK FAILED" if expect else "EDITOR BOTTOM DOCK CHECK PASSED"
    return (rc != 0 if expect else rc == 0) and mark in out and len(failed) == len(expect)


def main() -> int:
    if not NODE:
        print("MISSING node：先确保 node 在 PATH 里")
        return 2
    dist = next(
        (
            p for p in sorted((ROOT / ".harness/demo/ppx_probe/app/gui/dist/assets").glob("*.js"))
            if "rpa.saveFlowInputs" in p.read_text(encoding="utf-8")
        ),
        None,
    )
    if dist is None:
        print("MISSING dist/assets/*.js：先 npm run build 再跑负向验证")
        return 2
    dist_rel = dist.relative_to(ROOT).as_posix()
    p1 = next(c for c in CASES if c["id"] == "P1")
    p5 = next(c for c in CASES if c["id"] == "P5")
    cases = [
        *CASES,
        # 产物层：改了 src 忘了 build（dist 是 gitignore 产物，md5 照样还原）
        case(
            "F9", "dist 产物里没有新 RPC", dist_rel,
            "rpa.saveFlowInputs", "rpa.noSuchRpc", {P_DIST}, set(),
        ),
        # 元验证：判据放宽后，同一处注入里那条判据必须变绿。
        case(
            "M1", "放宽桥层注册判据后，RPC 未注册不再报红（元验证）", TEST_REL,
            '    assert "rpa.saveFlowInputs" in pb._registry()',
            '    assert True  # 元验证放宽',
            set(), set(),
            extra=((p1["rel"], p1["old"], p1["new"]),), green_pytest=P_BRIDGE,
        ),
        case(
            "M2", "放宽 HTML/JS key 对齐判据后，tab 漂移不再报红（元验证）", DOCK_SCRIPT,
            "  assert(JSON.stringify(htmlTabs) === JSON.stringify(jsKeys), 'tab key 与 DOCK_TABS 不一致')",
            "  assert(true, 'tab key 与 DOCK_TABS 不一致')",
            set(), set(),
            extra=((p5["rel"], p5["old"], p5["new"]),), green_node=N_KEYS,
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

    # 哨兵 + 锚点唯一性自检：上一轮崩在半路会把注入态留在源码里。
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
                print(f"RESTORE FAILED: {rel} 与基线 md5 不一致")
                bad += 1

    print(f"\nNEGATIVE VERIFY {'PASSED' if bad == 0 else 'FAILED'} ({len(cases) - bad}/{len(cases)})")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
