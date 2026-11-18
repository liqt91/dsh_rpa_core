"""M95 取证探针：任务栏搜索按钮（元素2）校验为何 count=0。

三件事分开量：
1. 元素2 记下的 windowHandle 是否被 visible_top_windows() 收进去（怀疑 shell_traywnd 被过滤）；
2. 直接从该 HWND 构造 UIAWrapper 再 _find，看定位器本身是否还成立（排除 name/automationId 漂移）；
3. 走用户实际入口 verify_element(doc)，记录耗时与结果（"过了一会儿"是否来自遍历 400+ 窗口）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from rpa_core.capture.foreground_window import _Win32Windows, visible_top_windows

ROOT = Path(r"D:\Users\Administrator\Documents\代码\rpa_core")
DOC = ROOT / "examples/api-usage/elements/元素2.json"


def main() -> None:
    doc = json.loads(DOC.read_text(encoding="utf-8"))
    expected = doc["metadata"]["windowHandle"]
    print(f"element={doc['name']} expected_hwnd={expected}")
    print(f"locator={doc['selector']['locator']}")

    api = _Win32Windows()
    rows = api.enumerate_top_windows()
    print(f"\nenumerate_top_windows: {len(rows)} 项")
    for hwnd, cls, title, proc, visible in rows:
        if hwnd == expected or "tray" in cls.lower():
            print(f"  hwnd={hwnd} cls={cls!r} title={title!r} proc={proc!r} visible={visible}")

    windows = visible_top_windows(api)
    print(f"\nvisible_top_windows: {len(windows)} 项")
    print(f"  expected 在链里: {any(w.hwnd == expected for w in windows)}")
    print(f"  前 8: {[(w.hwnd, w.class_name, w.title) for w in windows[:8]]}")

    from rpa_core.executors.desktop import DesktopExecutor
    from rpa_core.model.desktop import DesktopLocator

    t0 = time.perf_counter()
    wrapper = DesktopExecutor._window_by_handle(expected)
    print(f"\n直接构造 wrapper: {bool(wrapper)}  用时 {time.perf_counter() - t0:.2f}s")
    if wrapper is not None:
        locator = DesktopLocator.model_validate(doc["selector"]["locator"])
        t0 = time.perf_counter()
        matches = DesktopExecutor._find(wrapper, locator)
        print(f"  直接 _find: count={len(matches)}  用时 {time.perf_counter() - t0:.2f}s")
        for match in matches[:3]:
            info = match.element_info
            print(f"    name={info.name!r} aid={info.automation_id!r} "
                  f"ct={info.control_type!r} rect={match.rectangle()}")

    from rpa_core.capture.verification import verify_element

    t0 = time.perf_counter()
    result = verify_element(doc, mode="preview")
    print(f"\nverify_element(preview): {result.to_dict()}  用时 {time.perf_counter() - t0:.2f}s")


if __name__ == "__main__":
    main()
