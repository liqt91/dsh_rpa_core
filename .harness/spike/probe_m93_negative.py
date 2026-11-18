"""M93 负向验证：元素库 P2（贴影刀单树 / 右键 / 批量）的判据必须真拦得住破坏。

注入都针对本轮新写的界面接线与落盘路径，期望失败集按推理写、不照抄实际：
1. 分组分桶恒 false      → UI 树渲染/折叠用例 + 契约接线红（元素全挤进未分组）；
2. 资产右键不接菜单      → UI 右键菜单用例红（菜单项消失）；
3. 折叠落盘变 no-op      → 折叠持久化 RPC 测试红（刷新后展开态回退）；
4. 多选勾选不进集合      → UI 多选批量用例红；
5. 前端折叠不落盘        → UI 折叠用例 + 契约接线红（只本地翻、刷新丢失）。

写回一律 write_bytes（write_text 会把 LF 转 CRLF），锚点要求唯一，结束核 md5。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
API = ROOT / ".harness/demo/ppx_probe/app/api/api.py"
MAIN = ROOT / ".harness/demo/ppx_probe/app/gui/src/main.js"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "M93-INJECT-SENTINEL"

WIRED = "元素库 P2：分组树/右键/批量接线"
UI_WIRED = "契约层：P2 管理 RPC 与树渲染函数都真接线"
BUCKET_TEST = "分组桶：按服务端顺序 + 未分组兜底，空分组保留、折叠态带出"
TREE_TEST = "树渲染：分组头与资产行同树，折叠组不渲染其子行"
FOLD_TEST = "点分组头折叠：先本地收起再落盘，失败回滚"
MENU_TEST = "资产右键菜单：详情/复制/移动/查引用/删除五项齐全，分组菜单有 CRUD"
MENU_EVENT_TEST = "资产行的右键事件真拉起菜单（不是只测 openLibMenu 本身）"
SEL_TEST = "多选批量：勾选进选中集、批量条浮出、取消选择清空"
COLLAPSE_PY = "test_group_collapse_persists_and_reflects_in_list"

INJECTIONS = [
    (
        "bucket-always-ungrouped", MAIN,
        "    elements: elements.filter((e) => e.group === g.id),\n",
        "    elements: elements.filter(() => false),  # " + SENTINEL + "\n",
        # 折叠用例的 group 是本地字面量（不经过 elementTreeBuckets），注入只让
        # 分桶变空，不会红折叠用例 —— 按推理的期望集不含 FOLD_TEST。
        ("ui", "contract"), {BUCKET_TEST, TREE_TEST, WIRED},
    ),
    (
        "asset-menu-not-wired", MAIN,
        "    openLibMenu(event, 'asset', e)\n",
        "    // " + SENTINEL + "\n",
        ("ui",), {MENU_EVENT_TEST},
    ),
    (
        "collapse-noop", API,
        '    group["collapsed"] = bool(collapsed)\n',
        '    group["collapsed"] = group.get("collapsed")  # ' + SENTINEL + "\n",
        ("library",), {COLLAPSE_PY},
    ),
    (
        "selection-noop", MAIN,
        "    if (pick.checked) elemSelected.add(e.name)\n",
        "    if (pick.checked) void 0  # " + SENTINEL + "\n",
        ("ui",), {SEL_TEST},
    ),
    (
        "fold-no-persist", MAIN,
        "  const res = await ppx.call('rpa.setElementGroupCollapsed', {\n"
        "    path: currentPath, group_id: group.id, collapsed,\n"
        "  })\n",
        "  const res = { ok: true }  # " + SENTINEL + "\n",
        ("ui", "contract"), {FOLD_TEST, WIRED, UI_WIRED},
    ),
]

RUNNERS = {
    "contract": [str(PY), ".harness/scripts/check_element_pick_contract.py"],
    "ui": ["node", "scripts/check_element_library.mjs"],
    "library": [str(PY), "-m", "pytest", "apps/xhs-workbench/tests/test_editor_element_library.py",
                "-q", "--basetemp=D:/Temp/pt_m93neg"],
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
        if which in ("contract", "ui") and line.startswith("FAIL "):
            names.add(line[5:].split(" — ")[0].strip())
        elif line.startswith("FAILED "):
            names.add(line.split("::")[-1].split()[0].strip())
    return names


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    paths = (API, MAIN)
    for path in paths:
        if SENTINEL in path.read_text(encoding="utf-8"):
            print(f"[FATAL] {path.name} 已有注入哨兵，先手工清理")
            return 2
    baselines = {p: md5(p) for p in paths}
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
        for name, path, old, new, runners, expected in INJECTIONS:
            original = path.read_text(encoding="utf-8")
            saved.setdefault(path, original.encode("utf-8"))
            if original.count(old) != 1:
                print(f"[FAIL] {name}: 锚点出现 {original.count(old)} 次（要求 1）")
                ok = False
                continue
            # JS 注入不能用 Python 的 # 注释，按文件类型换注释符。
            comment = "//" if path.suffix == ".js" else "#"
            new = new.replace("  # " + SENTINEL, "  " + comment + " " + SENTINEL)
            path.write_bytes(original.replace(old, new, 1).encode("utf-8"))
            got: set[str] = set()
            codes: list[int] = []
            tails: list[str] = []
            try:
                for which in runners:
                    bad = run(which)
                    codes.append(bad.returncode)
                    got |= failed_names(which, bad.stdout)
                    tails.append(
                        (bad.stdout.strip().splitlines() or [""])[-1]
                        + (" error" if " error" in bad.stdout else "")
                    )
            finally:
                path.write_bytes(original.encode("utf-8"))
            print(f"\n[{name}] rc={codes} 失败集={sorted(got)}")
            print(f"  期望={sorted(expected)}  {tails[-1][:80]}")
            if got != expected:
                print("  [FAIL] 失败集与期望不等")
                ok = False
            if any(code not in (0, 1) for code in codes) or any(
                " error" in tail for tail in tails
            ):
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
