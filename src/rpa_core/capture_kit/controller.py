"""捕获编排器：把「会话 + 浮窗 + 结果」收成一个可复用的状态机。

给宿主的最小接口只有三件事：

- ``start()``：建混合会话、起双臂、显示浮窗、后台 ``pick``；返回首屏状态；
- ``poll()``：返回 ``pending / ready / cancelled / offline / failed``；
- ``cancel()``：取消本次捕获（浮窗按钮 / 宿主取消 / 二次点击共用）。

宿主差异被收在两个注入口：

- ``on_captured``：有效描述符产出后先交给宿主落盘/绑定，再把结果回显；
- 最小化/还原由**宿主自己**做（Qt 用 ``showMinimized``/``showNormal``，ppx 用
  前端 ``window.minimize``/``window.restore``）——WebView 宿主的最小化是 JS 能力，
  Python 侧拿不到窗口句柄，硬塞一个 host 回调反而多一层不可测的间接。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .engine import (
    NEVER_TIMEOUT,
    build_hybrid_session,
    desktop_status,
    ensure_extension_host,
    gesture_hint,
    status_snapshot,
    web_status,
)
from .float_window import CaptureFloat

_READY_KINDS = ("desktop", "browser")


class CaptureController:
    """一次捕获的生命周期（单次使用；重复 start 请新建实例）。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] | None = None,
        on_captured: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        host: Any = None,
        float_factory: Callable[[], Any] | None = None,
        heal_host: Callable[[], dict[str, Any]] | None = None,
        edit_dialog_factory: Callable[[], Any] | None = None,
        edit_model: Callable[[dict[str, Any]], dict[str, str]] | None = None,
        on_commit: Callable[[str, str], str | None] | None = None,
        on_discard: Callable[[], None] | None = None,
    ) -> None:
        self._factory = session_factory or build_hybrid_session
        self._on_captured = on_captured
        self._host = host  # 需提供 minimize() / restore()；None 则不收主窗
        self._float_factory = float_factory or CaptureFloat
        # 扩展离线时重注册 Native Messaging 白名单（路径漂移会让扩展 ID 变）
        self._heal_host = heal_host or ensure_extension_host
        # 编辑框三件套（M68 后半场）：给了工厂，抓完由控制器**自己弹** win32 编辑框，
        # 状态机多三个值：editing / committed / discarded；不给工厂（非 Windows 或
        # 宿主想自己画）则维持旧的 ready 语义，由宿主页面接管确认。
        self._edit_dialog_factory = edit_dialog_factory
        self._edit_model = edit_model
        self._on_commit = on_commit
        self._on_discard = on_discard
        self._edit_dialog: Any = None
        self._editing = False
        self._session: Any = None
        self._float: CaptureFloat | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._result: dict[str, Any] | None = None
        self._cancelled = False
        self._desktop_offline = False
        self._extension_note: dict[str, Any] = {}

    # -- 生命周期 ------------------------------------------------------------
    def start(self) -> dict[str, Any]:
        """建会话并开始捕获。返回首屏状态；双通道都不可用时返回 ``ok=False``。"""
        session = self._factory()
        session.start()
        self._session = session
        self._desktop_offline = bool(getattr(session, "desktop_offline", False))
        ext_offline = bool(getattr(session, "extension_offline", False))
        if ext_offline:
            # 扩展离线最常见的原因不是「没装」，而是 host 白名单里是旧路径派生的 ID
            # （unpacked 扩展 ID 随目录变）——浏览器因此拒绝拉起 host。这里幂等重注册：
            # 补上后扩展会在 service worker 重启时自动重连，本会话的「离线复活」守望
            # 会把它收编回竞速。
            healed = self._run_heal()
            self._extension_note = {
                "extensionInstalled": bool(healed.get("installed")),
                "extensionHostRefreshed": bool(healed.get("changed")),
                "extensionHostBrowsers": list(healed.get("refreshed") or []),
            }
        else:
            self._extension_note = {
                "extensionInstalled": True,
                "extensionHostRefreshed": False,
                "extensionHostBrowsers": [],
            }
        snapshot = status_snapshot(session)
        if self._desktop_offline and ext_offline:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
            return {
                "ok": False,
                "code": "NO_CAPTURE_CHANNEL",
                "extensionOffline": True,
                "desktopOffline": True,
                **self._extension_note,
                **snapshot,
            }
        window = self._float_factory()
        window.show(
            snapshot["gesture"],
            snapshot["web"],
            snapshot["desktop"],
            snapshot["webOk"],
            snapshot["desktopOk"],
            on_cancel=self.cancel,
        )
        self._float = window
        self._minimize()
        try:
            self._thread = threading.Thread(
                target=self._work, name="capture-pick", daemon=True
            )
            self._thread.start()
        except Exception:
            self._restore()
            raise
        return {
            "ok": True,
            "offline": self._desktop_offline,
            "extensionOffline": ext_offline,
            **self._extension_note,
            **snapshot,
        }

    @property
    def instructions(self) -> str:
        """当前会话的手势提示（供宿主面板复用）。"""
        if self._session is None:
            return ""
        return gesture_hint(self._session)

    def poll(self) -> dict[str, Any]:
        """状态机：pending / editing / ready / committed / discarded / cancelled /
        offline / failed。"""
        with self._lock:
            if self._cancelled:
                return {"state": "cancelled"}
            result = self._result
            editing = self._editing
        if editing:
            # 编辑框正在桌面上：宿主页面继续轮询，别弹自己的确认框
            return {"state": "editing"}
        if result is None:
            self._refresh_float()
            session = self._session
            armed = bool(getattr(session, "armed", False)) if session is not None else False
            return {"state": "pending", "offline": self._desktop_offline, "armed": armed}
        state = result.get("state")
        if state == "committed":
            return {"state": "committed", "name": result.get("name")}
        if state == "discarded":
            return {"state": "discarded"}
        if result.get("kind") in _READY_KINDS:
            return {"state": "ready", "result": result}
        if result.get("unavailable") or result.get("offline"):
            return {
                "state": "offline",
                "reason": result.get("error") or "没有可用的捕获通道",
            }
        return {
            "state": "failed",
            "reason": result.get("error") or result.get("stderrTail") or str(result),
        }

    def cancel(self) -> None:
        """取消本次捕获，并收掉浮窗与编辑框（可重入）。"""
        with self._lock:
            self._cancelled = True
        session = self._session
        try:
            if session is not None:
                session.cancel()
                session.close()
        except Exception:  # noqa: BLE001 - 取消失败也要退出等待态
            pass
        dialog = self._edit_dialog
        if dialog is not None:
            try:
                dialog.close()
            except Exception:  # noqa: BLE001 - 框已销毁也无妨
                pass
        self._hide_float()
        self._restore()

    # -- 内部 ----------------------------------------------------------------
    def _run_heal(self) -> dict[str, Any]:
        """跑一次 host 白名单自愈；失败只影响提示文案，绝不能挡住捕获本身。"""
        try:
            healed = self._heal_host()
        except Exception:  # noqa: BLE001
            return {}
        return healed if isinstance(healed, dict) else {}

    def _work(self) -> None:
        session = self._session
        try:
            try:
                result = session.pick(timeout_seconds=NEVER_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - 任何异常都落进结果，别让轮询永挂
                result = {"error": f"捕获线程异常：{exc}"}
            if isinstance(result, dict) and result.get("kind") in _READY_KINDS:
                if self._on_captured is not None:
                    try:
                        result = self._on_captured(result)
                    except Exception as exc:  # noqa: BLE001
                        result = {"error": f"元素落盘失败：{exc}"}
        finally:
            self._hide_float()
            self._restore()
        # ★ _result 与 _editing 必须在同一把锁里落定：分开赋值的话，宿主轮询可能
        # 恰好插进「ready 已可见、editing 还没置位」的缝隙，web 弹窗与 win32 框一起弹。
        open_edit = False
        with self._lock:
            if isinstance(result, dict) and result.get("cancelled"):
                self._cancelled = True
            else:
                self._result = result
                open_edit = (
                    isinstance(result, dict)
                    and result.get("kind") in _READY_KINDS
                    and self._edit_dialog_factory is not None
                )
                if open_edit:
                    self._editing = True
        # 编辑态：有对话框工厂时，抓完由控制器直接弹 win32 编辑框——
        # 「捕获 → 确认入库」不再依赖宿主页面活着（M68 后半场）。
        if open_edit:
            self._open_edit_dialog(result)

    # -- 编辑框（win32；工厂缺省时整条路径不触发） ------------------------------
    def _open_edit_dialog(self, doc: dict[str, Any]) -> None:
        dialog = self._edit_dialog_factory()
        if dialog is None or not getattr(dialog, "available", True):
            # 对话框不可用（非 Windows 工厂给 None）：退回旧的 ready 语义，
            # 宿主页面接管确认。
            with self._lock:
                self._editing = False
            return
        model = self._edit_model(doc) if self._edit_model is not None else {}
        self._edit_dialog = dialog
        dialog.show(
            title=model.get("title") or "捕获到元素",
            name=model.get("name") or "",
            selector=model.get("selector") or "",
            meta=model.get("meta") or "",
            on_save=self._commit_from_dialog,
            on_close=self._close_edit,
        )

    def _commit_from_dialog(self, name: str, selector: str) -> str | None:
        """编辑框「保存」的回调：宿主落盘，返回 None=成功关框 / 文案=错误留框。"""
        error: str | None = None
        if self._on_commit is not None:
            try:
                error = self._on_commit(name, selector)
            except Exception as exc:  # noqa: BLE001 - 入库异常也要回显在框里
                error = f"入库失败：{exc}"
        if error:
            return str(error)
        with self._lock:
            self._editing = False
            self._result = {"state": "committed", "name": name}
        return None

    def _close_edit(self, saved: bool) -> None:
        """编辑框关闭（取消路径）：丢掉暂存，状态机落 discarded。"""
        if saved:
            return  # committed 已在 _commit_from_dialog 落定
        if self._on_discard is not None:
            try:
                self._on_discard()
            except Exception:  # noqa: BLE001 - 丢弃失败不该卡住收场
                pass
        with self._lock:
            self._editing = False
            self._result = {"state": "discarded"}

    def _refresh_float(self) -> None:
        """等待期每进一次 poll 就刷新双腿状态（扩展 arm/判死是异步的）。"""
        session = self._session
        window = self._float
        if session is None or window is None:
            return
        web, web_ok = web_status(session)
        desktop, desktop_ok = desktop_status(session)
        window.set_status(web, desktop, web_ok, desktop_ok)

    def _hide_float(self) -> None:
        window = self._float
        if window is not None:
            window.hide()

    def _minimize(self) -> None:
        host = self._host
        if host is None:
            return
        try:
            host.minimize()
        except Exception:  # noqa: BLE001 - 收主窗失败不影响捕获本身
            pass

    def _restore(self) -> None:
        host = self._host
        if host is None:
            return
        try:
            host.restore()
        except Exception:  # noqa: BLE001 - 还原失败也别把异常抛进轮询
            pass

    # 便于宿主观察（调试/状态条）
    @property
    def offline(self) -> bool:
        return self._desktop_offline

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled
