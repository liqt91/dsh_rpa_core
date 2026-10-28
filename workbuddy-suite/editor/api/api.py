"""WorkBuddy Studio 的 Python 侧：只管磁盘与「会话通道」，不碰任何 UI 状态。

三条边界（有意为之）：

1. **不拥有编辑状态。** 文本的权威在窗口里的 textarea；这里只在打开/保存的瞬间
   读写一次磁盘。所以本进程崩了，用户正在敲的东西不会跟着没。
2. **不做 shell。** 保存就是写这一个文件，不递归、不删除、不改权限。
3. **会话是可选依赖。** 拿不到会话（Agent 没在跑、端口没起），编辑器照常能开能存；
   UI 上只会显示「会话离线」。任何一次 RPC 都不该因为对端不在而卡住超过 1 秒。

会话协议（与 `scripts/studio.py` 的 HTTP 服务对齐，默认 127.0.0.1:7788）：
    GET  /inbox?since=<n>&client=editor   -> {ok, cursor, requests:[...]}
    POST /events  {kind, path, sha, ...}  -> {ok, cursor}
    GET  /health                          -> {ok, service, version, port}
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ppx_py import api_method

VERSION = "1.0.0"
MAX_BYTES = 8 * 1024 * 1024
HTTP_TIMEOUT = 1.0
# ★ 关掉代理：Windows 上 http_proxy 常驻，urllib 会把 127.0.0.1 也交给代理，报 502。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SESSION_DIR = Path(
    os.environ.get("STUDIO_HOME") or (Path(os.environ.get("TEMP") or "/tmp") / "workbuddy-studio")
)
DEFAULT_PORT = int(os.environ.get("STUDIO_PORT") or 7788)


# ---------------------------------------------------------------------------
# 仓库内的深度校验（可选）：只在「看起来像工作流」的 JSON 上生效。
# 找不到 rpa_core 就静默退化成语法校验，不报错 —— 这个窗口要能独立分发。
# ---------------------------------------------------------------------------


def _repo_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "src" / "rpa_core").is_dir():
            return parent
    return None


def _deep_validate(text: str) -> dict[str, Any] | None:
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict) or "root" not in document:
        return None
    root = sys.modules.get("rpa_core")
    if root is None:
        repo = _repo_root()
        if repo is None:
            return None
        source = str(repo / "src")
        if source not in sys.path:
            sys.path.insert(0, source)
    try:
        from rpa_core.model.workflow import Workflow
    except Exception:  # noqa: BLE001 - 依赖不可用就退化成语法校验
        return None
    try:
        Workflow.model_validate(document)
        return {"deep": True, "ok": True, "message": "", "line": 0, "col": 0}
    except Exception as exc:  # noqa: BLE001
        return {
            "deep": True,
            "ok": False,
            "message": f"{type(exc).__name__}: {exc}"[:600],
            "line": 0,
            "col": 0,
        }


def _validate(path: str, text: str) -> dict[str, Any]:
    suffix = Path(path).suffix.lower()
    if suffix in (".json", ".jsonc", ".json5") and text.strip():
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return {
                "kind": "json",
                "ok": False,
                "message": exc.msg,
                "line": exc.lineno,
                "col": exc.colno,
            }
        deep = _deep_validate(text)
        if deep is not None:
            return {"kind": "workflow", **deep}
        return {"kind": "json", "ok": True, "message": "", "line": 0, "col": 0}
    return {"kind": "text", "ok": True, "message": "", "line": 0, "col": 0}


# ---------------------------------------------------------------------------
# 文件读写：保留编码、BOM 与换行风格 —— 编辑器改一行不该把整个文件翻新。
# ---------------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_document(path: str) -> dict[str, Any]:
    target = Path(path)
    if not target.is_absolute():
        return {"ok": False, "code": "PATH_NOT_ABSOLUTE", "error": f"需要绝对路径：{path}"}
    if not target.exists():
        return {"ok": False, "code": "FILE_NOT_FOUND", "error": f"文件不存在：{path}"}
    if target.is_dir():
        return {"ok": False, "code": "IS_DIRECTORY", "error": f"这是一个目录：{path}"}
    raw = target.read_bytes()
    if len(raw) > MAX_BYTES:
        return {
            "ok": False,
            "code": "FILE_TOO_LARGE",
            "error": f"文件 {len(raw)} 字节，超过 {MAX_BYTES} 字节上限（轻量窗口不背大文件）",
        }
    if b"\x00" in raw[:4096]:
        return {"ok": False, "code": "BINARY_FILE", "error": "这是二进制文件，编辑器只处理文本"}

    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig" if bom else "utf-8")
    except UnicodeDecodeError:
        return {
            "ok": False,
            "code": "NOT_UTF8",
            "error": "文件不是 UTF-8 编码；请先转码，避免保存时把原有字符写坏",
        }
    eol = "CRLF" if text.count("\r\n") > text.count("\n") // 2 else "LF"
    text = text.replace("\r\n", "\n")
    return {
        "ok": True,
        "path": str(target),
        "text": text,
        "sha": _sha(raw),
        "bytes": len(raw),
        "lines": text.count("\n") + 1,
        "eol": eol,
        "bom": bom,
        "mtime": target.stat().st_mtime,
        "lang": _lang_of(target.name),
    }


def _lang_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in (".json", ".jsonc", ".json5", ".lock"):
        return "json"
    if suffix in (".py", ".pyi"):
        return "python"
    if suffix in (".js", ".mjs", ".cjs", ".ts"):
        return "javascript"
    if suffix in (".md", ".markdown"):
        return "markdown"
    if suffix in (".yml", ".yaml"):
        return "yaml"
    return "plain"


def _write_document(path: str, text: str, eol: str, bom: bool) -> bytes:
    payload = text.replace("\r\n", "\n")
    if eol == "CRLF":
        payload = payload.replace("\n", "\r\n")
    data = payload.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    Path(path).write_bytes(data)
    return data


# ---------------------------------------------------------------------------
# 会话通道（尽力而为：失败只返回 online=False，绝不抛给 UI）
# ---------------------------------------------------------------------------


def _endpoint() -> tuple[int, str | None]:
    try:
        meta = json.loads((SESSION_DIR / "endpoint.json").read_text(encoding="utf-8"))
        return int(meta.get("port") or DEFAULT_PORT), meta.get("token")
    except (OSError, ValueError, TypeError):
        return DEFAULT_PORT, None


def _http(
    method: str,
    route: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = HTTP_TIMEOUT,
) -> dict[str, Any] | None:
    port, token = _endpoint()
    url = f"http://127.0.0.1:{port}{route}"
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("Origin", "http://localhost:5273")
    if token:
        request.add_header("X-Studio-Token", token)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def _notify(kind: str, payload: dict[str, Any]) -> bool:
    body = {"kind": kind, "at": time.time(), "by": "editor", **payload}
    result = _http("POST", "/events", body)
    return bool(result and result.get("ok"))


# ---------------------------------------------------------------------------
# RPC
# ---------------------------------------------------------------------------


_TITLEBAR_DONE = False


def _titlebar_log(text: str) -> None:
    if not os.environ.get("STUDIO_VERBOSE"):
        return
    try:
        with (SESSION_DIR / "titlebar.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%H:%M:%S')} {text}\n")
    except OSError:
        pass


def _dark_titlebar() -> None:
    """把系统标题栏刷成深色（Windows 11 走 DwmSetWindowAttribute）。

    PPX 不提供 frameless 选项，默认是白色系统标题栏 —— 一个深色编辑器顶着白条很难看。
    这里属于「有则更好」的修饰：任何一步失败都静默放过，不影响编辑功能。
    """
    global _TITLEBAR_DONE
    if _TITLEBAR_DONE or sys.platform != "win32":
        return
    _TITLEBAR_DONE = True
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        for _ in range(20):
            hwnd = user32.FindWindowW(None, "WorkBuddy Studio")
            if hwnd:
                break
            time.sleep(0.25)
        if not hwnd:
            _titlebar_log("window not found")
            return
        enabled = ctypes.c_int(1)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE（Win10 2004+）；老 build 只认 19
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled)) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))
        caption = ctypes.c_int(0x001E1611)  # COLORREF: 0x00BBGGRR -> #11161E
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        text = ctypes.c_int(0x00F3EDE6)  # -> #E6EDF3
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
        _titlebar_log(f"hwnd={hwnd} dark={enabled.value}")
    except Exception:  # noqa: BLE001 - 纯装饰，失败就算了
        pass


@api_method("studio.bootstrap")
def bootstrap() -> dict[str, Any]:
    threading.Thread(target=_dark_titlebar, daemon=True).start()
    port, _token = _endpoint()
    health = _http("GET", "/health", timeout=0.7)
    inbox = _http("GET", "/inbox?since=0&client=editor", timeout=0.7) if health else None
    requests = (inbox or {}).get("requests") or []
    pending = next((item for item in requests if item.get("kind") == "open"), None)
    return {
        "ok": True,
        "version": VERSION,
        "python": sys.version.split()[0],
        "session": {
            "online": bool(health and health.get("ok")),
            "port": port,
            "cursor": (inbox or {}).get("cursor", 0),
            "service": (health or {}).get("service"),
        },
        "capabilities": {
            "deepValidation": _repo_root() is not None,
            "session": True,
        },
        "recent": _recent_files(),
        "pending": (
            {"path": pending.get("path"), "reason": pending.get("reason")} if pending else None
        ),
    }


_RECENT_FILE = SESSION_DIR / "recent.json"


def _remember(path: str) -> None:
    items = [item for item in _recent_files() if item != path]
    items.insert(0, path)
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        _RECENT_FILE.write_text(json.dumps(items[:12], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _recent_files() -> list[str]:
    try:
        data = json.loads(_RECENT_FILE.read_text(encoding="utf-8"))
        return [str(item) for item in data][:12]
    except (OSError, ValueError, TypeError):
        return []


@api_method("studio.openFile")
def open_file(path: str) -> dict[str, Any]:
    document = _read_document(path)
    if not document.get("ok"):
        return document
    document["validation"] = _validate(document["path"], document["text"])
    _remember(document["path"])
    _notify("opened", {"path": document["path"], "sha": document["sha"]})
    return document


@api_method("studio.saveFile")
def save_file(path: str, text: str, base_sha: str | None = None) -> dict[str, Any]:
    target = Path(path)
    if not target.is_absolute():
        return {"ok": False, "code": "PATH_NOT_ABSOLUTE", "error": f"需要绝对路径：{path}"}
    if not target.exists():
        return {
            "ok": False,
            "code": "FILE_NOT_FOUND",
            "error": f"文件不存在（本窗口只改已有文件）：{path}",
        }

    previous = _read_document(str(target))
    if not previous.get("ok"):
        return previous
    if base_sha and previous["sha"] != base_sha:
        return {
            "ok": False,
            "code": "CONFLICT",
            "error": "文件在打开后被其他程序改过，已拒绝覆盖",
            "sha": previous["sha"],
        }

    eol = previous.get("eol", "LF")
    encoded = _write_document(str(target), text, eol, bool(previous.get("bom", False)))
    validation = _validate(str(target), text)
    notified = _notify(
        "save",
        {
            "path": str(target),
            "sha": _sha(encoded),
            "bytes": len(encoded),
            "lines": text.count("\n") + 1,
            "validation": validation,
        },
    )
    return {
        "ok": True,
        "path": str(target),
        "sha": _sha(encoded),
        "bytes": len(encoded),
        "mtime": target.stat().st_mtime,
        "validation": validation,
        "notified": notified,
    }


@api_method("studio.pollSession")
def poll_session(since: int = 0) -> dict[str, Any]:
    port, _token = _endpoint()
    inbox = _http("GET", f"/inbox?since={int(since)}&client=editor", timeout=1.0)
    if inbox is None:
        return {"ok": True, "online": False, "port": port, "cursor": since, "requests": []}
    return {
        "ok": True,
        "online": True,
        "port": inbox.get("port", port),
        "cursor": inbox.get("cursor", since),
        "requests": inbox.get("requests") or [],
    }


@api_method("studio.heartbeat")
def heartbeat(path: str | None = None, dirty: bool = False) -> dict[str, Any]:
    return {"ok": True, "notified": _notify("heartbeat", {"path": path, "dirty": bool(dirty)})}


@api_method("studio.ack")
def ack(message_id: str) -> dict[str, Any]:
    return {"ok": True, "notified": _notify("ack", {"id": message_id})}


@api_method("studio.validateText")
def validate_text(path: str, text: str) -> dict[str, Any]:
    return {"ok": True, "validation": _validate(path, text)}
