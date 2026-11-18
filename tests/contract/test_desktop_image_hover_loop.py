"""M85：图像模式的 hover 循环必须能连续跑多帧。

2026-11-17 报障：「按图捕获，刚打开没有错误提示，鼠标一移动就提示桌面捕获进程起不来」。

根因在 ``_hover_capture``：图像分支把模板框（四元组 ``(left, top, right, bottom)``）
写进了 ``last_rect``——那个槽子是留给 **UIA RECT 对象**的，下一帧的
``_rect_contains`` 按 ``.left/.right`` 读就抛 ``AttributeError``；hover 主循环没有
兜底，异常直接打死整个 agent，父进程只看到「exited with code 1」，浮窗于是说
「捕获进程起不来」，用户去查环境/重装插件。

判据刻意做成**多帧行为**而不是「函数能返回」：这类串槽 bug 单帧永远测不出来
（第一帧赋值、第二帧才炸），这也是它躲过了 M84 几何验证的原因——那轮验证的是
``_image_box_at`` 的算术，没跑用户真正走的那个循环（探针也是直接调几何函数）。
"""

from __future__ import annotations

from rpa_core.capture import desktop_agent


class _FakeOverlay:
    """悬浮框替身：只记画框请求（真实现要建 win32 顶层窗口）。"""

    hwnd = 1

    def __init__(self) -> None:
        self.rects: list[tuple[int, int, int, int]] = []
        self.hides = 0

    def show_rect(self, left: int, top: int, right: int, bottom: int) -> None:
        self.rects.append((left, top, right, bottom))

    def hide(self) -> None:
        self.hides += 1

    def pump(self) -> None:
        pass

    def destroy(self) -> None:
        pass


def _stub_environment(monkeypatch, overlay: _FakeOverlay) -> None:
    """把 hover 循环的外部依赖全换成确定值：鼠标在走、热键没按、窗口恒为 1234。"""
    state = {"n": 0}

    def _cursor_pos() -> tuple[int, int]:
        # 每帧 +8px：越过 ``moved`` 的 3px 阈值，红框才会逐帧重画
        state["n"] += 1
        return (100 + state["n"] * 8, 200 + state["n"] * 8)

    monkeypatch.setattr(desktop_agent, "_HoverOverlay", lambda: overlay)
    monkeypatch.setattr(desktop_agent, "_cursor_pos", _cursor_pos)
    monkeypatch.setattr(desktop_agent, "_hotkey_pressed", lambda vk: False)  # noqa: ARG005
    monkeypatch.setattr(desktop_agent, "_poll_click_hook_event", lambda: False)
    monkeypatch.setattr(desktop_agent, "_start_click_hook", lambda *a, **k: None)
    monkeypatch.setattr(desktop_agent, "_stop_click_hook", lambda: None)
    monkeypatch.setattr(desktop_agent, "_win32_root_at", lambda x, y: 1234)  # noqa: ARG005
    monkeypatch.setattr(
        desktop_agent,
        "_image_box_at",
        lambda root, x, y, **kw: (x - 16, y - 16, x + 16, y + 16) if root else None,
    )


def test_image_hover_survives_consecutive_frames(monkeypatch) -> None:
    overlay = _FakeOverlay()
    _stub_environment(monkeypatch, overlay)

    result = desktop_agent._hover_capture(
        desktop_agent.VK_F9, 0.3, hybrid=True, prefer_image=True
    )

    assert result == {"timeout": True}, "循环跑满超时才算健康退出（不是异常退出）"
    # ★ 至少两帧：单帧只能证明「赋值没炸」，第二帧才走到读 last_rect 的那一行
    assert len(overlay.rects) >= 3, f"红框只画了 {len(overlay.rects)} 次，循环没跑起来"
    # 每帧坐标不同 ⇒ 每次都必须重画（顺带证明 last_box 去抖没有把循环卡死）
    assert len(set(overlay.rects)) == len(overlay.rects)
    assert overlay.rects[-1][2] - overlay.rects[-1][0] == 32, "框的边长必须等于模板边长"
