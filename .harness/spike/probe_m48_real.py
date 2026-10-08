"""M48 真机复验：D1 祖先链 / D2 控件级 matchMode / D3 锚点，在**真实 UIA 树**上的行为。

为什么需要这一支（契约测试已 39 项、负向验证 19 处全绿）：
`tests/contract/test_desktop_locator_path.py` 全部走 `FakeElement` / `FakeInfo` 替身。
替身只能证明「我们写的算法在给定的键上是对的」，证明不了：

  ① 真实 UIA provider 到底吐不吐 `class_name` / `automation_id`（替身是我们自己填的）；
  ② 真实祖先链的**形状与深度**——WinForms 在 Form 与控件之间插了几层（替身里没有）；
  ③ `descendants()` 在真实树上是否真的能收到 path 收窄要的那一级；
  ④ matchMode 的 `contains`/`regex` 在真实字符串上是否按预期命中（含大小写、Unicode）；
  ⑤ anchor 前置解析在真实树上找不到时，是否真的走到 `ANCHOR_NOT_FOUND` 而不是静默降级。

本文件 **不冒充** 契约测试的替代品——两者证明的是不同的事（算法正确 / 现场成立）。

运行：`RPA_DESKTOP_E2E=1 ./.venv/Scripts/python.exe .harness/spike/probe_m48_real.py`
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
sys.path.insert(0, str(ROOT / "src"))

import desktop_fixture  # noqa: E402

from rpa_core.capture import desktop_agent  # noqa: E402
from rpa_core.executors.desktop import AnchorNotResolved, DesktopExecutor  # noqa: E402
from rpa_core.model.desktop import DesktopLocator  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    reason = desktop_fixture.fixture_unavailable_reason()
    if reason is not None:
        print(f"夹具不可用：{reason}")
        return 2

    exe = desktop_fixture.compile_demo_app(Path(tempfile.mkdtemp(prefix="m48real-")))
    title = desktop_fixture.APP_TITLE
    desktop_fixture.kill_demo_apps()
    proc = subprocess.Popen([str(exe)])
    try:
        hwnd = desktop_fixture.wait_for_window(title)
        desktop_fixture.warmup_uia(title)
        desktop_fixture.force_foreground(title)
        print(f"靶子已起：hwnd={hwnd} title={title!r}")

        # 用元素中心点做屏幕级捕获（走 capture_at 的父链上溯分支）
        _run_window_scope_probe(hwnd)
        _run_executor_probe(hwnd)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        desktop_fixture.kill_demo_apps()

    print("\n=== 结果 ===")
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)} / {len(RESULTS)} 通过")
    if failed:
        print("失败项：")
        for name in failed:
            print("  -", name)
        return 1
    print("真机复验全部通过。")
    return 0


# --------------------------------------------------------------------------
# ① 捕获侧：真实祖先链的形状与内容
# --------------------------------------------------------------------------
def _run_window_scope_probe(hwnd: int) -> None:
    section("D1 捕获侧：真实祖先链（window scope hit-test）")

    from pywinauto import Desktop

    window = Desktop(backend="uia").window(handle=hwnd)

    # 挑「有多级祖先」的目标：真机实测只有 ComboBox 下拉箭头（打开）与标题栏按钮
    # 的祖先链 > 1 级。Submit / Count 等按钮**直接挂在 Form 下**（WinForms 无中间 Pane），
    # DFS 链只有根窗口一级 ⇒ 剥根后为空 ⇒ 不写 path（这是**正确**行为，见下面 ①-e）。
    multi = None
    for el in window.descendants(control_type="Button"):
        try:
            rect = el.rectangle()
        except Exception:
            continue
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        infos: list = []
        desktop_agent._window_scope_hit(hwnd, cx, cy, path_out=infos)
        if len(infos) > 1:
            multi = (el, cx, cy)
            break

    if multi is None:
        check("找到多级祖先链目标", False, "真实树上没有链 > 1 级的按钮，无法验证剥根语义")
    else:
        el, cx, cy = multi
        check("找到多级祖先链目标", True, f"{el.element_info.name!r}")

    # ① Submit（单级链）：path 为空是正确行为——祖先链只有根窗口一级时不携带收窄信息。
    btn = window.descendants(control_type="Button", title="Submit")
    if not btn:
        check("定位 Submit 按钮", False, "真实树上找不到")
    else:
        rect = btn[0].rectangle()
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        desc = desktop_agent.capture_in_window(hwnd, cx, cy)
        if desc is None:
            check("window scope 命中", False, "capture_in_window 返回 None")
        else:
            check("window scope 命中", True)
            locator = desc["selector"]["locator"]
            # ①-e 单级链 ⇒ path 必须为空（不写字段），且无 path 时仍能命中目标。
            check(
                "单级链目标 path 为空（根级不携带收窄信息）",
                "path" not in locator,
                f"locator={locator}",
            )
            window_wrap = DesktopExecutor._window_by_handle(hwnd)
            found = DesktopExecutor._find(
                window_wrap, DesktopLocator.model_validate(locator)
            )
            check("无 path 回放仍命中目标", len(found) == 1, f"命中 {len(found)} 个")

    # ② 多级链目标：path 必须非空、不含根窗口级、且回放命中。
    if multi is not None:
        el, cx, cy = multi
        desc = desktop_agent.capture_in_window(hwnd, cx, cy)
        locator = desc["selector"]["locator"] if desc else {}
        path = locator.get("path")
        check("多级链目标 locator 带 path", bool(path), f"path={path}")
        if path:
            allowed = {"controlType", "automationId", "name", "className"}
            bad = [step for step in path if not step or not set(step) <= allowed]
            check("每级非空且键集合法", not bad, f"越界级={bad}")

            # ①-b 核心断言：path 里**不含根窗口级**（2026-10-08 修复的那个真 bug）。
            root_aid = getattr(el.element_info, "automation_id", None)
            has_window_level = any(s.get("controlType") == "Window" for s in path)
            check(
                "path 不含根窗口级（修复点）",
                not has_window_level,
                f"path={path}",
            )
            with_class = sum(1 for step in path if "className" in step)
            print(f"    path 共 {len(path)} 级；其中带 className 的 {with_class} 级")
            print(f"    path = {path}")

            # ①-d 用这条真机生成的 path 回放给执行器：必须仍能命中目标。
            window_wrap = DesktopExecutor._window_by_handle(hwnd)
            found = DesktopExecutor._find(
                window_wrap, DesktopLocator.model_validate(locator)
            )
            check("真机多级 path 回放命中目标", len(found) == 1, f"命中 {len(found)} 个")


# --------------------------------------------------------------------------
# ② 执行器侧：D2 matchMode + D3 anchor，直接在真实树上
# --------------------------------------------------------------------------
def _run_executor_probe(hwnd: int) -> None:
    cls = DesktopExecutor
    window = cls._window_by_handle(hwnd)
    if window is None:
        check("构造 window 包装", False)
        return

    def find(loc: dict) -> list:
        return cls._find(window, DesktopLocator.model_validate(loc))

    section("D2 控件级 matchMode（真实 UIA 字符串）")

    # exact：Submit 按钮（controlType=Button, name=Submit）
    exact = find({"backend": "uia", "controlType": "Button", "name": "Submit"})
    check("exact 命中 Submit", len(exact) == 1, f"命中 {len(exact)}")

    # contains：真实 name「Submit」含 "ubmi"
    contains = find(
        {"backend": "uia", "controlType": "Button", "name": "ubmi", "matchMode": "contains"}
    )
    check("contains 命中 Submit", len(contains) == 1, f"命中 {len(contains)}")

    # regex：真实 name「Submit」匹配 ^Sub
    regex = find(
        {"backend": "uia", "controlType": "Button", "name": "^Sub", "matchMode": "regex"}
    )
    check("regex 命中 Submit", len(regex) == 1, f"命中 {len(regex)}")

    # 反证：同一值在 exact 下**必须**不命中（证明 matchMode 真参与了过滤，不是恒真）
    exact_miss = find({"backend": "uia", "controlType": "Button", "name": "ubmi"})
    check("同值 exact 不命中（matchMode 真生效）", len(exact_miss) == 0, f"命中 {len(exact_miss)}")

    # contains 作用在 automationId 上（靶子按钮的 AutomationId 未设 ⇒ 用 title 反推不合适，
    # 改用一个确实设了 Name 的控件：queryInput 的 name 是空串，故这条用 controlType 限定）
    print("    注：靶子控件的 AutomationId 未显式设置，automationId 路径由契约测试覆盖")

    section("D1 path 收窄（真实树）")

    # 正例：path 给一级**真实存在且是目标容器**的祖先。真机实测 Submit 按钮的祖先里
    # 没有中间容器，但它所在的 Form 之下也无别的可描述级——故这里改用一个**确实存在的
    # 后代级**：把 path 指向「Submit 的某个祖先级」用真实树验证会更脆；改用
    # `path=[{controlType: 'Window'}]` 会被**有意**拒（窗口不属于自己的后代），
    # 那是修复后的正确语义，故不再断言它命中。
    #
    # 真正要钉的是：**path 指向不存在的容器 ⇒ 收窄为空 ⇒ 不命中**（下面负例），
    # 以及**产侧不再生成根窗口级**（上面的捕获侧探针已覆盖）。
    no_path = find(
        {
            "backend": "uia",
            "controlType": "Button",
            "name": "Submit",
            "path": [{"automationId": "no-such-container-xyz"}],
        }
    )
    check("path 指向不存在容器 ⇒ 不命中", len(no_path) == 0, f"命中 {len(no_path)}")

    # 负例（钉「谓词没被放宽」）：path 指向根窗口自身 ⇒ 收窄为空 ⇒ 不命中。
    # 这条在修复前会「命中」（旧 bug 的谓词把自身也算上），修复后必须不命中。
    self_path = find(
        {
            "backend": "uia",
            "controlType": "Button",
            "name": "Submit",
            "path": [{"controlType": "Window"}],
        }
    )
    check("path=[Window]（指向根自身）⇒ 不命中（谓词未放宽）", len(self_path) == 0,
          f"命中 {len(self_path)}")

    section("D3 锚点前置（真实树）")

    # 正例：anchor 用真实存在的控件（Submit 按钮），目标用真实存在的另一控件（Count 按钮）
    ok_anchor = find(
        {
            "backend": "uia",
            "controlType": "Button",
            "name": "Count",
            "anchor": {
                "locator": {"backend": "uia", "controlType": "Button", "name": "Submit"}
            },
        }
    )
    check("anchor 命中 ⇒ 正常找目标", len(ok_anchor) == 1, f"命中 {len(ok_anchor)}")

    # 负例：anchor 指向不存在的控件 ⇒ 必须抛 AnchorNotResolved（不是静默降级）
    raised = False
    try:
        find(
            {
                "backend": "uia",
                "controlType": "Button",
                "name": "Count",
                "anchor": {
                    "locator": {
                        "backend": "uia",
                        "controlType": "Button",
                        "name": "no-such-anchor-xyz",
                    }
                },
            }
        )
    except AnchorNotResolved:
        raised = True
    except Exception as exc:  # noqa: BLE001
        check("anchor 未命中抛 AnchorNotResolved", False, f"抛了别的：{type(exc).__name__}")
    else:
        check("anchor 未命中抛 AnchorNotResolved", raised)

    # 关键反证：anchor 未命中时**目标本身是存在的**（Count 按钮真在），
    # 若不抛异常就会返回 1 个 —— 这条断言排除了「恰好目标也不存在」的伪证。
    target_alone = find({"backend": "uia", "controlType": "Button", "name": "Count"})
    check(
        "反证：无 anchor 时目标确实存在（说明上面的异常来自 anchor）",
        len(target_alone) == 1,
        f"命中 {len(target_alone)}",
    )


if __name__ == "__main__":
    if os.environ.get("RPA_DESKTOP_E2E") != "1":
        print("真实桌面 E2E 会弹窗抢焦点；设 RPA_DESKTOP_E2E=1 启用")
        sys.exit(2)
    sys.exit(main())
