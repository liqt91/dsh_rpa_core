"""M97 负向验证：新增的捕获粒度判据必须真拦得住破坏。

背景（真机）：hover 框着微信搜索框、按 F9 保存下来是整个微信窗口。根因不是「兜底
没写」，而是兜底写在了另一条路上——快路径不产 DFS 的 leaf，而捕获只复用 leaf。

注入按推理写期望集（不照抄实际）：
1. 复用条件退回「要有 DFS leaf」   → 只有「复用画红框元素」那条红；
2. hit_info 为 None 时保留旧元素    → 「不许复用旧元素」红；
3. 屏幕级兜底退回「handle 必须是真句柄」→ 「虚拟元素要采信」红；
4. 重试择优退回「只比命中数」       → 「粒度压过命中数」红；
5. 提前返回退回「命中 1 就算好」    → 「整窗不提前返回」红；
6. 粒度判定不看 rect               → 上面三条里依赖粒度的全红；
7. capture_at 不写语义 rect        → 「语义捕获要落 rect」红。

写回一律 write_bytes（write_text 会把 LF 转 CRLF）；锚点要求唯一；结束核 md5。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
DA = ROOT / "src/rpa_core/capture/desktop_agent.py"
PY = ROOT / ".venv/Scripts/python.exe"
SENTINEL = "M97-INJECT-SENTINEL"

REUSE = "test_hover_capture_reuses_element_that_drew_the_frame"
STALE = "test_hover_capture_does_not_reuse_stale_element"
VIRTUAL = "test_finer_accepts_virtual_element_without_handle"
REJECT_OTHER = "test_finer_rejects_element_of_another_window"
RANK_MISSING = "test_capture_rank_tolerates_missing_rect"
PREFER = "test_capture_with_retry_prefers_finer_granularity"
NOSTOP = "test_capture_with_retry_does_not_stop_on_container_hit"
AT_RECT = "test_capture_at_records_rect_in_semantic_mode"
GATE = "M96 捕获粒度：整窗容器回退屏幕级"

INJECTIONS = [
    (
        "hover-reuse-requires-leaf", DA,
        "                        and last_hit_info is not None\n",
        "                        and _leaf is not None  # " + SENTINEL + "\n",
        ("pytest",), {REUSE},
    ),
    (
        "hover-keeps-stale-info", DA,
        "                last_hit_info = hit_info\n"
        "                last_path = list(path_infos) if hit_info is not None else []\n",
        "                if hit_info is not None:  # " + SENTINEL + "\n"
        "                    last_hit_info = hit_info\n"
        "                    last_path = list(path_infos)\n",
        ("pytest",), {STALE},
    ),
    (
        # 这一项同时证明两层判据都拦得住：单测（虚拟元素要采信）+ 契约锚点（源码里
        # 必须有 handle=0 的兜底）——只改一层就绿是「假绿灯」的典型形态。
        "finer-root-no-fallback", DA,
        "    root_of_element = _root_window_handle(handle) if handle else _win32_root_at(x, y)\n",
        "    root_of_element = _root_window_handle(handle)  # " + SENTINEL + "\n",
        ("pytest", "gate"), {VIRTUAL, GATE},
    ),
    (
        "retry-ranks-by-count", DA,
        "        if best is None or rank > best_rank:\n",
        "        if best is None or (\n"
        "            int(result.get(\"verifyCount\") or 0)\n"
        "            > int(best.get(\"verifyCount\") or 0)  # " + SENTINEL + "\n"
        "        ):\n",
        ("pytest",), {PREFER},
    ),
    (
        "retry-stops-on-container-hit", DA,
        "        if rank[1] == 1 and rank[0] == 1:\n",
        "        if rank[1] == 1:  # " + SENTINEL + "\n",
        ("pytest",), {NOSTOP},
    ),
    (
        "rank-ignores-rect", DA,
        "    container = _rect_fills_window(rect, int(scope_hwnd or 0))\n",
        "    container = False  # " + SENTINEL + "\n",
        ("pytest",), {RANK_MISSING, PREFER, NOSTOP},
    ),
    (
        "capture-at-no-semantic-rect", DA,
        "    else:\n"
        "        # 语义模式也要写 rect：预览红框读它，``capture_with_retry`` 判「这一枪是不是\n"
        "        # 只拿到整窗容器」也读它。此前只有图像模式写 ⇒ 语义捕获的 rect 恒缺，\n"
        "        # 下游只能按「拿不到矩形 = 容器」保守处理（M97）。\n"
        "        rect_text = _info_rect(info)\n"
        "        if rect_text is not None:\n"
        "            metadata[\"rect\"] = rect_text\n",
        "    # " + SENTINEL + "\n",
        ("pytest",), {AT_RECT},
    ),
]

RUNNERS = {
    "pytest": [
        str(PY), "-m", "pytest", "tests/unit/test_capture_container_fallback.py",
        "-q", "--basetemp=D:/Temp/pt_m97neg",
    ],
    "gate": [str(PY), ".harness/scripts/check_element_pick_contract.py"],
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
    if which == "gate":
        return {
            line[5:].split(" — ")[0].strip()
            for line in output.splitlines() if line.startswith("FAIL ")
        }
    return {
        line.split("::")[-1].split()[0].strip()
        for line in output.splitlines() if line.startswith("FAILED ")
    }


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    if SENTINEL in DA.read_text(encoding="utf-8"):
        print(f"[FATAL] {DA.name} 已有注入哨兵，先手工清理")
        return 2
    baseline_md5 = md5(DA)
    print("baseline md5:", baseline_md5[:12])

    for which in RUNNERS:
        base = run(which)
        tail = (base.stdout or base.stderr).strip().splitlines()[-1:]
        print(f"[baseline {which}] rc={base.returncode} {tail[0][:70] if tail else ''}")
        if base.returncode != 0:
            print("[FATAL] 基线不绿，负向验证无意义")
            return 2

    ok = True
    for name, path, old, new, runners, expected in INJECTIONS:
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"[FAIL] {name}: 锚点出现 {original.count(old)} 次（要求 1）")
            ok = False
            continue
        path.write_bytes(original.replace(old, new, 1).encode("utf-8"))
        got: set[str] = set()
        codes: list[int] = []
        errorish = False
        try:
            for which in runners:
                bad = run(which)
                codes.append(bad.returncode)
                got |= failed_names(which, bad.stdout)
                errorish = errorish or " error" in bad.stdout
        finally:
            path.write_bytes(original.encode("utf-8"))
        print(f"\n[{name}] rc={codes} 失败集={sorted(got)}")
        print(f"  期望={sorted(expected)}")
        if got != expected:
            print("  [FAIL] 失败集与期望不等")
            ok = False
        if any(code not in (0, 1) for code in codes) or errorish:
            print("  [FAIL] 出现 error 或异常退出码（环境坏了，不是判据红）")
            ok = False

    restored = md5(DA)
    print("\nrestored md5:", restored[:12])
    if restored != baseline_md5:
        print("[FAIL] md5 未逐字节还原")
        ok = False
    for which in RUNNERS:
        again = run(which)
        print(f"[after restore {which}] rc={again.returncode}")
        if again.returncode != 0:
            print("[FAIL] 还原后复跑不绿")
            ok = False
    print("M97 NEGATIVE VERIFICATION " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
