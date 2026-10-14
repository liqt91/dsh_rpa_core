"""M63 S5 负向验证：台账长度闸门（`check_ledger.py`）—— 必须真红，且逐字节还原。

覆盖三条判据 + 一条防空转自检：

- **N1** `feature_list.json` 的 `title` 超 120 ⇒ 红；
- **N2** `project_state.json` 的 `notes` 超 2000 ⇒ 红；
- **N3** `PROGRESS.md` 的 cutoff **之后**追加超长 `evidence` ⇒ 红；
- **N4 ★** 把 cutoff 标记删掉 ⇒ 必须红（**防空转**：没有标记时判据 3 会静默检查 0 条 =
  一条永远绿的假判据，比没有更坏）；
- **N5 ★** 把一个台账文件写成 **CRLF** ⇒ 必须红。这一向复刻的是**真实发生过的**形状：
  `set_feature.py`（README 指定的改 `passes` 的官方入口）用 `Path.write_text` 且没给
  `newline="\n"`，一次调用就把 `feature_list.json` 漂出 426 个 CRLF。脚本已修，这一向防复发。

三自保：① 哨兵自检；② `try/finally` 还原；③ 基于 `read_bytes` 的 md5 逐字节核。

用法：`.venv/Scripts/python.exe .harness/spike/probe_m63_ledger_gates_negative.py`
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SENTINEL = "__m63ledger__"

GATE = ROOT / ".harness" / "scripts" / "check_ledger.py"
FEATURES = ROOT / ".harness" / "feature_list.json"
PROJECT_STATE = ROOT / ".harness" / "project_state.json"
PROGRESS = ROOT / ".harness" / "PROGRESS.md"

sys.path.insert(0, str(GATE.parent))
from check_ledger import CUTOFF  # noqa: E402


def _text_replace(old: str, new: str) -> Callable[[str], str]:
    def mutate(text: str) -> str:
        count = text.count(old)
        if count != 1:
            raise ValueError(f"锚点命中 {count} 次（须恰好 1）")
        return text.replace(old, new)

    return mutate


def _to_crlf(text: str) -> str:
    """把整文件写成 CRLF —— 复刻 `set_feature.py` 旧 bug 的产物形状。"""
    if "\r\n" in text:
        raise ValueError("文件已经是 CRLF 了")
    return text.replace("\n", "\r\n")


def _json_set(field: str, value_maker: Callable[[object], object]) -> Callable[[str], str]:
    """JSON 感知的注入：解析 → 改字段 → 用**能逐字节还原**的参数序列化。"""

    def mutate(text: str) -> str:
        data = json.loads(text)
        if field == "title":
            data["features"][0]["title"] = value_maker(data["features"][0]["title"])
        else:
            data[field] = value_maker(data[field])
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    return mutate


CASES: list[tuple[str, Path, Callable[[str], str], str]] = [
    (
        "N1 title 超 120 字符 ⇒ 红",
        FEATURES,
        _json_set("title", lambda _old: SENTINEL * 13),
        f"title {len(SENTINEL) * 13} 字符 > 120",
    ),
    (
        "N2 notes 超 2000 字符 ⇒ 红",
        PROJECT_STATE,
        _json_set("notes", lambda _old: SENTINEL * 181),
        f"notes {len(SENTINEL) * 181} 字符 > 2000",
    ),
    (
        "N3 cutoff 之后的条目 evidence 超 400 ⇒ 红",
        PROGRESS,
        _text_replace(CUTOFF, CUTOFF + f"\n2026-10-14 | probe | x | {SENTINEL * 40}"),
        "evidence",
    ),
    (
        "N4 ★删掉 cutoff 标记 ⇒ 防空转自检红（否则判据 3 静默检查 0 条）",
        PROGRESS,
        _text_replace(CUTOFF, ""),
        "cutoff 标记出现 0 次",
    ),
    (
        "N5 ★台账文件写成 CRLF ⇒ 红（复刻 set_feature.py 旧 bug 的真实形状）",
        FEATURES,
        _to_crlf,
        "个 CRLF",
    ),
]

MANAGED = sorted({case[1] for case in CASES} | {GATE})


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run() -> tuple[int, str]:
    proc = subprocess.run([str(PY), str(GATE)], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    for path in MANAGED:
        if not path.is_file():
            print(f"REFUSING: 受管文件不存在：{path}")
            return 2
        if SENTINEL in path.read_text(encoding="utf-8"):
            print(f"REFUSING: 受管文件已含哨兵（上次未还原？）：{path}")
            return 2

    rc, out = run()
    if rc != 0:
        print(f"REFUSING: 基线不绿（check_ledger rc={rc}）—— 先修基线")
        print(out[-600:])
        return 2
    print("基线绿：check_ledger rc=0\n")

    results: list[tuple[str, bool, int, str, str]] = []
    for name, path, mutate, expect in CASES:
        original_bytes = path.read_bytes()
        before = md5(path)
        try:
            try:
                mutated = mutate(original_bytes.decode("utf-8"))
            except ValueError as exc:
                print(f"REFUSING: {name} —— {exc}")
                return 2
            if mutated.encode("utf-8") == original_bytes:
                print(f"REFUSING: {name} —— 注入没有改变文件")
                return 2
            path.write_text(mutated, encoding="utf-8", newline="\n")
            rc, out = run()
            lines = [line for line in out.strip().splitlines() if line.strip()]
            hit = rc != 0 and expect in out
            results.append((name, hit, rc, expect, lines[-1] if lines else ""))
        finally:
            path.write_bytes(original_bytes)
        after = md5(path)
        if before != after:
            print(f"REFUSING: {path.name} 未逐字节还原（{before} != {after}）")
            return 2

    print()
    all_hit = True
    for name, hit, rc, expect, last in results:
        print(f"{'HIT ' if hit else 'MISS'}  {name}   (rc={rc}，期望含 {expect!r})")
        if not hit:
            all_hit = False
            print(f"       末行：{last}")
    restored = run()[0] == 0
    print()
    print(f"还原后复绿：{restored}")
    verdict = "PASS（全中 + 逐字节还原 + 复绿）" if all_hit and restored else "FAIL"
    print(f"RESULT: {verdict}")
    return 0 if all_hit and restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
