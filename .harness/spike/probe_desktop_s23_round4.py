"""第四轮：在「工作线程 + pythoncom.CoInitialize」里复现执行器环境，测 Select 是否失效。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "e2e"))

import desktop_fixture  # noqa: E402

os.environ.setdefault("RPA_DESKTOP_E2E", "1")


async def main() -> None:
    import pywinauto.uia_defines as uia_defs
    from pywinauto import Desktop

    base = Path(tempfile.mkdtemp(prefix="rpa-s23-round4-"))
    desktop_fixture.kill_demo_apps()
    exe = desktop_fixture.compile_demo_app(base)
    process = subprocess.Popen([str(exe)])
    try:
        desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
        desktop_fixture.warmup_uia(desktop_fixture.APP_TITLE)

        def make_finder():
            win = Desktop(backend="uia").window(title=desktop_fixture.APP_TITLE)

            def find_list():
                out = []
                for child in win.descendants():
                    try:
                        if child.element_info.automation_id == "optionsList":
                            out.append(child)
                    except Exception:
                        continue
                assert len(out) == 1
                return out[0]

            return find_list

        def selected_of(lst) -> str:
            raw = lst.element_info.element
            sel = uia_defs.get_elem_interface(raw, "Selection")
            items = sel.GetCurrentSelection()
            names = [items.GetElement(i).CurrentName or "" for i in range(items.Length)]
            return ",".join(names) or "<空>"

        find_list = make_finder()
        lst = find_list()
        print(f"主线程初始选中：{selected_of(lst)}")

        result_holder: dict = {}

        def worker(co_init: bool) -> None:
            try:
                if co_init:
                    import pythoncom

                    pythoncom.CoInitialize()
                win2 = Desktop(backend="uia").window(title=desktop_fixture.APP_TITLE)
                out = []
                for child in win2.descendants():
                    try:
                        if child.element_info.automation_id == "optionsList":
                            out.append(child)
                    except Exception:
                        continue
                lst2 = out[0]
                items2 = lst2.descendants(control_type="ListItem")
                print(f"  [worker co_init={co_init}] 枚举到 {len(items2)} 项，"
                      f"即将 Select items2[1]（beta）")
                items2[1].iface_selection_item.Select()
                result_holder[co_init] = selected_of(lst2)
                print(f"  [worker co_init={co_init}] 线程内读选中：{result_holder[co_init]}")
            except Exception as exc:
                print(f"  [worker co_init={co_init}] 异常 {type(exc).__name__}: {exc}")
                result_holder[co_init] = f"<异常 {type(exc).__name__}>"
            finally:
                if co_init:
                    import pythoncom

                    pythoncom.CoUninitialize()

        t1 = threading.Thread(target=worker, args=(True,), name="sta-worker")
        t1.start()
        t1.join()
        main_after = selected_of(find_list())
        print(f"worker(co_init=True) 之后主线程读选中：{main_after} "
              f"（线程内读的是 {result_holder.get(True)}）")

        t2 = threading.Thread(target=worker, args=(False,), name="raw-worker")
        t2.start()
        t2.join()
        print(f"worker(co_init=False) 之后主线程读选中：{selected_of(find_list())} "
              f"（线程内读的是 {result_holder.get(False)}）")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        desktop_fixture.kill_demo_apps()
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
