"""宿主窗口最大化状态的契约（M104）。

pywebview(WinForms) 的 ``restore()`` 把窗口设成 Normal：最小化前若是最大化，
还原后就缩了。这里只测可移植的适配层，原生探针由 ppx 宿主注入。
"""

from __future__ import annotations

from rpa_core.capture_kit.window_host import MaximizedAwareHost


class _FakeHost:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def minimize(self) -> None:
        self.calls.append("minimize")

    def restore(self) -> None:
        self.calls.append("restore")

    def maximize(self) -> None:
        self.calls.append("maximize")


def test_maximized_window_is_restored_as_maximized() -> None:
    order: list[str] = []
    host = _FakeHost()

    def probe() -> bool:
        order.append("probe")
        return True

    aware = MaximizedAwareHost(host, probe)
    aware.minimize()
    aware.restore()
    # 必须先探测再最小化：最小化之后 IsZoomed 必然是 False
    assert order == ["probe"]
    assert host.calls == ["minimize", "maximize"]


def test_normal_window_keeps_plain_restore() -> None:
    host = _FakeHost()
    aware = MaximizedAwareHost(host, lambda: False)
    aware.minimize()
    aware.restore()
    assert host.calls == ["minimize", "restore"]


def test_maximized_state_does_not_leak_into_next_cycle() -> None:
    flag = [True]
    host = _FakeHost()
    aware = MaximizedAwareHost(host, lambda: flag[0])
    aware.minimize()
    flag[0] = False
    aware.restore()
    aware.minimize()
    aware.restore()
    assert host.calls == ["minimize", "maximize", "minimize", "restore"]
