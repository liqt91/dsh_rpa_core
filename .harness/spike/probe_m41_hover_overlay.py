"""M41 探针：桌面捕获高亮框「躲不躲鼠标」的实测。

维护者 2026-09-28 报（第二轮之后）：

    捕获元素的框跟影刀的一样，躲着鼠标，否则影响其他元素的捕捉

两个候选机制，本探针分别证伪/证实：

- **机制 A（视觉遮挡）**：框画在元素 rect 上，鼠标在元素内 ⇒ 指针就压在框线/半透明
  填充上。元素很小（图标/复选框）时框线甚至跨过指针。
- **机制 B（hit-test 劫持）**：`_HoverOverlay` 是 `WS_EX_TOPMOST | WS_EX_TRANSPARENT`
  的分层窗口。鼠标消息穿透（`WS_EX_TRANSPARENT` 只管这个），但 **UIA 的
  `ElementFromPoint` 是否穿透**是另一回事——`_hover_hit` 里那句
  `if hwnd != exclude_hwnd` 说明作者早已遇到「UIA 把 overlay 返回成顶层元素」。
  若成立：鼠标一旦落在 overlay 的 region 上，`rect` 取不到 → 走 DFS 兜底 → 再失败
  就**退化成整个窗口矩形**——用户看到框突然铺满窗口，且捕到的是窗口而不是元素。

方法（**A/B 同点对比**，唯一变量是 overlay 在不在）：

1. 造一个真实目标窗口（tkinter，3×3 网格，便于肉眼确认框住的是哪一格）；
2. 在窗口 rect 上取采样点：网格中心 + **紧贴框线的 4 个边中点**（BORDER=3 的线上）；
3. A 臂（无 overlay）与 B 臂（`show_rect` 后）对**同一批点**各调一次
   `_element_from_point`，比对返回的 hwnd / bounding rectangle；
4. 截屏存 PNG，供人眼判「框是否压住鼠标」。

跑法：``uv run python .harness/spike/probe_m41_hover_overlay.py``
产物：``.harness/spike/_m41_overlay_ab.json`` + ``.harness/spike/_m41_overlay_shot.png``
"""

from __future__ import annotations

import ctypes
import json
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rpa_core.capture.desktop_agent import (  # noqa: E402
    _element_from_point,
    _hover_hit,
    _HoverOverlay,
)

GA_ROOT = 2
OUT_JSON = Path(__file__).with_name("_m41_overlay_ab.json")
OUT_PNG = Path(__file__).with_name("_m41_overlay_shot.png")


def _log(text: str = "") -> None:
    print(text, flush=True)


def _make_target():
    """真实目标窗口：3×3 网格（框住哪格一眼可辨）。"""
    import tkinter as tk

    root = tk.Tk()
    root.title("rpa_hover_probe_target")
    root.geometry("420x300+320+240")
    for row in range(3):
        for col in range(3):
            tk.Label(
                root, text=f"cell {row}-{col}", width=12, height=3,
                relief="solid", borderwidth=1,
            ).grid(row=row, column=col)
    root.update()
    root.update_idletasks()
    time.sleep(0.3)
    return root


def _rect_of(hwnd: int) -> wintypes.RECT:
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect


def _sample_points(rect: wintypes.RECT) -> list[tuple[str, int, int]]:
    """采样点：9 个网格中心 + 4 个框线中点（BORDER=3 的内缩线上）。"""
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    width, height = right - left, bottom - top
    points: list[tuple[str, int, int]] = []
    for i in range(1, 4):
        for j in range(1, 4):
            points.append(
                (f"grid{i}{j}", left + width * i // 4, top + height * j // 4)
            )
    mid_x, mid_y = (left + right) // 2, (top + bottom) // 2
    border = _HoverOverlay.BORDER
    points += [
        ("edge-top", mid_x, top + border - 1),
        ("edge-bottom", mid_x, bottom - border),
        ("edge-left", left + border - 1, mid_y),
        ("edge-right", right - border, mid_y),
    ]
    return points


def _probe_point(x: int, y: int) -> dict:
    """单点 UIA 命中：返回 hwnd / rect / 异常。"""
    try:
        info = _element_from_point(x, y)
        hwnd = int(info.handle or 0)
        rect = info.rectangle
        return {
            "hwnd": hwnd,
            "rect": [rect.left, rect.top, rect.right, rect.bottom],
            "controlType": str(getattr(info, "control_type", "") or ""),
            "class": str(getattr(info, "class_name", "") or ""),
        }
    except Exception as exc:  # noqa: BLE001 - 探针只报告，不吞
        return {"error": f"{type(exc).__name__}: {exc}"}


def _screenshot(rect: wintypes.RECT, pad: int = 40) -> str | None:
    from PIL import ImageGrab

    bbox = (
        max(rect.left - pad, 0), max(rect.top - pad, 0),
        rect.right + pad, rect.bottom + pad,
    )
    ImageGrab.grab(bbox=bbox).save(OUT_PNG)
    return str(OUT_PNG)


def main() -> int:
    import pythoncom

    pythoncom.CoInitialize()
    # overlay 是顶层置顶窗口，会短暂出现在屏幕上（探针结束即销毁）
    _log("[M41] 造目标窗口（屏幕上会短暂出现一个 3×3 网格窗体）…")
    target = _make_target()
    child = int(target.winfo_id())
    hwnd = int(ctypes.windll.user32.GetAncestor(child, GA_ROOT)) or child
    rect = _rect_of(hwnd)
    _log(f"[M41] target hwnd={hwnd} rect={rect.left},{rect.top},{rect.right},{rect.bottom}")

    points = _sample_points(rect)
    report: dict = {"target": {"hwnd": hwnd, "rect": [rect.left, rect.top, rect.right, rect.bottom]}}

    # ---- A 臂：无 overlay ----------------------------------------------------
    _log("\n§1 A 臂（无 overlay）")
    arm_a = {name: _probe_point(x, y) for name, x, y in points}
    for name, x, y in points:
        hit = arm_a[name]
        _log(f"    {name:<12} ({x:>5},{y:>5}) hwnd={hit.get('hwnd')} "
             f"class={hit.get('class')!r} err={hit.get('error')}")
    report["arm_a"] = arm_a

    # ---- B 臂：overlay 显示中 -------------------------------------------------
    _log("\n§2 B 臂（overlay 显示中，框 = 目标窗口 rect）")
    overlay = _HoverOverlay()
    overlay.show_rect(rect.left, rect.top, rect.right, rect.bottom)
    overlay.pump()
    time.sleep(0.25)
    overlay.pump()
    arm_b = {name: _probe_point(x, y) for name, x, y in points}
    for name, x, y in points:
        hit = arm_b[name]
        _log(f"    {name:<12} ({x:>5},{y:>5}) hwnd={hit.get('hwnd')} "
             f"class={hit.get('class')!r} err={hit.get('error')}")
    report["arm_b"] = arm_b
    report["overlay_hwnd"] = overlay.hwnd

    # ---- §3 差异：overlay 是否参与命中 ---------------------------------------
    _log("\n§3 A/B 差异（唯一变量 = overlay 在不在）")
    diffs = []
    hijacked = []
    for name, x, y in points:
        a, b = arm_a[name], arm_b[name]
        if a != b:
            diffs.append({"point": name, "xy": [x, y], "a": a, "b": b})
            if b.get("hwnd") == overlay.hwnd:
                hijacked.append(name)
    _log(f"    差异点：{len(diffs)}/{len(points)}；直接命中 overlay 的点：{hijacked or '无'}")
    for item in diffs:
        _log(f"    · {item['point']} {item['xy']}: A hwnd={item['a'].get('hwnd')} "
             f"→ B hwnd={item['b'].get('hwnd')} ({item['b'].get('class')!r})")
    report["diffs"] = diffs
    report["hijacked_points"] = hijacked

    # ---- §4 hover 命中链路：框线上一点会不会退化成窗口矩形 --------------------
    _log("\n§4 `_hover_hit(exclude_hwnd=overlay)` 在各点取到的 rect（退化=窗口矩形）")
    degraded = []
    for name, x, y in points:
        hit_rect, root, leaf, scoped = _hover_hit(x, y, overlay.hwnd, allow_scoped=True)
        if hit_rect is None:
            shown = None
        else:
            shown = [hit_rect.left, hit_rect.top, hit_rect.right, hit_rect.bottom]
        is_window_rect = shown == [rect.left, rect.top, rect.right, rect.bottom]
        if is_window_rect:
            degraded.append(name)
        _log(f"    {name:<12} rect={shown} scoped={scoped} 退化窗口矩形={is_window_rect}")
        overlay.pump()
    report["degraded_points"] = degraded

    # ---- §5 截屏 ------------------------------------------------------------
    _log("\n§5 截屏（人眼判：框线是否跨过采样点）")
    try:
        shot = _screenshot(rect)
        _log(f"    已存：{shot}")
    except Exception as exc:  # noqa: BLE001
        _log(f"    截屏不可用：{type(exc).__name__}: {exc}")

    # ---- §6 真实指针逐点录屏：指针点是否被框线/填充压住 ----------------------
    # 判据：把真实光标移到采样点，跑一帧 hover 高亮，截屏并**在指针处画十字**。
    # 若可见像素（红框线/红填充）压在十字上 ⇒ 框真的挡了鼠标。
    _log("\n§6 真实指针逐点（每点一张 PNG，指针位置已画十字）")
    covered: list[str] = []
    shots_dir = Path(__file__).with_name("_m41_hover_shots")
    shots_dir.mkdir(exist_ok=True)
    cursor = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor))
    original_cursor = (cursor.x, cursor.y)
    try:
        from PIL import Image, ImageDraw, ImageGrab

        for name, x, y in points:
            ctypes.windll.user32.SetCursorPos(x, y)
            time.sleep(0.05)
            hit_rect, _root, _leaf, _scoped = _hover_hit(
                x, y, overlay.hwnd, allow_scoped=True
            )
            if hit_rect is not None:
                overlay.show_rect(
                    hit_rect.left, hit_rect.top, hit_rect.right, hit_rect.bottom
                )
            overlay.pump()
            time.sleep(0.08)
            overlay.pump()
            pad = 40
            bbox = (
                max(rect.left - pad, 0), max(rect.top - pad, 0),
                rect.right + pad, rect.bottom + pad,
            )
            img = ImageGrab.grab(bbox=bbox)
            draw = ImageDraw.Draw(img)
            px, py = x - bbox[0], y - bbox[1]
            draw.line([(px - 9, py), (px + 9, py)], fill=(0, 0, 255), width=1)
            draw.line([(px, py - 9), (px, py + 9)], fill=(0, 0, 255), width=1)
            path = shots_dir / f"{name}.png"
            img.save(path)
            # 判据：指针点周围 2px 内是否有红框线像素（红框色 ~ (255,59,48)）
            red_near = 0
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    try:
                        r, g, b = img.getpixel((px + dx, py + dy))[:3]
                    except Exception:  # noqa: BLE001
                        continue
                    if r > 180 and g < 110 and b < 110:
                        red_near += 1
            flag = "覆盖" if red_near else "空闲"
            if red_near:
                covered.append(name)
            _log(f"    {name:<12} 框={hit_rect and (hit_rect.left, hit_rect.top, hit_rect.right, hit_rect.bottom)} "
                 f"指针±2px 内红像素={red_near:>2} → {flag}  ({path.name})")
    except Exception as exc:  # noqa: BLE001
        _log(f"    逐点录屏不可用：{type(exc).__name__}: {exc}")
    finally:
        ctypes.windll.user32.SetCursorPos(*original_cursor)
    report["cursor_covered_points"] = covered
    report["shots_dir"] = str(shots_dir)
    _log(f"    → 指针被红框覆盖的点：{covered or '无'}")

    # ---- §7 净外移量扫描：验证「框线带落在元素外」的实测效果 --------------------
    # 本扫描走**真实 `show_rect` 路径**（它内部已经外扩 OVERLAY_OUTSET），通过预先在传入
    # 的 rect 上做等量反向偏移，模拟出不同的**净外移量** e：
    #     净外移 = 传入偏移量 + OVERLAY_OUTSET
    # e = 0 即旧实现（框线带占元素最外 3px）；e = OVERLAY_OUTSET 就是当前实现态。
    # 判据：指针点 ±2px 内不得有红色框线像素——解析解 e ≥ BORDER + 2 = 5。
    _log("\n§7 净外移量扫描（走真实 show_rect；判据：指针 ±2px 内无红像素）")
    from rpa_core.capture.desktop_agent import OVERLAY_OUTSET

    nets = (0, 3, 5, 8)
    scan: dict[str, dict] = {}
    for net in nets:
        delta = OVERLAY_OUTSET - net  # 传入前先加 delta，抵消 show_rect 内部的外扩
        rows = []
        for name, x, y in points:
            ctypes.windll.user32.SetCursorPos(x, y)
            time.sleep(0.03)
            hit_rect, _r, _l, _s = _hover_hit(x, y, overlay.hwnd, allow_scoped=True)
            if hit_rect is not None:
                overlay.show_rect(
                    hit_rect.left + delta, hit_rect.top + delta,
                    hit_rect.right - delta, hit_rect.bottom - delta,
                )
            overlay.pump()
            time.sleep(0.05)
            overlay.pump()
            img = ImageGrab.grab(bbox=(x - 12, y - 12, x + 13, y + 13))
            pixels = img.load()
            nearest = None
            for ix in range(img.width):
                for iy in range(img.height):
                    r, g, b = pixels[ix, iy][:3]
                    if r > 180 and g < 110 and b < 110:
                        dist = max(abs(ix - 12), abs(iy - 12))
                        if nearest is None or dist < nearest:
                            nearest = dist
            rows.append((name, nearest))
        blocked = [n for n, d in rows if d is not None and d <= 2]
        nearest_all = [d for _, d in rows if d is not None]
        scan[str(net)] = {"rows": rows, "blocked": blocked}
        tag = "（= 当前实现态）" if net == OVERLAY_OUTSET else (
            "（= 旧实现）" if net == 0 else ""
        )
        _log(f"    净外移={net:>2}{tag} 框线带占元素外 [{net - 3},{net})"
             f" 压线点={len(blocked):>2}/{len(points)}"
             f" 最近红像素距离={min(nearest_all) if nearest_all else '∞'}")
        if blocked:
            _log(f"        压线点：{blocked}")
    report["net_outset_scan"] = scan

    overlay.destroy()
    target.destroy()
    pythoncom.CoUninitialize()
    OUT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    _log(f"\n[M41] 报告：{OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
