"""M47.11 负向验证：预览截图链路的判据是否真拦得住。

每条注入都改在**判据实际检查的那个方向**上，然后跑受影响的那几个测试文件，
要求：退出码非 0、有 ``failed``、**无 ``error``**（error=挂住/收集失败，不算拦住了）、
且失败 nodeid 命中期望项。全部完成后逐字节还原并核 md5。

注入清单（6条）：
  H1 信封不带 wantShot⇒ 图链路根本不启动（扩展侧永远拿不到 true）。
  H2 wantShot 不透传/ 被清成 False ⇒ 要图时也没图。
  H3 rect/viewport 被丢弃 ⇒ GUI 拿不到红框坐标，静默退化成「只有图没有框」。
  H4 截图失败被当成校验失败 ⇒ 预览页签显示「校验失败」。
  H5 没要图也把 dataUrl 揣进结果 ⇒ 几百 KB 白进 GUI 内存。
  H6 preview_box_in_image 用 dpr 而不是图实际宽高 ⇒ 分数缩放下框偏 1px。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "src" / "rpa_core" / "capture" / "verify.py"
TESTS = [
    ROOT / "tests" / "contract" / "test_verify_element.py",
]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")


def run_pytest(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", "-p", "no:cacheprovider", "--tb=line", "-q", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout + proc.stderr


def failed_nodeids(output: str) -> list[str]:
    """从输出里取 ``FAILED <nodeid>`` 行。

    **为什么不读 ``N failed`` 摘要行**：本机 pytest 在teardown 清理 tmp 目录时会被
    批量删除守卫拦下（fail-closed ``SystemExit(1)``），摘要行因此**永远打不出来**——
    连全绿那次也一样（只到 ``[100%]``）。但 ``FAILED`` 行是**跑的过程中**逐条打的，
    在 teardown 之前，所以照样拿得到。用它反而更强：能指名道姓地确认「红的是
    期望的那几条」，而不是只看到一个数字。
    """
    out = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            out.append(line)
    return out


def counts(output: str) -> tuple[int, int]:
    """(failed, error) 个数，按 ``FAILED``/``ERROR`` 行计（理由见 failed_nodeids）。"""
    lines = failed_nodeids(output)
    failed = sum(1 for line in lines if line.startswith("FAILED "))
    error = sum(1 for line in lines if line.startswith("ERROR "))
    return failed, error


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


# ---- 注入（每个改一处，注释里写清改的是判据的哪个方向）----------------------
INJECTIONS: list[tuple[str, str, str, str]] = [
    (
        "H1",
        "verify.py",
        '"wantShot": bool(want_shot) and mode != "clear",',
        '"wantShot": False,',
        "信封恒 false：判据test_want_shot_true_sends_flag_and_returns_payload "
        "与test_clear_preview_never_asks_for_shot 都要红",
    ),
    (
        "H2",
        "verify.py",
        '        if want_shot and isinstance(shot, str) and shot:\n            result["dataUrl"] = shot',
        '        if False:\n            result["dataUrl"] = shot',
        "要图时也不给dataUrl：要图的判据与「截图失败仍回 count」同侧",
    ),
    (
        "H3",
        "verify.py",
        '        if isinstance(reply.get("rect"), dict):\n            result["rect"] = dict(reply["rect"])',
        '        if False:\n            result["rect"] = dict(reply["rect"])',
        "rect 不透传：只剩图没有框坐标（GUI 画不出红框且不报错）",
    ),
    (
        "H4",
        "verify.py",
        '        if want_shot and isinstance(shot, str) and shot:\n            result["dataUrl"] = shot',
        '        if want_shot and not (isinstance(shot, str) and shot):\n            return {"error": "shot-failed"}\n        if want_shot and isinstance(shot, str) and shot:\n            result["dataUrl"] = shot',
        "截图失败被升级成校验失败：test_want_shot_failure_still_returns_count 要红",
    ),
    (
        "H5",
        "verify.py",
        '        if want_shot and isinstance(shot, str) and shot:\n            result["dataUrl"] = shot',
        '        if isinstance(shot, str) and shot:\n            result["dataUrl"] = shot',
        "没要图也带 dataUrl：test_default_verify_does_not_ask_for_shot 要红",
    ),
    (
        "H6",
        "verify.py",
        '        if img_w > 0 and vp_w > 0:\n            scale_x = img_w / vp_w',
        '        if img_w > 0 and vp_w > 0:\n            scale_x = 1.0  # 假装 100% 缩放',
        "换算基准从「图实际宽」退化成 1.0：preview_box 的缩放判据要红",
    ),
]


EXPECTED = (
    "test_want_shot_true_sends_flag_and_returns_payload",
    "test_default_verify_does_not_ask_for_shot",
    "test_clear_preview_never_asks_for_shot",
    "test_want_shot_failure_still_returns_count",
    "test_shot_payload_is_copied_not_aliased",
    "test_preview_box_scales_by_image_size_not_assumed_dpr",
    "test_preview_box_falls_back_to_dpr_without_image_size",
    "test_preview_box_ignores_scroll_offset",
    "test_preview_box_returns_none_when_nothing_to_draw",
    "test_preview_box_clamps_partially_scrolled_out",
    "test_preview_box_survives_junk_image_size",
)


def main() -> int:
    target = VERIFY
    original = target.read_bytes()
    backup = target.with_suffix(".py.injectbak")
    backup.write_bytes(original)
    before = md5(target)
    tests = [str(p) for p in TESTS]

    code, out = run_pytest(*tests)
    if counts(out) != (0, 0):
        print("BASELINE 红了，先修判据/被测代码再谈注入：")
        print(out[-3000:])
        backup.unlink(missing_ok=True)
        return 2
    print(f"BASELINE 绿（failed=0 error=0；exit={code} 非 0 是删除守卫 SystemExit，不计判据）")

    verdicts: list[tuple[str, bool, str]] = []
    try:
        for tag, _fname, old, new, _why in INJECTIONS:
            text = original.decode("utf-8")
            if text.count(old) != 1:
                print(f"[{tag}] SKIP：锚点命中 {text.count(old)} 次（要求恰好 1）")
                verdicts.append((tag, False, f"锚点命中 {text.count(old)} 次"))
                continue
            target.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            code, out = run_pytest(*tests)
            lines = failed_nodeids(out)
            failed, error = counts(out)
            hit = [key for key in EXPECTED if any(key in line for line in lines)]
            ok = failed > 0 and error == 0 and bool(hit)
            detail = f"failed={failed} error={error} 命中={hit}"
            print(f"[{tag}] {'红' if ok else '未拦住'}：{detail}")
            verdicts.append((tag, ok, detail))
    finally:
        target.write_bytes(original)

    restored = md5(target)
    same = restored == before
    print(f"还原核md5: {'一致' if same else '不一致!!'}（{before} -> {restored}）")
    backup.unlink(missing_ok=True)

    bad = [t for t, ok, _ in verdicts if not ok]
    if not same:
        bad.append("restore")
    for tag, ok, detail in verdicts:
        print(f"  {tag}: {'PASS' if ok else 'FAIL'} {detail}")
    print("全部注入均被拦住" if not bad else f"未拦住: {bad}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())