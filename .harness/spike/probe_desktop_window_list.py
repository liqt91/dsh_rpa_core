"""探针（M38 S2）：`getWindowList` 到底返回什么——两后端实测形状 + 「静默空列表」会不会发生。

## 为什么需要它

`desktop(w.win32).getWindowList` 的正路径在用例表里**只断言形状**（`outputKeys` + `effect`），
理由是「整桌面枚举的结果随本机环境漂移，按值断言等于把维护者的桌面写进期望」。这句话对
一半：**能按值断言的锚点是靶子窗口自己**（标题恒定 `RPA Core Desktop Demo`），只要断言
「列表里**至少有这一项**」就不会漂移——不需要断言整个列表。

而这条区别很重要，因为 pywinauto 在这条路径上有一个**静默吞错**：

    uia_element_info.py:
        def _get_elements(self, ...):
            try:
                ptrs_array = self._element.FindAll(tree_scope, cond)
                ...
            except (COMError, ValueError):
                ActionLogger().log("COM error: can't get elements")
                return []

即：全桌面 UIA 枚举被 COM 拒绝时（实测的 `0x8001010d` = `RPC_E_CANTCALLOUT_ININPUTSYNCCALL`，
pytest 的 faulthandler 会把它渲染成 `Windows fatal exception`，见任务单 §1.6）**返回空列表**，
而不是报错。于是「枚举失败」与「本机真没有匹配窗口」在 outputs 上**完全同形**（都是
`windows: []`），只断言形状的用例两条都收。本探针量两件事：

1. 两后端在没有 COM 干扰时返回的真实形状（用来给「列表含靶子窗口」这条新断言定锚点）；
2. 反复枚举会不会真的撞上静默空列表（若会，说明这条缺口有可复现面）。

用法：RPA_DESKTOP_E2E=1 uv run python .harness/spike/probe_desktop_window_list.py
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "e2e"))

import desktop_fixture  # noqa: E402

from rpa_core.executors import DesktopExecutor, Win32DesktopExecutor  # noqa: E402
from rpa_core.model.command import CommandInvocation  # noqa: E402

# 枚举重复次数：够看出「静默空列表」是否偶发，又不至于把探针拖长。
REPEAT = 8

LINES: list[str] = []


def log(text: str) -> None:
    LINES.append(text)


def invocation(command: str, inputs: dict) -> CommandInvocation:
    return CommandInvocation(
        command_id=command, command_version="1.0.0", run_id="probe", step_id="probe",
        inputs=inputs,
    )


async def probe_backend(executor, prefix: str, title: str) -> None:
    attached = await executor.execute(
        invocation(f"{prefix}.attachWindow", {"title": title, "timeoutMs": 5000}),
        asyncio.Event(),
    )
    log(f"[{prefix}] attachWindow → {attached.status}")
    session_id = str(attached.outputs.get("sessionId") or "")

    for index in range(REPEAT):
        result = await executor.execute(
            invocation(f"{prefix}.getWindowList", {"sessionId": session_id}),
            asyncio.Event(),
        )
        if result.status != "success":
            log(f"[{prefix}] #{index} 无过滤 → 失败 {result.error.code}: {result.error.message}")
            continue
        windows = result.outputs.get("windows") or []
        match = [w for w in windows if w.get("title") == title]
        log(
            f"[{prefix}] #{index} 无过滤 → {len(windows)} 项；"
            f"靶子窗口命中 {len(match)} 项{('：' + str(match[0])) if match else ''}"
            f"{'  ← 静默空列表！' if not windows else ''}"
        )

    exact = await executor.execute(
        invocation(
            f"{prefix}.getWindowList",
            {"sessionId": session_id, "titlePattern": title, "matchMode": "exact"},
        ),
        asyncio.Event(),
    )
    windows = exact.outputs.get("windows") or []
    log(f"[{prefix}] exact 过滤 → {len(windows)} 项：{windows if len(windows) < 4 else windows[:3]}")

    await executor.close()


async def main() -> int:
    if desktop_fixture.fixture_unavailable_reason() is not None:
        log(f"SKIP：{desktop_fixture.fixture_unavailable_reason()}")
        return 0
    desktop_fixture.kill_demo_apps()
    with tempfile.TemporaryDirectory(prefix="rpa-probe-windowlist-") as tmp:
        exe = desktop_fixture.compile_demo_app(Path(tmp))
        process = subprocess.Popen([str(exe)])
        try:
            desktop_fixture.wait_for_window(desktop_fixture.APP_TITLE)
            desktop_fixture.warmup_uia(desktop_fixture.APP_TITLE)
            desktop_fixture.force_foreground(desktop_fixture.APP_TITLE)
            await probe_backend(DesktopExecutor(), "desktop", desktop_fixture.APP_TITLE)
            await probe_backend(
                Win32DesktopExecutor(), "desktop.win32", desktop_fixture.APP_TITLE
            )
        finally:
            process.terminate()
            process.wait(timeout=10)
            desktop_fixture.kill_demo_apps()
    print("\n".join(LINES))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
