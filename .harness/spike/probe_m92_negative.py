"""M92 负向验证：元素库 P1 的判据必须真的拦得住破坏。

注入都针对本轮新写的数据模型/RPC 判据，期望失败集按推理写、不照抄实际：
1. 删掉显式 group 字段     → 静态存储判据 + 模型 roundtrip 红；
2. 分组文件塞进 elements/  → 静态判据 + 分组 CRUD 红（假元素会混进清单）；
3. 移动元素只改内存不写盘  → 分组 CRUD 红（刷新后归属回退）；
4. 查找未使用恒真          → 复制/未使用/引用测试红；
5. 入库不补库字段          → 静态判据 + 新资产字段测试红。

写回一律 write_bytes（write_text 会把 LF 转 CRLF），锚点要求唯一，结束核 md5。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
API = ROOT / "apps/editor/api/api.py"
MODEL = ROOT / "src/rpa_core/model/capture.py"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "M92-INJECT-SENTINEL"

STORAGE = "元素库 P1：分组存储与显式元数据"
MODEL_TEST = "test_library_fields_survive_roundtrip"
GROUP_TEST = "test_element_groups_crud_and_move_persist"
UNUSED_TEST = "test_copy_unused_and_references_are_structured"
COMMIT_TEST = "test_commit_fills_library_fields_without_migrating_old_assets"

INJECTIONS = [
    (
        "drop-explicit-group", MODEL,
        "    group: str | None = None\n",
        "    # " + SENTINEL + "\n",
        ("contract", "model"), {STORAGE, MODEL_TEST},
    ),
    (
        "groups-file-inside-elements", API,
        '    return _flow_path(path).parent / "element_groups.json"\n',
        '    return _elements_dir(path) / "element_groups.json"  # ' + SENTINEL + "\n",
        ("contract", "library"), {STORAGE, GROUP_TEST},
    ),
    (
        "move-group-noop", API,
        "    for name in wanted:\n"
        "        element = existing[name]\n"
        "        element[\"group\"] = group_id\n",
        "    for name in wanted:\n"
        "        element = existing[name]\n"
        "        element[\"group\"] = element.get(\"group\")  # " + SENTINEL + "\n",
        ("library",), {GROUP_TEST},
    ),
    (
        "unused-always-true", API,
        "        if counts.get(name, 0) == 0\n",
        "        if True  # " + SENTINEL + "\n",
        ("library",), {UNUSED_TEST},
    ),
    (
        "commit-skips-library-fields", API,
        "    doc = dict(entry[\"captured\"] or {})\n"
        "    doc[\"name\"] = name\n"
        "    _fill_library_fields(doc)\n",
        "    doc = dict(entry[\"captured\"] or {})\n"
        "    doc[\"name\"] = name\n"
        "    # " + SENTINEL + "\n",
        ("contract", "library"), {STORAGE, COMMIT_TEST},
    ),
]

RUNNERS = {
    "contract": [str(PY), ".harness/scripts/check_element_pick_contract.py"],
    "library": [str(PY), "-m", "pytest", "apps/xhs-workbench/tests/test_editor_element_library.py",
                "-q", "--basetemp=D:/Temp/pt_m92neg"],
    "model": [str(PY), "-m", "pytest", "tests/unit/test_element_descriptor.py", "-q",
              "--basetemp=D:/Temp/pt_m92neg"],
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
        if which == "contract" and line.startswith("FAIL "):
            names.add(line[5:].split(" — ")[0].strip())
        elif line.startswith("FAILED "):
            names.add(line.split("::")[-1].split()[0].strip())
    return names


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    paths = (API, MODEL)
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
