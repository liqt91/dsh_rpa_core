"""M81 真机复验：微信自绘控件的显式图像 proposal + 跨 DPI 重采样。

⚠ 会前置微信窗口（**不点击、不输入、不读聊天内容**，只取左侧导航栏图标那一小块
UI 像素）；只在 ``RPA_DESKTOP_E2E=1`` 时运行。

为什么要单独一个探针（M79 覆盖不到的部分）：

* M79 只在记事本上验证了语义定位与遮挡 Z 序；微信当时被判成「没有可见窗口」。
* 微信 4.x 是 Qt 自绘（窗口类 ``Qt51514QWindowIcon``），UIA 树是空的 —— 这正是
  「必须走显式图像驱动」的场景，也是 ADR 0003 允许走第 4 层的唯一前提。
* 真机像素 + 模拟 ``captureScale`` ⇒ 能验证重采样在**真实截图**上成立，而不是只
  在替身里成立。**注意**：本机系统 DPI 是 100%，没有真的切到 125%（那要改系统设置
  并注销），所以「125% 捕获」的 provenance 是模拟的，模板像素是从真机 1.25 倍放大来的。

跑法：RPA_DESKTOP_E2E=1 ./.venv/Scripts/python.exe \
      .harness/spike/probe_m81_wechat_real.py
"""

from __future__ import annotations

import base64
import copy
import ctypes
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS: list[dict] = []

#: 导航栏「通讯录」人形图标相对窗口左上角的偏移（纯灰矢量图标，不含文字/个人信息）。
NAV_CONTACTS = (41, 148)

_WECHAT_EXE_TOKENS = ("weixin.exe", "wechat.exe", "wxwork.exe")


def record(name: str, ok: bool | None, detail: str) -> None:
    state = "PASS" if ok is True else ("FAIL" if ok is False else "SKIP")
    RESULTS.append({"check": name, "state": state, "detail": detail})
    print(f"{state} | {name} — {detail}", flush=True)


def run_cli(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "rpa_core.cli", *argv],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    return proc.returncode, (proc.stdout or "").strip()


def write_element(workflows: Path, name: str, doc: dict) -> None:
    target = workflows / "demo" / "elements"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{name}.json").write_bytes(
        json.dumps(doc, ensure_ascii=False).replace("\r\n", "\n").encode("utf-8")
    )


def read_element(workflows: Path, name: str) -> dict | None:
    path = workflows / "demo" / "elements" / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def live_verify(workflows: Path, name: str, *, shot: bool = False) -> dict | None:
    argv = ["elements", "verify", "--flow", "demo", "--workflows", str(workflows),
            "--live", "--mode", "flash"]
    if shot:
        argv.append("--shot")
    argv.append(name)
    code, out = run_cli(argv)
    if code != 0:
        record(f"live verify {name}", False, f"rc={code} out={out[:200]}")
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        record(f"live verify {name}", False, f"non-JSON {out[:200]} ({exc})")
        return None


def _process_path(pid: int) -> str:
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    from ctypes import wintypes

    buffer = ctypes.create_unicode_buffer(4096)
    size = wintypes.DWORD(4096)
    ok = k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
    k32.CloseHandle(handle)
    return buffer.value if ok else ""


def find_wechat_window() -> tuple[int, str, str] | None:
    """按**进程可执行文件名**找微信主窗口。

    ★ 不能只靠窗口类名/标题：微信 4.x 是 Qt 窗口（``Qt51514QWindowIcon``），
    可执行文件叫 ``Weixin.exe``（不是 ``WeChat.exe``）。M79 探针就是在这里判漏了。
    """
    import win32gui
    import win32process

    best: tuple[int, str, str] | None = None
    best_area = 0

    def cb(hwnd, _):
        nonlocal best, best_area
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe = _process_path(pid).lower()
        if not any(token in exe for token in _WECHAT_EXE_TOKENS):
            return True
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width, height = right - left, bottom - top
        if width < 200 or height < 200:
            return True
        if width * height > best_area:
            best_area = width * height
            best = (hwnd, win32gui.GetClassName(hwnd), exe)
        return True

    win32gui.EnumWindows(cb, None)
    return best


def template_is_textured(template_b64: str) -> tuple[bool, str]:
    """退化模板（纯色块）匹配到哪都像，用它验证等于假绿 —— 先自查。"""
    try:
        from PIL import Image, ImageStat

        with Image.open(io.BytesIO(base64.b64decode(template_b64))) as img:
            stat = ImageStat.Stat(img.convert("L"))
            stddev = stat.stddev[0]
        return stddev >= 3.0, f"灰度标准差={stddev:.2f}"
    except Exception as exc:  # noqa: BLE001
        return False, f"模板解码失败 {type(exc).__name__}: {exc}"


def make_scaled_variant(doc: dict, factor: float) -> dict:
    """把模板按 ``factor`` 放大并改 provenance，模拟「在 factor 倍缩放下捕获」。"""
    from PIL import Image

    scaled = copy.deepcopy(doc)
    locator = scaled["selector"]["locator"]
    with Image.open(io.BytesIO(base64.b64decode(locator["imageTemplate"]))) as img:
        size = img.size
        big = img.resize((int(size[0] * factor), int(size[1] * factor)), Image.LANCZOS)
    buffer = io.BytesIO()
    big.save(buffer, format="PNG")
    locator["imageTemplate"] = base64.b64encode(buffer.getvalue()).decode("ascii")
    locator["templateSize"] = [big.width, big.height]
    locator["captureScale"] = float(factor)
    locator["captureDpi"] = int(round(96 * factor))
    locator["templateHash"] = f"simulated-{factor}"
    for cand in scaled["selector"].get("candidates", []):
        if cand.get("kind") == "image":
            cand["locator"] = copy.deepcopy(locator)
    return scaled


def main() -> int:
    if os.environ.get("RPA_DESKTOP_E2E") != "1":
        print("需要 RPA_DESKTOP_E2E=1（会抢前台）", file=sys.stderr)
        return 2
    if sys.platform != "win32":
        print("仅 Windows", file=sys.stderr)
        return 2

    workflows = ROOT / "rpa-m81-real"
    if workflows.exists():
        import shutil

        shutil.rmtree(workflows)
    workflows.mkdir(parents=True, exist_ok=True)

    import win32gui

    # ---- 1. 找窗口 ----------------------------------------------------------
    found = find_wechat_window()
    if found is None:
        record("微信窗口识别", None, "没有可见且 ≥200x200 的微信窗口（Weixin/WeChat/WXWork）")
        return _report()
    hwnd, cls, exe = found
    record("微信窗口识别", True, f"hwnd={hwnd} class={cls} exe={Path(exe).name}")

    user32 = ctypes.windll.user32
    dpi = user32.GetDpiForWindow(hwnd)
    record("窗口 DPI 取证", True, f"dpi={dpi} 缩放≈{round(dpi / 96 * 100)}%")

    # ---- 2. 前置 + 截图 ----------------------------------------------------
    try:
        win32gui.ShowWindow(hwnd, 3)  # SW_MAXIMIZE 会改布局，这里用 SW_SHOW
    except Exception:  # noqa: BLE001
        pass
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception as exc:  # noqa: BLE001
        record("微信前置", None, f"前台锁定拒绝: {exc}")
    time.sleep(0.5)
    foreground = win32gui.GetForegroundWindow() == hwnd
    rect = win32gui.GetWindowRect(hwnd)
    left, top = rect[0], rect[1]
    record(
        "微信前置",
        True if foreground else None,
        f"foreground={foreground} rect={rect}",
    )

    # ---- 3. UIA 取证：说明为什么必须走图像 ---------------------------------
    try:
        from pywinauto import Desktop

        wrapper = Desktop(backend="uia").window(handle=hwnd)
        kinds = set()
        for item in wrapper.descendants()[:400]:
            kinds.add(getattr(item.element_info, "control_type", None))
        interactive = {k for k in kinds if k in ("Button", "Edit", "ListItem", "TabItem")}
        record(
            "UIA 自绘取证",
            True,
            f"descendants controlType={sorted(x for x in kinds if x)[:8]} "
            f"交互控件={sorted(interactive) or '无'}",
        )
    except Exception as exc:  # noqa: BLE001
        record("UIA 自绘取证", None, f"UIA 不可用: {type(exc).__name__}: {exc}")

    # ---- 4. 显式图像捕获 ---------------------------------------------------
    px, py = left + NAV_CONTACTS[0], top + NAV_CONTACTS[1]
    code, out = run_cli([
        "capture", "desktop", "--point", f"{px},{py}",
        "--window-handle", str(hwnd), "--image",
        "--save-as", "wechat_contacts", "--flow", "demo", "--workflows", str(workflows),
    ])
    if code != 0:
        record("显式图像捕获", False, f"rc={code} out={out[:240]}")
        return _report()

    captured = json.loads(out)
    write_element(workflows, "wechat_contacts", captured)
    locator = captured.get("selector", {}).get("locator", {})
    source_rect = locator.get("sourceRect")
    shape_ok = (
        locator.get("driver") == "image"
        and locator.get("backend") == "image"
        and locator.get("templateSchema") == 2
        and isinstance(locator.get("captureDpi"), int)
        and locator["captureDpi"] > 0
        and isinstance(locator.get("captureScale"), (int, float))
        and locator["captureScale"] > 0
        and isinstance(source_rect, list)
        and len(source_rect) == 4
    )
    record(
        "V2 provenance 字段",
        shape_ok,
        f"schema={locator.get('templateSchema')} dpi={locator.get('captureDpi')} "
        f"scale={locator.get('captureScale')} size={locator.get('templateSize')} "
        f"sourceRect={source_rect}",
    )
    if not shape_ok:
        return _report()

    cand_counts = [c.get("matchedCount") for c in captured.get("selector", {}).get("candidates", [])
                   if c.get("kind") == "image"]
    record(
        "proposal 不冒充实测",
        True if cand_counts and all(c == 0 for c in cand_counts) else False,
        f"image 候选 matchedCount={cand_counts}（应为 0）",
    )

    textured, detail = template_is_textured(locator.get("imageTemplate", ""))
    record("模板非退化自查", textured, detail)
    if not textured:
        record("100% 活体校验", None, "模板是纯色块，匹配到哪都成立，本轮不作数")
        return _report()

    # ---- 5. 100% 真机命中 --------------------------------------------------
    result = live_verify(workflows, "wechat_contacts", shot=True)
    if result is None:
        return _report()
    hit_rect = result.get("rect")
    same_rect = (
        isinstance(hit_rect, dict)
        and [hit_rect.get("left"), hit_rect.get("top")] == [source_rect[0], source_rect[1]]
        and [hit_rect.get("width"), hit_rect.get("height")] == [source_rect[2], source_rect[3]]
    )
    record(
        "100% 活体命中",
        bool(result.get("count") == 1 and same_rect),
        f"count={result.get('count')} rect={hit_rect} sourceRect={source_rect}",
    )
    shot = result.get("shot") or {}
    box = shot.get("box") or {}
    origin = shot.get("windowOrigin") or [0, 0]
    box_ok = (
        box.get("x") == float(source_rect[0] - origin[0])
        and box.get("y") == float(source_rect[1] - origin[1])
    )
    record(
        "--shot 回执同源",
        box_ok and bool(shot.get("dataUrl")),
        f"box={box} windowOrigin={shot.get('windowOrigin')} usedScale={result.get('usedScale')}",
    )
    record(
        "100% 尺度证据",
        result.get("usedScale") == 1.0,
        f"usedScale={result.get('usedScale')}（应为 1.0）",
    )

    # ---- 6. 跨 DPI：1.25 倍模板 → 重采样回 0.8 -----------------------------
    scaled = make_scaled_variant(captured, 1.25)
    write_element(workflows, "wechat_contacts_125", scaled)
    result125 = live_verify(workflows, "wechat_contacts_125")
    if result125 is None:
        return _report()
    hit125 = result125.get("rect")
    same125 = (
        isinstance(hit125, dict)
        and [hit125.get("left"), hit125.get("top")] == [source_rect[0], source_rect[1]]
    )
    record(
        "125% 模板重采样命中",
        bool(result125.get("count") == 1 and same125 and result125.get("usedScale") == 0.8),
        f"count={result125.get('count')} rect={hit125} usedScale={result125.get('usedScale')}"
        "（期望 0.8 且矩形同 100%）",
    )

    # ---- 7. 对照：关掉重采样必须掉 -----------------------------------------
    control = make_scaled_variant(captured, 1.25)
    control["selector"]["locator"]["captureScale"] = 1.0
    for cand in control["selector"].get("candidates", []):
        if cand.get("kind") == "image":
            cand["locator"] = copy.deepcopy(control["selector"]["locator"])
    write_element(workflows, "wechat_contacts_ctl", control)
    result_ctl = live_verify(workflows, "wechat_contacts_ctl")
    if result_ctl is None:
        return _report()
    record(
        "对照：不重采样应失配",
        result_ctl.get("count") == 0,
        f"count={result_ctl.get('count')}（放大模板直配，期望 0）",
    )

    return _report()


def _report() -> int:
    passed = sum(1 for row in RESULTS if row["state"] == "PASS")
    failed = sum(1 for row in RESULTS if row["state"] == "FAIL")
    skipped = sum(1 for row in RESULTS if row["state"] == "SKIP")
    print(f"\nM81 WECHAT REAL: {passed} pass / {failed} fail / {skipped} skip", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
