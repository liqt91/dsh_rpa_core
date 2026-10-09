"""负向验证：M47.9「校验目标 = 最近激活的浏览器」（2026-10-09）。

**要证的机制**：

1. **Z 序识别**（`capture/foreground_window.py`）：走 `GetTopWindow` + `GW_HWNDNEXT`
   取第一个**浏览器**窗口（非浏览器窗口跳过）。
2. **定点下发**（`capture/verify.py::ElementVerifier`）：识别到目标就只发匹配端点，
   不再广播（否则 M47.8 的「两个浏览器都闪」会回来）。
3. **只 flash 置前**：`preview`/`clear` 不抢焦点。
4. **兜底不许变成「点了没反应」**：目标没装扩展 / Z 序无浏览器 ⇒ 退回广播。

**注入的两侧都要打**（用户的「两个方向都做」规矩）：
- 打**读侧**（foreground_window 的过滤/识别逻辑）；
- 打**写侧**（verify 的 `_candidates`/`_exchange` 接线）。

每条注入的期望必须落在**具体断言文本**上（不能只判退出码非 0 —— M40 的假绿灯机教训）。

用法：
    ./.venv/Scripts/python.exe .harness/spike/probe_m47_9_negative.py
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
SENTINEL = "# [M47.9-NEGATIVE-INJECTED]"

FG = REPO / "src" / "rpa_core" / "capture" / "foreground_window.py"
VFY = REPO / "src" / "rpa_core" / "capture" / "verify.py"

TESTS = ["tests/unit/test_foreground_window.py", "tests/contract/test_verify_element.py"]


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_tests(junit: Path) -> subprocess.CompletedProcess:
    """跑判据。

    不传 `-q`（`pyproject.toml` 的 addopts 已有一个，叠加成 `-qq` 会吞掉汇总行）。
    `--junitxml` 本身覆盖写，**不要 unlink**——本机删除守卫按 turn 累计会 fail-closed。
    """
    return subprocess.run(
        [
            str(PY), "-m", "pytest", *TESTS,
            "-p", "no:randomly",
            "--junitxml", str(junit),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def parse_counts(out: str) -> tuple[int, int, int]:
    failed = passed = error = 0
    for key, attr in (("failed", "failed"), ("passed", "passed"), ("error", "error")):
        match = re.search(rf"(\d+) {key}", out)
        if match:
            if attr == "failed":
                failed = int(match.group(1))
            elif attr == "passed":
                passed = int(match.group(1))
            else:
                error = int(match.group(1))
    return failed, passed, error


class Injection:
    def __init__(self, label: str, path: Path, mutate, expect_in: str):
        self.label = label
        self.path = path
        self.mutate = mutate          # (src) -> src（须断言锚点命中）
        self.expect_in = expect_in    # 变红时输出里必须出现的文本


# ---------------------------------------------------------------- 注入实现


def inj_read_side_class_name(src: str) -> str:
    """读侧①：`_is_browser_window` 退回「看类名」——把 ``Chrome_WidgetWin_1`` 认成浏览器。

    这正是实测踩过的坑：定制 Chromium 客户端（咚咚/WorkBuddy）也用这个类名。
    期望红在 `test_custom_chromium_client_is_not_a_browser`。
    """
    old = """    name = str(process or "").strip().lower()
    return _PROCESS_BROWSERS.get(name)"""
    new = f"""    if str(class_name or "").strip() in ("Chrome_WidgetWin_1", "MozillaWindowClass"): {SENTINEL}
        return "chromium"
    name = str(process or "").strip().lower()
    return _PROCESS_BROWSERS.get(name)"""
    assert old in src, "read-side-class-name 锚点未命中"
    return src.replace(old, new, 1)


def inj_read_side_no_filter(src: str) -> str:
    """读侧②：`_is_user_window` 恒真——不滤不可见/无标题/工具窗。

    期望红在 `test_hidden_and_titleless_windows_are_skipped`（以及维护者例子那条）。
    """
    old = """    if not visible:
        return False
    if not str(title or "").strip():
        return False"""
    new = f"""    if False:  # {SENTINEL}
        return False"""
    assert old in src, "read-side-no-filter 锚点未命中"
    return src.replace(old, new, 1)


def inj_read_side_first_window(src: str) -> str:
    """读侧③：不找浏览器，直接返回第一个用户窗（丢掉「第一个**浏览器**」语义）。

    期望红在 `test_non_browser_windows_are_skipped`（维护者的 edge→chrome→资源管理器例子）。
    """
    old = """        browser = _is_browser_window(class_name, process)
        if browser:
            return ForegroundBrowser("""
    new = f"""        browser = _is_browser_window(class_name, process)
        if True:  # {SENTINEL}
            return ForegroundBrowser("""
    assert old in src, "read-side-first-window 锚点未命中"
    return src.replace(old, new, 1)


def inj_write_side_no_target(src: str) -> str:
    """写侧①：`_candidates()` 忽略自动目标，恒走广播（= M47.8 的症状复现）。

    期望红在 `test_auto_target_dispatches_only_to_zorder_browser`。
    """
    old = """        if not target and self._auto_target:
            from rpa_core.capture.foreground_window import first_browser_window

            found = first_browser_window()"""
    new = f"""        if False:  # {SENTINEL}
            from rpa_core.capture.foreground_window import first_browser_window

            found = first_browser_window()"""
    assert old in src, "write-side-no-target 锚点未命中"
    return src.replace(old, new, 1)


def inj_write_side_no_fallback(src: str) -> str:
    """写侧②：目标没命中时返回空列表——「识别到但没装扩展」变成「点了没反应」。

    期望红在 `test_target_not_installed_falls_back_to_all`。
    """
    old = "            return (matched or names), target, hwnd"
    new = f"            return matched, target, hwnd  {SENTINEL}"
    assert old in src, "write-side-no-fallback 锚点未命中"
    return src.replace(old, new, 1)


def inj_write_side_bring_front_all_modes(src: str) -> str:
    """写侧③：置前不再限定 flash——preview 每敲一键抢一次焦点。

    期望红在 `test_bring_front_called_for_flash_not_preview_or_clear`。
    """
    old = """        if self._bring_front and mode == "flash" and hwnd:"""
    new = f"""        if self._bring_front and hwnd:  {SENTINEL}"""
    assert old in src, "write-side-bring-front-all 锚点未命中"
    return src.replace(old, new, 1)


def inj_write_side_no_bring_front(src: str) -> str:
    """写侧④：`bring_front=False` 被忽略——调用方想要的不抢焦点不生效。

    期望红在 `test_bring_front_disabled`。
    """
    old = """        self._bring_front = bring_front"""
    new = f"""        self._bring_front = True  {SENTINEL}"""
    assert old in src, "write-side-no-bring-front 锚点未命中"
    return src.replace(old, new, 1)


def inj_write_side_bring_front_not_wrapped(src: str) -> str:
    """写侧⑤：置前调用**不再包 try**——异常穿透整个校验（影刀那步②失败不该毁校验）。

    期望红在 `test_bring_front_failure_is_harmless`。
    """
    old = """            try:
                from rpa_core.capture.foreground_window import bring_to_foreground

                bring_to_foreground(hwnd)
            except Exception:  # noqa: BLE001 - 置前失败不该影响校验
                pass"""
    new = f"""            from rpa_core.capture.foreground_window import bring_to_foreground  {SENTINEL}

            bring_to_foreground(hwnd)"""
    assert old in src, "write-side-not-wrapped 锚点未命中"
    return src.replace(old, new, 1)


INJECTIONS = [
    Injection("读侧①只看类名（定制 Chromium 被误判成浏览器）", FG,
              inj_read_side_class_name,
              "test_first_browser_skips_custom_chromium_clients"),
    Injection("读侧②不过滤可见/标题（工具窗挤掉真浏览器）", FG,
              inj_read_side_no_filter,
              "test_first_browser_skips_hidden_and_untitled"),
    Injection("读侧③取第一个用户窗（丢掉「第一个浏览器」语义）", FG,
              inj_read_side_first_window,
              "test_first_browser_skips_non_browser_windows"),
    Injection("写侧①忽略自动目标恒广播（M47.8 症状复现）", VFY,
              inj_write_side_no_target,
              "test_auto_target_dispatches_only_to_zorder_browser"),
    Injection("写侧②没命中就返回空（点了没反应）", VFY,
              inj_write_side_no_fallback,
              "test_target_not_installed_falls_back_to_all"),
    Injection("写侧③preview/clear 也置前（每键抢焦点）", VFY,
              inj_write_side_bring_front_all_modes,
              "test_bring_front_called_for_flash_not_preview_or_clear"),
    Injection("写侧④bring_front=False 被忽略", VFY,
              inj_write_side_no_bring_front,
              "test_bring_front_disabled"),
    Injection("写侧⑤置前异常穿透（毁掉整个校验）", VFY,
              inj_write_side_bring_front_not_wrapped,
              "test_bring_front_failure_is_harmless"),
]


def main() -> int:
    if not FG.exists() or not VFY.exists():
        print("FAIL: 仓库路径不对（找不到 foreground_window.py / verify.py）")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="rpa-m479-neg-"))
    backups = {p: tmp / p.name for p in (FG, VFY)}
    for src, dst in backups.items():
        dst.write_bytes(src.read_bytes())
    originals = {p: _md5(p) for p in backups}
    junit = tmp / "report.xml"

    def restore_all() -> None:
        for src, dst in backups.items():
            src.write_bytes(dst.read_bytes())

    print("=== 对照（注入前）===")
    control = run_tests(junit)
    cf, cp, ce = parse_counts(control.stdout)
    if control.returncode != 0 or cf != 0 or ce != 0:
        print("FAIL: 对照未绿，先修再谈负向验证")
        print(control.stdout[-3000:])
        restore_all()
        return 1
    print(f"对照绿（{cp} passed / {cf} failed / {ce} error）")

    failures = 0
    for idx, inj in enumerate(INJECTIONS, start=1):
        print(f"\n=== {idx}/{len(INJECTIONS)} {inj.label} ===")
        try:
            src = inj.path.read_text(encoding="utf-8")
            mutated = inj.mutate(src)
            assert SENTINEL in mutated, "注入未落哨兵"
            inj.path.write_bytes(mutated.encode("utf-8"))

            try:
                proc = run_tests(junit)
            except subprocess.TimeoutExpired:
                print("FAIL: 判据挂住（超时）——不是红也不是绿，按失败计")
                failures += 1
                restore_all()
                continue
            combined = proc.stdout + proc.stderr
            failed_n, passed_n, error_n = parse_counts(combined)
            ok = (
                proc.returncode != 0
                and inj.expect_in in combined
                and error_n == 0
                and failed_n > 0
            )
            print(f"  exit={proc.returncode} failed={failed_n} passed={passed_n} error={error_n}"
                  f"（要求 exit!=0、failed>0、error==0、含「{inj.expect_in}」）")
            if not ok:
                print("  ✗ 未按预期变红")
                print(combined[-2500:])
                failures += 1
        finally:
            restore_all()

        for p, want in originals.items():
            got = _md5(p)
            if got != want:
                print(f"  ✗ 还原失败 {p.name}: {got} != {want}")
                failures += 1

    print("\n=== 复绿 ===")
    try:
        final = run_tests(junit)
    except subprocess.TimeoutExpired:
        print("FAIL: 复绿判据挂住")
        failures += 1
        final = None
    if final is not None:
        ff, fp, fe = parse_counts(final.stdout)
        if final.returncode != 0 or ff != 0 or fe != 0:
            print("FAIL: 还原后判据未复绿")
            print(final.stdout[-3000:])
            failures += 1
        else:
            print(f"还原后判据复绿（{fp} passed）")

    print(f"\n{'全部通过' if failures == 0 else f'{failures} 处失败'}")
    return 1 if failures else 0


def _atexit_guard() -> None:
    """收尾保险：按**哨兵**还原注入行（不用陈旧备份整体覆盖——M40 真事故的教训）。"""
    for path in (FG, VFY):
        try:
            src = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if SENTINEL not in src:
            continue
        print(f"WARN: {path.name} 仍带注入哨兵，请人工核对（应已逐字节还原）")


if __name__ == "__main__":
    import atexit

    atexit.register(_atexit_guard)
    sys.exit(main())
