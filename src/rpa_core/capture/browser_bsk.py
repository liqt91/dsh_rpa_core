"""BrowserSkill（bsk）浏览器元素捕获会话（M14）。

传输协议（docs/capture-browserskill.md §4）：
  devserver → `bsk` CLI 子进程 → daemon（命名管道）→ 扩展（ws://127.0.0.1:52800）
  → chrome.debugger（CDP）→ 用户真实已登录浏览器的 Agent Window

与 Playwright picker 的差异：
- evaluate 是命令式的（一次调用一个结果），pick 通过**注入 picker 后循环轮询**
  `window.__rpaCaptureResult` 读回（而非 wait_for_function 阻塞回调）
- Agent Window 常驻 `<browser-skill-overlay>` 全屏遮罩（pointer-events 拦截），
  picker 用 `elementsFromPoint` 取坐标下元素栈、沿 shadow host 链跳过遮罩节点
- 捕获手势：**Ctrl+Click**（普通点击穿透不捕获，用户可正常导航找到目标）；
  Esc 取消；bsk 合成点击用 `bsk click --modifiers ctrl`（CDP modifiers 位）
- 会话取消/结束强制 `bsk session stop`（规则 11：释放 attempt 拥有的资源）
"""

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# picker 与现有 browser picker 共享同一套 selector 构造/描述逻辑，
# 差异仅在：坐标栈代替 mouseover target、Ctrl+Click 手势、主世界状态轮询。
_PICKER_JS = r"""
(() => {
  if (window.__rpaPickerInstalled) return 'already-installed';
  window.__rpaPickerInstalled = true;
  window.__rpaCaptureResult = null;
  const skipOverlay = (stack) => stack.find((el) => {
    let n = el;
    while (n) {
      if (n.tagName && n.tagName.toLowerCase() === 'browser-skill-overlay') return false;
      n = n.parentElement;
    }
    return true;
  });
  const cssSelectorFor = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
      if (node.className && typeof node.className === 'string') {
        const cls = node.className.trim().split(/\s+/).filter(Boolean)[0];
        if (cls) part += '.' + CSS.escape(cls);
      }
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  };
  let box = null;
  const show = (el) => {
    if (!box) {
      box = document.createElement('div');
      box.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;'
        + 'border:2px solid #ff3b30;background:rgba(255,59,48,.12)';
      document.documentElement.appendChild(box);
    }
    const r = el.getBoundingClientRect();
    box.style.left = r.left + 'px'; box.style.top = r.top + 'px';
    box.style.width = r.width + 'px'; box.style.height = r.height + 'px';
  };
  const cleanup = () => {
    document.removeEventListener('mousemove', onMove, true);
    document.removeEventListener('click', onClick, true);
    document.removeEventListener('keydown', onKey, true);
    if (box) box.remove();
    window.__rpaPickerInstalled = false;
  };
  const onMove = (e) => {
    const el = skipOverlay(document.elementsFromPoint(e.clientX, e.clientY));
    if (el) show(el);
  };
  const onClick = (e) => {
    if (!e.ctrlKey) return;
    const el = skipOverlay(document.elementsFromPoint(e.clientX, e.clientY));
    if (!el) return;
    e.preventDefault(); e.stopPropagation();
    cleanup();
    const css = cssSelectorFor(el);
    const r = el.getBoundingClientRect();
    window.__rpaCaptureResult = {
      kind: 'browser',
      selector: { css: css },
      verifyCount: document.querySelectorAll(css).length,
      metadata: {
        tag: el.tagName.toLowerCase(),
        id: el.id || null,
        classes: (typeof el.className === 'string'
          ? el.className.trim().split(/\s+/).filter(Boolean) : []),
        text: (el.textContent || '').trim().slice(0, 80),
        rect: { x: Math.round(r.x), y: Math.round(r.y),
                width: Math.round(r.width), height: Math.round(r.height) },
      },
    };
  };
  const onKey = (e) => {
    if (e.key === 'Escape') { cleanup(); window.__rpaCaptureResult = { cancelled: true }; }
  };
  document.addEventListener('mousemove', onMove, true);
  document.addEventListener('click', onClick, true);
  document.addEventListener('keydown', onKey, true);
  return 'installed';
})()
"""

_POLL_JS = "JSON.stringify(window.__rpaCaptureResult)"
_CLEANUP_JS = "window.__rpaPickerCleanup && window.__rpaPickerCleanup()"

_DEFAULT_BSK = str(Path.home() / ".local" / "bin" / "bsk.exe")


def default_bsk_binary() -> str:
    return _DEFAULT_BSK


class BrowserBskCaptureSession:
    """bsk 传输捕获会话；pick/cancel 从任意线程调用（同步，子进程内阻塞）。"""

    def __init__(
        self,
        *,
        transport: str = "bsk",
        browser_instance_id: str | None = None,
        start_url: str | None = None,
        user_data_dir: str | None = None,  # bsk 复用真实浏览器 profile，忽略
        headless: bool = False,  # bsk 在用户真实浏览器，忽略
        user_agent: str | None = None,
        browser_type: str = "edge",
        page_url: str | None = None,
        bsk_binary: str | None = None,
        runner: Callable[..., dict] | None = None,
    ):
        self.transport = transport
        self._config = {
            "browser_instance_id": browser_instance_id,
            "start_url": start_url,
        }
        self._bsk_binary = bsk_binary or _DEFAULT_BSK
        self._runner = runner  # 测试注入：替代真实子进程
        self._session_id: str | None = None
        self._cancelled = False
        self._closed = False

    # -- bsk 子进程（同步，evaluate 命令式） ---------------------------------

    def _bsk(self, *args: str) -> dict:
        if self._runner is not None:
            return self._runner(*args)
        proc = subprocess.run(
            [self._bsk_binary, "--json", *args],
            capture_output=True, text=True, timeout=60, encoding="utf-8",
        )
        out = proc.stdout.strip()
        try:
            return json.loads(out) if out else {"_rc": proc.returncode}
        except json.JSONDecodeError:
            return {"_raw": out, "_stderr": proc.stderr.strip(), "_rc": proc.returncode}

    def _evaluate(self, session_id: str, expression: str) -> Any:
        result = self._bsk("evaluate", "--session", session_id, expression)
        if result.get("code") == "not_found":
            raise RuntimeError(f"bsk session not active: {session_id}")
        return result.get("value")

    def start(self) -> list[str]:
        args = ["session", "start"]
        if self._config["browser_instance_id"]:
            args += ["--browser", self._config["browser_instance_id"]]
        result = self._bsk(*args)
        if result.get("code"):
            raise RuntimeError(f"bsk session start failed: {result.get('message')}")
        session_id = result.get("session_id")
        if not session_id:
            raise RuntimeError(f"bsk session start did not return session_id: {result}")
        self._session_id = session_id
        if self._config["start_url"]:
            self._bsk("navigate", "--session", session_id, self._config["start_url"])
            self._bsk("wait-for-navigation", "--session", session_id)
        return [session_id]

    @property
    def session_id(self) -> str | None:
        return self._session_id

    # -- 捕获：注入 → 轮询 → 回读 ------------------------------------------

    def pick(self, timeout_seconds: float = 60.0, click_css: str | None = None) -> dict:
        if not self._session_id:
            raise RuntimeError("bsk capture session not started")
        sid = self._session_id
        self._evaluate(sid, _PICKER_JS)
        if click_css:
            # 自动化验收：CDP 合成 Ctrl+Click（等价真实 Ctrl+Click 手势）
            self._bsk(
                "click", "--session", sid,
                "--selector", click_css, "--modifiers", "ctrl",
            )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._cancelled or self._closed:
                self._safe_evaluate(sid, _CLEANUP_JS)
                return {"cancelled": True}
            raw = self._evaluate(sid, _POLL_JS)
            if raw and raw != "null":
                data = json.loads(raw)
                self._safe_evaluate(sid, _CLEANUP_JS)
                if data.get("cancelled"):
                    return {"cancelled": True}
                if not isinstance(data, dict):
                    continue
                return data
            time.sleep(0.4)
        self._safe_evaluate(sid, _CLEANUP_JS)
        return {"timeout": True}

    def _safe_evaluate(self, session_id: str, expression: str) -> None:
        try:
            self._evaluate(session_id, expression)
        except Exception:
            pass

    # -- 生命周期：取消/结束强制 session stop（规则 11） ---------------------

    def cancel(self) -> None:
        self._cancelled = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancelled = True
        sid = self._session_id
        self._session_id = None
        if sid:
            try:
                self._bsk("session", "stop", sid)
            except Exception:
                pass
