"""M89 负向验证：元素编辑服务化后，五个新判据必须真的拦得住破坏。

注入都针对「这次新写的判据」，期望失败集按推理写、不照抄实际：
1. main.js 去掉 refCount 展示 → 元素库面板契约红（引用护栏看不见了）；
2. main.js 去掉 elementCompose 调用 → 编辑框契约红（前端要自己拼 css）；
3. api.py 去掉按参数键过滤 → 候选 RPC 测试红（面板能选、运行期换不进去）；
4. element_editing.py 清空保留名表 → 名字校验测试红（CON.json 能落盘）；
5. index.html 塞回旧的手写按钮 → 结构层断言红（旧入口回归）。

每注入跑一次只影响一个判据；所有写回走 write_bytes（write_text 会转 CRLF）。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
MAIN = ROOT / ".harness/demo/ppx_probe/app/gui/src/main.js"
INDEX = ROOT / ".harness/demo/ppx_probe/app/gui/index.html"
API = ROOT / ".harness/demo/ppx_probe/app/api/api.py"
EDITING = ROOT / "src/rpa_core/capture_kit/element_editing.py"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "# M89-INJECT-SENTINEL"

LIBRARY = "契约层：搜索/分组/引用数/全部校验都真接线"
COMPOSE = "契约层：编辑模型/重组/校验/重捕走语义 RPC，前端不拼 css"
STRUCT = "结构层：元素面板只有两个捕获按钮，管理按钮在白名单内"

INJECTIONS = [
    (
        "drop-refcount", MAIN,
        "  if (e.refCount) row.append(mk('span', 'elemRefs', `${e.refCount} 处引用`))\n",
        "  // " + SENTINEL + "\n",
        "library", {LIBRARY},
    ),
    (
        "drop-compose-rpc", MAIN,
        "    const res = await ppx.call('rpa.elementCompose', { levels: dialogLevels })\n",
        "    const res = { ok: true, selector: '#injected' }  // " + SENTINEL + "\n",
        "pick", {COMPOSE},
    ),
    (
        "drop-candidate-kind-filter", API,
        "    if wanted:\n        items = [e for e in items if e.get(\"kind\") == wanted]\n",
        "    # " + SENTINEL + "\n",
        "rpc", {"test_candidates_rpc_filters_by_param_key"},
    ),
    (
        "clear-reserved-names", EDITING,
        'RESERVED_NAMES = frozenset(\n    ["con", "prn", "aux", "nul"]\n'
        '    + [f"com{index}" for index in range(1, 10)]\n'
        '    + [f"lpt{index}" for index in range(1, 10)]\n)\n',
        "RESERVED_NAMES = frozenset()  " + SENTINEL + "\n",
        "editing", {"test_name_allows_chinese_and_rejects_disk_breaking_chars"},
    ),
    (
        "restore-old-write-button", INDEX,
        '            <div class="elemFilter">\n',
        '            <button type="button" id="elemNewWrite">写入</button>  '
        + SENTINEL + "\n" + '            <div class="elemFilter">\n',
        "pick", {STRUCT},
    ),
]

RUNNERS = {
    "library": ["node", "scripts/check_element_library.mjs"],
    "pick": ["node", "scripts/check_element_pick_ui.mjs"],
    "editing": [str(PY), "-m", "pytest", "tests/unit/test_element_editing.py", "-q",
                "--basetemp=D:/Temp/pt_m89neg"],
    "rpc": [str(PY), "-m", "pytest", "apps/xhs-workbench/tests/test_element_edit_rpc.py", "-q",
            "--basetemp=D:/Temp/pt_m89neg"],
}


def run(which: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({"TMPDIR": "D:/Temp", "TEMP": "D:/Temp", "TMP": "D:/Temp",
                "PYTHONPATH": str(ROOT / "src")})
    return subprocess.run(
        RUNNERS[which], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=600,
    )


def failed_names(which: str, output: str) -> set[str]:
    names: set[str] = set()
    for line in output.splitlines():
        if which in ("library", "pick") and line.startswith("FAIL "):
            names.add(line[5:].split(" — ")[0].strip())
        elif line.startswith("FAILED "):
            names.add(line.split("::")[-1].split()[0].strip())
    return names


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (MAIN, INDEX, API, EDITING):
        if SENTINEL in path.read_text(encoding="utf-8"):
            print(f"[FATAL] {path.name} 已有注入哨兵，先手工清理")
            return 2
    baselines = {p: md5(p) for p in (MAIN, INDEX, API, EDITING)}
    print("baseline md5:", {p.name: v[:10] for p, v in baselines.items()})

    ok = True
    for which in RUNNERS:
        base = run(which)
        tail = (base.stdout or base.stderr).strip().splitlines()[-1:]
        print(f"[baseline {which}] rc={base.returncode} {tail[0][:70] if tail else ''}")
        if base.returncode != 0:
            print("[FATAL] 基线不绿，负向验证无意义")
            return 2

    saved: dict[Path, bytes] = {}
    try:
        for name, path, old, new, which, expected in INJECTIONS:
            original = path.read_text(encoding="utf-8")
            saved.setdefault(path, original.encode("utf-8"))
            if original.count(old) != 1:
                print(f"[FAIL] {name}: 锚点出现 {original.count(old)} 次（要求 1）")
                ok = False
                continue
            path.write_bytes(original.replace(old, new, 1).encode("utf-8"))
            try:
                bad = run(which)
            finally:
                path.write_bytes(original.encode("utf-8"))
            got = failed_names(which, bad.stdout)
            tail = bad.stdout.strip().splitlines()[-1:] if bad.stdout.strip() else [""]
            print(f"\n[{name}] rc={bad.returncode} 失败集={sorted(got)}")
            print(f"  期望={sorted(expected)}  {tail[0][:80]}")
            if got != expected:
                print("  [FAIL] 失败集与期望不等")
                ok = False
            if " error" in bad.stdout or bad.returncode not in (0, 1):
                print("  [FAIL] 出现 error 或异常退出码（环境坏了，不是判据红）")
                ok = False
    finally:
        for path, raw in saved.items():
            path.write_bytes(raw)

    md5_ok = True
    for path, digest in baselines.items():
        after = md5(path)
        if after != digest:
            print(f"[FAIL] {path.name} 还原后 md5 不一致 {after} != {digest}")
            md5_ok = False
    print("所有文件 md5 逐字节还原一致" if md5_ok else "还原核对失败")
    ok &= md5_ok

    for which in RUNNERS:
        back = run(which)
        print(f"[restored {which}] rc={back.returncode}")
        ok &= back.returncode == 0
    print("\n" + ("ALL PASS" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
