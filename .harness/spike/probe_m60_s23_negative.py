"""M60 S2/S3 负向验证：注入破坏 → **预期的那条判据**必红 → 逐字节还原核 md5。

判据不是「有没有红」，而是「红在**预期的那几条**上」：每个注入都同时声明
`expect_red`（必须红）与 `expect_green`（必须仍绿）——只做一个方向的话，
一个「把什么都判红」的坏判据也能骗过检查。

三道自保（沿用 `probe_m60_ocr_gates_negative.py` 与 M40/M41 的教训）：

1. **起手「此刻是不是注入态」自检**：不是靠「备份目录里有没有这个文件」，
   而是直接问文件本身（`.py` 看哨兵注释；`.json` 看那条被注入改掉的字段还在不在）。
   上一轮被硬杀留下的注入态必须挡住，否则整轮实验都跑在脏基线上；
2. 注入包在 `try/finally` 里，收尾**逐字节还原并核 md5**；
3. 调用侧**输出重定向到文件再读**，不接管道（`| head` 会 SIGPIPE 打断还原）。

锚点一律**整行取**、且注入前做 `count == 1` 的唯一性自检。

用法：./.venv/Scripts/python.exe .harness/spike/probe_m60_s23_negative.py > 文件 2>&1
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "src" / "rpa_core" / "workers" / "ocr_worker.py"
SLIDE_MANIFEST = ROOT / "commands" / "ocr" / "slideMatch.json"
MANAGED = (WORKER, SLIDE_MANIFEST)

SENTINEL = "[M60-S23-NEGATIVE-INJECTED]"


def _is_injected(path: Path) -> bool:
    """「这份文件此刻是不是注入态」——直接问文件，而不是问备份。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return SENTINEL in text
    # json 放不下注释 ⇒ 用「被注入改掉的那个字段还在不在」当判据
    return "EXECUTOR_FAILED" not in (json.loads(text).get("errors") or [])


def _mark(path: Path, text: str) -> str:
    """给注入态留哨兵。**只有 .py 能留**（json 没有注释语法），
    json 靠 `_is_injected` 的字段判据兜住。"""
    if path.suffix == ".py":
        return text + f"# {SENTINEL}\n"
    return text


# ---------------------------------------------------------------- 检查项
def _pytest(*args: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        "tests/contract/test_ocr_contract.py",
        "-q",
        "-p",
        "no:cacheprovider",
        *args,
    ]


CHECKS: dict[str, list[str]] = {
    "契约::charset": _pytest("-k", "charset_range"),
    "契约::detect": _pytest("-k", "detect_normalises"),
    "契约::dispatch": _pytest("-k", "dispatches_every_ocr_command"),
    "契约::slide": _pytest("-k", "min_confidence"),
    "用例表真跑": [sys.executable, ".harness/spike/probe_ocr_cases_live.py"],
    "门禁::param": [sys.executable, ".harness/scripts/check_param_consumption.py"],
    "门禁::error": [sys.executable, ".harness/scripts/check_error_contract.py"],
    "门禁::matrix": [sys.executable, ".harness/scripts/check_command_matrix.py"],
}

# 每个注入：锚点（整行）→ 破坏 → 谁的判据该红 / 谁必须仍绿 / 红输出里必须出现的证据
INJECTIONS: list[dict] = [
    {
        "id": "① charsetRange 不喂给引擎",
        "why": "承认参数存在但不用它 ⇒ 字符集收窄整条失效",
        "target": WORKER,
        "old": '    engine = _engine("recognize", model_path, charset_range)\n',
        "new": '    engine = _engine("recognize", model_path, "")\n',
        "expect_red": ["契约::charset", "用例表真跑"],
        "expect_green": ["契约::detect", "契约::slide", "契约::dispatch"],
        # 必须红在「charsetRange=abc 应退化成空串」那一格上
        "must_contain": {
            "契约::charset": ["'abc'"],
            "用例表真跑": ["charset-range-outside-the-model-charset-yields-empty"],
        },
    },
    {
        "id": "② 引擎缓存键丢掉 charset_range",
        "why": "set_ranges 改的是实例状态 ⇒ 范围会串味给后续请求",
        "target": WORKER,
        "old": "    key = (mode, model_path, charset_range)\n",
        "new": "    key = (mode, model_path)\n",
        "expect_red": ["契约::charset", "用例表真跑"],
        "expect_green": ["契约::detect", "契约::slide"],
        # ★ 串味只被「第 3 次不填该参数」那一格抓到：期望 (None, '58')，实际会变成 ''
        "must_contain": {"契约::charset": ["(None,"]},
    },
    {
        "id": "③ detect 把 right/bottom 当成 width/height",
        "why": "五元归一化写错 ⇒ 框会越界",
        "target": WORKER,
        "old": (
            '        items.append({"x": left, "y": top, "width": right - left, '
            '"height": bottom - top})\n'
        ),
        "new": '        items.append({"x": left, "y": top, "width": right, "height": bottom})\n',
        "expect_red": ["契约::detect"],
        # 用例表只断言 outputKeys ⇒ 抓不到；这说明「形状判据」确实拦不住越界
        "expect_green": ["契约::slide", "契约::charset", "用例表真跑", "契约::dispatch"],
        "must_contain": {"契约::detect": ["<= width"]},
    },
    {
        "id": "④ minConfidence 形同虚设（恒不拦）",
        "why": "库没有失配分支 ⇒ 不拦就等于错坐标一路拖下去",
        "target": WORKER,
        "old": (
            "    if min_confidence > 0 and (confidence is None or confidence < min_confidence):\n"
        ),
        "new": "    if False:\n",
        "expect_red": ["契约::slide", "用例表真跑"],
        "expect_green": ["契约::detect", "契约::charset", "契约::dispatch"],
        "must_contain": {"用例表真跑": ["low-match-is-rejected-when-min-confidence-is-set"]},
    },
    {
        "id": "⑤ minConfidence 恒拦（默认 0 也被拦）",
        "why": "默认态被拦 ⇒ 「默认透传」这个口径被破坏",
        "target": WORKER,
        "old": (
            "    if min_confidence > 0 and (confidence is None or confidence < min_confidence):\n"
        ),
        "new": "    if min_confidence >= 0:\n",
        "expect_red": ["契约::slide", "用例表真跑"],
        "expect_green": ["契约::detect", "契约::charset", "契约::dispatch"],
        "must_contain": {"用例表真跑": ["min-confidence-zero-passes-the-match-through"]},
    },
    {
        "id": "⑥ 坐标从左边界数（不是中心）",
        "why": "卸掉「+ 尺寸//2」⇒ 中心语义被破坏（拖动距离会整体偏掉半块）",
        "target": WORKER,
        "old": (
            '        "x": int(raw.get("target_x", 0)),\n        "y": int(raw.get("target_y", 0)),\n'
        ),
        # 「减 17」= 模拟顶左边界：本探针的块图是 35x35 ⇒ 17 == 尺寸 // 2。
        # ★ 已知边界：能抓到的只是一条**公式必要条件**（x >= 模板宽 // 2），
        #   32x32 以外的夹具方位抓不到（见 M60 §9.3）；用例表只断言键 ⇒ 必然仍绿。
        "new": (
            '        "x": int(raw.get("target_x", 0)) - 17,\n'
            '        "y": int(raw.get("target_y", 0)) - 17,\n'
        ),
        "expect_red": ["契约::slide"],
        "expect_green": ["用例表真跑", "契约::detect", "契约::charset", "契约::dispatch"],
        "must_contain": {"契约::slide": ["tpl_w // 2"]},
    },
    {
        "id": "⑦ handler 丢掉 detect 分支",
        "why": "显式 if 链少一条 ⇒ 合法命令掉进 COMMAND_NOT_FOUND 兜底",
        "target": WORKER,
        "old": '    if command_id == "ocr.detect":\n',
        "new": '    if command_id == "ocr.detect-lost":\n',
        "expect_red": ["契约::dispatch", "契约::detect", "用例表真跑", "门禁::param"],
        # ★ 首轮实测把 `门禁::error` 也写成了「期望红」——**是错的，已按实测修正**：
        #   错误契约门禁的判据是**单向包含** `returned ⊆ declared`（源码
        #   `returned |= per_command.get(command_id, set())` 后算 `missing = returned - declared`），
        #   分支消失后 `returned = ∅` ⇒ `missing = ∅` ⇒ **空过**。
        #   ⇒ 「命令从分派器里消失」这件事**只有参数门禁看得见**（声明了参数却没人读）；
        #   错误门禁对「压根没接线」是无感的。这条已登记（M60 §十 / BACKLOG）。
        "expect_green": ["契约::slide", "契约::charset", "门禁::error"],
        "must_contain": {"契约::dispatch": ["COMMAND_NOT_FOUND"]},
    },
    {
        "id": "⑧ manifest 少声明 EXECUTOR_FAILED",
        "why": "分支体会返回的码没声明 ⇒ 错误契约必须拦住",
        "target": SLIDE_MANIFEST,
        "old": '  "errors": ["INVALID_INPUT", "EXECUTOR_FAILED", "TIMEOUT", "CANCELLED"],\n',
        "new": '  "errors": ["INVALID_INPUT", "TIMEOUT", "CANCELLED"],\n',
        "expect_red": ["门禁::error"],
        "expect_green": [
            "契约::slide",
            "契约::detect",
            "用例表真跑",
            "门禁::param",
            "门禁::matrix",
        ],
        "must_contain": {"门禁::error": ["slideMatch", "EXECUTOR_FAILED"]},
    },
]


def _run(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    # --- 起手自检：受管文件不许处于注入态 ---
    for path in MANAGED:
        if _is_injected(path):
            print(f"X 受管文件处于注入态（{path.relative_to(ROOT)}）——先还原再跑")
            return 2

    baseline = {path: path.read_bytes() for path in MANAGED}
    baseline_md5 = {path: _md5(path) for path in MANAGED}

    # --- 基线对照：干净树上这些检查必须全绿 ---
    print("== 基线（干净树）==")
    dirty = []
    for label, argv in CHECKS.items():
        code, out = _run(argv)
        print(f"  [{'OK ' if code == 0 else 'RED'}] {label}")
        if code != 0:
            dirty.append(label)
            lines = out.strip().splitlines()
            print("      " + (lines[-1][:200] if lines else ""))
    if dirty:
        print(f"X 基线不干净（{dirty}）⇒ 拒绝继续：注入实验必须从全绿开始")
        return 2

    failures: list[str] = []
    try:
        for spec in INJECTIONS:
            path: Path = spec["target"]
            text = path.read_text(encoding="utf-8")
            hits = text.count(spec["old"])
            print(f"\n== {spec['id']} ==\n   为什么钉它：{spec['why']}")
            if hits != 1:
                failures.append(f"{spec['id']}：锚点不唯一（count={hits}）")
                print(f"   X 锚点唯一性自检未过（count={hits}），跳过")
                continue
            path.write_text(
                _mark(path, text.replace(spec["old"], spec["new"])), encoding="utf-8", newline="\n"
            )
            try:
                observed: dict[str, tuple[int, str]] = {}
                for label in spec["expect_red"] + spec["expect_green"]:
                    observed[label] = _run(CHECKS[label])
                for label in spec["expect_red"]:
                    code, out = observed[label]
                    print(f"   [{'OK ' if code != 0 else 'XX'}] 期望红 · {label}")
                    if code == 0:
                        failures.append(f"{spec['id']}：{label} 该红却绿了")
                        print("        " + (out.strip().splitlines() or [""])[-1][:180])
                for label in spec["expect_green"]:
                    code, out = observed[label]
                    print(f"   [{'OK ' if code == 0 else 'XX'}] 期望绿 · {label}")
                    if code != 0:
                        failures.append(f"{spec['id']}：{label} 该绿却红了")
                        print("        " + (out.strip().splitlines() or [""])[-1][:180])
                for label, needles in (spec.get("must_contain") or {}).items():
                    out = observed.get(label, (0, ""))[1]
                    for needle in needles:
                        hit = needle in out
                        print(f"   [{'OK ' if hit else 'XX'}] 证据 · {label} 含 {needle!r}")
                        if not hit:
                            failures.append(f"{spec['id']}：{label} 输出里没有 {needle!r}")
            finally:
                path.write_bytes(baseline[path])
    finally:
        for path, blob in baseline.items():
            path.write_bytes(blob)

    # --- 逐字节还原核对 ---
    print("\n== 还原核对 ==")
    for path in MANAGED:
        now, before = _md5(path), baseline_md5[path]
        ok = now == before and not _is_injected(path)
        print(f"  [{'OK ' if ok else 'XX'}] {path.relative_to(ROOT)} md5 {now}")
        if not ok:
            failures.append(f"{path} 未逐字节还原")

    if failures:
        print("\n负向验证失败：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(f"\n负向验证通过：{len(INJECTIONS)} 个方向全部落在预期，受管文件 md5 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
