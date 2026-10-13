"""负向验证探针（M60 S1）：证明 OCR 指令族**真的**被两条静态门禁覆盖。

背景：`ocr.recognize` 起初在 `check_param_consumption.EXECUTOR_FILES` 里没有映射，两条门禁
（参数消费 / 错误契约）都把它算成 `skipped`——**「跳过」不是「通过」**，只是没人检查。
M60 S1 登记映射后两条门禁的计数从 `77 checked / 1 skipped` 变成 `78 checked / 0 skipped`。
本探针要证的是这个 `+1` **是真覆盖**，而不是「登记了个名字换来个假绿」。

四向，每向沿**一条真实缺陷的形状**注入，跑完逐字节还原（md5 核）：

1. **参数侧（实现读错键）**：worker 把 `inputs.get("withProbability")` 读成
   `inputs.get("withProbabilityX")` ⇒ `check_param_consumption.py` 必须红并**点名
   `withProbability` 从未被读取**；
2. **错误码侧（返回了没声明）**：`ocr.recognize` 分支体里把 `INVALID_INPUT` 换成未声明的
   `CAPABILITY_DENIED` ⇒ `check_error_contract.py` 必须红并点名该码。
   （这两向互为对照：I1 只该红参数门禁、I2 只该红错误门禁——只红一个才算「打在对的方向上」。）
3. **口径陷阱（`else:` 兜底）**：把兜底写成
   `if cmd == "ocr.recognize": ... else: return COMMAND_NOT_FOUND`——最自然的分派写法。
   旧门禁用 `ast.unparse(node)`（连 `orelse` 一起算）判「本期未实现」，会把 `ocr.recognize`
   整条**静默豁免**：门禁照报 PASS，覆盖却没了。本向既断言**修复后不再豁免**
   （checked 仍 78、未实现名单不变），也直接算出「旧口径本会豁免 = True」把机制钉死。
4. **豁免机制没被我改坏**：把 `browser.py` 里那三个未实现命令的
   `ErrorCode.COMMAND_NOT_FOUND` 换成别的码 ⇒ 它们不再豁免、必须真的被检查（红灯点名）。
   这一向与第 ③ 向配对：一个证明「分支体里没有 COMMAND_NOT_FOUND 就不该豁免」，
   一个证明「分支体里有就该豁免」，改「只看 node.body」不能把两边弄反。

★ 自保三道（都踩过坑，见 BACKLOG / 跨项目记忆）：
① `main()` 开头先跑**基线对照**（干净树上两条门禁必须 PASS 且 `0 skipped`）——
   上一轮注入残留会让基线就不干净，此时硬失败退出，绝不带着哨兵往下跑；
② 所有注入包在 `try/finally` 里，`restore` 直接写回**原始字节**并核 md5；
③ 调用侧输出不接管道（`| head` 会 SIGPIPE 打断探针导致还原不执行）。

用法：uv run python .harness/spike/probe_m60_ocr_gates_negative.py
"""

from __future__ import annotations

import ast
import hashlib
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")

WORKER = ROOT / "src" / "rpa_core" / "workers" / "ocr_worker.py"
BROWSER = ROOT / "src" / "rpa_core" / "executors" / "browser.py"
PARAM_GATE = ROOT / ".harness" / "scripts" / "check_param_consumption.py"
ERR_GATE = ROOT / ".harness" / "scripts" / "check_error_contract.py"

# 干净树上的两条门禁摘要（注入任何一处都会改变它，所以它是基线的判据）
CLEAN_SUMMARY = "78 checked / 3 exempt / 0 skipped = 81 条命令"
UNIMPLEMENTED_LINE = "browser.download, browser.handleDialog, browser.upload"

# 本探针自己的注入痕迹（启动哨兵扫描用）
SENTINELS = ("withProbabilityX", "NEGPROBE", "CAPABILITY_DENIED")

REPORT: list[str] = []
FAILURES: list[str] = []


def _run(command: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _gate_report(name: str, code: int, output: str) -> None:
    """把一条门禁的实际输出摘成一行（判据失败时要能一眼看出红在哪）。"""
    tail = [line.strip() for line in output.splitlines() if line.strip()]
    REPORT.append(f"      {name}: exit={code}；{' | '.join(tail[:4])}")


def patch(path: pathlib.Path, injection: tuple[str, str]) -> bytes:
    """按 `(旧, 新)` 注入：锚点必须**唯一命中**（否则硬失败），返回原始字节。"""
    original = path.read_bytes()
    text = original.decode("utf-8")
    old, new = injection
    hits = text.count(old)
    assert hits == 1, f"注入锚点在 {path.name} 命中 {hits} 次（期望 1）：{old[:60]!r}"
    path.write_bytes(text.replace(old, new).encode("utf-8"))
    return original


def restore(path: pathlib.Path, original: bytes) -> bool:
    path.write_bytes(original)
    return path.read_bytes() == original


def check(step: str, ok: bool, detail: str, restored: bool, md5_ok: bool) -> bool:
    verdict = "红/绿在预期（符合）" if ok else "**未落在预期——判据是摆设**"
    REPORT.append(f"  {step}\n    → {verdict}；{detail}；字节还原 = {restored}；md5 一致 = {md5_ok}")
    if not ok:
        FAILURES.append(step)
    return ok


def _ocr_if_would_exempt_old(text: str) -> bool:
    """旧口径（`ast.unparse(node)` 连 orelse）会不会把 ocr.recognize 判成「未实现」。"""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        # ast.unparse 用 repr 形状（单引号），所以按**裸子串**比对。
        if "ocr.recognize" not in ast.unparse(node.test):
            continue
        if "COMMAND_NOT_FOUND" in ast.unparse(node):
            return True
    return False


def baseline() -> None:
    """哨兵 + 对照：干净树上两条门禁必须 PASS 且 0 skipped。不干净就直接退出。"""
    for path in (WORKER, BROWSER):
        text = path.read_text(encoding="utf-8")
        for sentinel in SENTINELS:
            if sentinel in text:
                raise SystemExit(
                    f"★ 工作区不干净：{path.name} 里残留注入痕迹 {sentinel!r}，"
                    "先还原再跑本探针（拒绝在带哨兵的树上做验证）。"
                )
    for name, gate in (("check_param_consumption", PARAM_GATE), ("check_error_contract", ERR_GATE)):
        code, output = _run([PYTHON, str(gate)])
        if code != 0 or CLEAN_SUMMARY not in output or "跳过" in output:
            raise SystemExit(
                f"★ 基线对照失败：{name} exit={code}；期望含 {CLEAN_SUMMARY!r} 且不含「跳过」。"
                f"\n--- 实际输出 ---\n{output}"
            )
    REPORT.append(" ⓪ 基线对照：两条门禁 PASS 且 0 skipped（干净树，与门禁输出一致）")


def main() -> None:
    baseline()

    # ── ① 参数侧：实现读错键 ────────────────────────────────────────────────
    before = _md5(WORKER)
    original = patch(
        WORKER,
        (
            '                with_probability=bool(inputs.get("withProbability")),',
            '                with_probability=bool(inputs.get("withProbabilityX")),  # NEGPROBE-1',
        ),
    )
    try:
        p_code, p_out = _run([PYTHON, str(PARAM_GATE)])
        e_code, _ = _run([PYTHON, str(ERR_GATE)])
    finally:
        restored = restore(WORKER, original)
    expected_hit = "ocr.recognize: 声明了但实现侧从未读取 → withProbability"
    _gate_report("param", p_code, p_out)
    check(
        "① 实现把 withProbability 读成 withProbabilityX ⇒ 参数门禁点名该参数（错误门禁应不受影响）",
        p_code == 1 and expected_hit in p_out and e_code == 0,
        f"param exit={p_code}（期望 1）、点名 = {expected_hit in p_out}；err exit={e_code}（期望 0）",
        restored,
        _md5(WORKER) == before,
    )

    # ── ② 错误码侧：返回了没声明 ────────────────────────────────────────────
    before = _md5(WORKER)
    original = patch(
        WORKER,
        (
            "            return _failure(request_id, ErrorCode.INVALID_INPUT, str(exc))",
            "            return _failure(request_id, ErrorCode.CAPABILITY_DENIED, str(exc))  # NEGPROBE-2",
        ),
    )
    try:
        e_code, e_out = _run([PYTHON, str(ERR_GATE)])
        p_code, _ = _run([PYTHON, str(PARAM_GATE)])
    finally:
        restored = restore(WORKER, original)
    expected_hit = "ocr.recognize: 实现会返回但 manifest.errors 未声明 → CAPABILITY_DENIED"
    _gate_report("errc", e_code, e_out)
    check(
        "② 分支体把 INVALID_INPUT 换成未声明的 CAPABILITY_DENIED ⇒ 错误门禁点名该码（参数门禁应不受影响）",
        e_code == 1 and expected_hit in e_out and p_code == 0,
        f"err exit={e_code}（期望 1）、点名 = {expected_hit in e_out}；param exit={p_code}（期望 0）",
        restored,
        _md5(WORKER) == before,
    )

    # ── ③ 口径陷阱：把兜底写成 else ─────────────────────────────────────────
    before = _md5(WORKER)
    original = patch(
        WORKER,
        (
            '        return {"id": request_id, "ok": True, "outputs": outputs}\n'
            "    # 注意：这条兜底 return 留在 If 之外（别顺手改成 `else:`）。\n"
            "    # 门禁判「本期未实现」的依据是**分支体**里出现 COMMAND_NOT_FOUND；\n"
            '    # `if cmd == "ocr.recognize": ... else: return COMMAND_NOT_FOUND` 这种最自然的分派\n'
            "    # 写法曾让它被整条静默豁免（口径缺陷已修：只看 `node.body`，负向验证见\n"
            "    # `.harness/spike/probe_m60_ocr_gates_negative.py` 第 ③ 向）。\n"
            "    return _failure(\n"
            "        request_id,\n"
            "        ErrorCode.COMMAND_NOT_FOUND,\n"
            '        f"ocr worker does not handle {command_id!r}",\n'
            "    )",
            '        return {"id": request_id, "ok": True, "outputs": outputs}\n'
            "    else:  # NEGPROBE-3：把兜底写成 else（模拟最自然的分派写法）\n"
            "        return _failure(\n"
            "            request_id,\n"
            "            ErrorCode.COMMAND_NOT_FOUND,\n"
            '            f"ocr worker does not handle {command_id!r}",\n'
            "        )",
        ),
    )
    try:
        injected_text = WORKER.read_text(encoding="utf-8")
        would_exempt_old = _ocr_if_would_exempt_old(injected_text)
        p_code, p_out = _run([PYTHON, str(PARAM_GATE)])
        e_code, e_out = _run([PYTHON, str(ERR_GATE)])
    finally:
        restored = restore(WORKER, original)
    _gate_report("param", p_code, p_out)
    _gate_report("errc", e_code, e_out)
    check(
        "③ else: 兜底 ⇒ 修复后**不**再静默豁免（旧口径本会豁免 = True，见 detail）",
        p_code == 0
        and e_code == 0
        and would_exempt_old
        and CLEAN_SUMMARY in p_out
        and CLEAN_SUMMARY in e_out
        and UNIMPLEMENTED_LINE in p_out
        and "ocr.recognize" not in p_out.split("未实现命令")[-1]
        and "ocr.recognize" not in e_out.split("未实现命令")[-1],
        f"旧口径本会豁免 = {would_exempt_old}（期望 True）；param exit={p_code}、"
        f"计数未变 = {CLEAN_SUMMARY in p_out}、未实现名单未变 = {UNIMPLEMENTED_LINE in p_out}",
        restored,
        _md5(WORKER) == before,
    )

    # ── ④ 豁免机制没被改坏：分支体里没有 COMMAND_NOT_FOUND 就不该豁免 ───────
    before = _md5(BROWSER)
    original = patch(
        BROWSER,
        (
            '        if command in ("browser.upload", "browser.download", "browser.handleDialog"):\n'
            "            return CommandResult.failure(\n"
            "                ErrorCode.COMMAND_NOT_FOUND,",
            '        if command in ("browser.upload", "browser.download", "browser.handleDialog"):\n'
            "            return CommandResult.failure(\n"
            "                ErrorCode.EXECUTOR_FAILED,  # NEGPROBE-4",
        ),
    )
    try:
        p_code, p_out = _run([PYTHON, str(PARAM_GATE)])
    finally:
        restored = restore(BROWSER, original)
    # 门禁的失败明细逐行形如 `- browser.upload: 声明了但实现侧从未读取 → files（…）`
    named = set(re.findall(r"- (browser\.[\w.]+): 声明了但实现侧从未读取", p_out))
    expected_named = {"browser.download", "browser.handleDialog", "browser.upload"}
    _gate_report("param", p_code, p_out)
    check(
        "④ 三个未实现命令的 COMMAND_NOT_FOUND 换成别的码 ⇒ 不再豁免、必须真被检查（红）",
        p_code == 1 and named == expected_named,
        f"param exit={p_code}（期望 1）、红点名的命令集合 = {sorted(named)}（期望 {sorted(expected_named)}）",
        restored,
        _md5(BROWSER) == before,
    )

    print("\n".join(REPORT))
    if FAILURES:
        print(f"\nNEGATIVE VERIFICATION FAILED（{len(FAILURES)} 向未落在预期）：" + "；".join(FAILURES))
        raise SystemExit(1)
    print("\nNEGATIVE VERIFICATION PASSED（四向全部红/绿在预期，注入逐字节还原）")


if __name__ == "__main__":
    main()
