import json
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rpa_core.catalog import CommandCatalog
from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler
from rpa_core.extension_exec import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ExtensionExecHub,
)
from rpa_core.extension_installer import (
    ExtensionInstallError,
    clear_uninstall_block,
    default_build_dir,
    extension_root,
    extension_status,
    install_external_guided,
    load_packed_extension,
    open_browser,
    open_path_in_explorer,
    pack_extension,
    update_manifest_xml,
)
from rpa_core.model.capture import (
    ElementDescriptor,
    ElementDocumentError,
    selector_errors,
    validate_element_document,
)
from rpa_core.model.workflow import Workflow

from .runs import RunManager
from .store import (
    WorkflowNameError,
    WorkflowNotFoundError,
    WorkflowStore,
    WorkflowStoreError,
)

DEFAULT_CAPABILITIES = frozenset(
    {
        "browser.read",
        "browser.control",
        "desktop.control",
        "process.start",
        "workspace.write",
    }
)

_CAPTURE_ACTIONS = frozenset({"start", "pick", "cancel"})
_BROWSER_TRANSPORTS = frozenset({"persistent", "user-browser", "bsk", "extension"})


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class DevServerApp:
    def __init__(
        self,
        catalog: CommandCatalog,
        store: WorkflowStore,
        capabilities: set[str] | None = None,
        browser_capture_factory=None,
        desktop_capture_factory=None,
        extension_build_dir: Path | None = None,
    ):
        self._catalog = catalog
        self._store = store
        self._capabilities = frozenset(capabilities) if capabilities else DEFAULT_CAPABILITIES
        self._browser_capture_factory = browser_capture_factory
        self._desktop_capture_factory = desktop_capture_factory
        self._extension_build_dir = extension_build_dir or default_build_dir()
        self._browser_sessions: dict[str, Any] = {}
        self._desktop_sessions: dict[str, Any] = {}
        self._capture_lock = threading.RLock()
        self._capture_seq = 0
        self._runs = RunManager(store.root)
        # 自研扩展执行通道（M15）：命令队列 + 长轮询下发（默认整个浏览器权限）
        self._extension_hub = ExtensionExecHub()

    def close(self) -> None:
        self._runs.close()
        with self._capture_lock:
            sessions = list(self._browser_sessions.values()) + list(
                self._desktop_sessions.values()
            )
            self._browser_sessions.clear()
            self._desktop_sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass

    def _next_capture_id(self, prefix: str) -> str:
        with self._capture_lock:
            self._capture_seq += 1
            return f"{prefix}-{self._capture_seq}"

    def _capture_session_id(self, registry: dict, body: dict) -> str:
        session_id = str(body.get("sessionId") or "")
        if not session_id or session_id not in registry:
            raise ApiError(404, "NOT_FOUND", f"capture session not found: {session_id}")
        return session_id

    def _require_capture(self, factory, label: str):
        if factory is None:
            raise ApiError(501, "NOT_IMPLEMENTED", f"{label} backend is not configured")
        return factory

    def _element_store(self, flow: str) -> WorkflowStore:
        """流程目录下的元素资产根：<workflows>/<flow>/elements（不自动建目录）。"""
        folder = self._store.directory(flow)
        return WorkflowStore(folder / "elements", create=False)

    def _flow_from_body(self, body: dict, *, require: bool) -> str | None:
        flow = str(body.get("flow") or "")
        if require and not flow:
            raise ApiError(400, "BAD_REQUEST", "missing 'flow' (workflow name)")
        return flow or None

    def catalog_overview(self) -> dict:
        commands = []
        for command_id in sorted(self._catalog):
            manifest = self._catalog[command_id]
            entry = {
                "id": manifest.id,
                "version": manifest.version,
                "kind": manifest.kind.value,
                "executor": manifest.executor,
                "risk": manifest.risk.value,
                "capabilities": manifest.capabilities,
                "resources": manifest.resources,
                "stability": manifest.stability.value,
                "effect": manifest.effect.model_dump(mode="json"),
                "input_schema": manifest.input_schema,
                "output_schema": manifest.output_schema,
                "errors": [error.value for error in manifest.errors],
                "retryable": manifest.retryable,
                "default_timeout_seconds": manifest.default_timeout_seconds,
            }
            if manifest.x_outputs:
                entry["x-outputs"] = manifest.x_outputs
            if manifest.x_var_write:
                entry["x-var-write"] = manifest.x_var_write
            commands.append(entry)
        return {"digest": self._catalog.digest, "commands": commands}

    def latest_run_events(self) -> dict:
        """读 run_artifacts 下最近一次运行的 events.jsonl（只读，设计期用）。"""
        artifacts = self._store.root.parent / "run_artifacts"
        if not artifacts.is_dir():
            return {"runId": None, "events": []}
        runs = sorted(
            (d for d in artifacts.iterdir() if (d / "events.jsonl").is_file()),
            key=lambda d: (d / "events.jsonl").stat().st_mtime,
            reverse=True,
        )
        if not runs:
            return {"runId": None, "events": []}
        run_dir = runs[0]
        events = []
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return {"runId": run_dir.name, "events": events}

    # -- 运行控制（ADR 0011：子进程 run host，devserver 只做代理） -----------

    def run_start(self, body: Any) -> dict:
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "request body must be a JSON object")
        workflow = body.get("workflow")
        if not isinstance(workflow, str) or not workflow:
            raise ApiError(400, "BAD_REQUEST", "missing 'workflow'")
        inputs = body.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            raise ApiError(400, "BAD_REQUEST", "'inputs' must be an object")
        try:
            return self._runs.start(workflow, inputs)
        except FileNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc

    def run_status(self, run_id: str) -> dict:
        try:
            return self._runs.status(run_id)
        except KeyError:
            raise ApiError(404, "NOT_FOUND", f"run not found: {run_id}") from None

    def run_events(self, run_id: str) -> dict:
        try:
            return self._runs.events(run_id)
        except KeyError:
            raise ApiError(404, "NOT_FOUND", f"run not found: {run_id}") from None

    def run_cancel(self, run_id: str) -> dict:
        try:
            return self._runs.cancel(run_id)
        except KeyError:
            raise ApiError(404, "NOT_FOUND", f"run not found: {run_id}") from None

    def compile(self, body: Any) -> dict:
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "request body must be a JSON object")
        document = body.get("workflow")
        if not isinstance(document, dict):
            raise ApiError(400, "BAD_REQUEST", "request body must contain a 'workflow' object")
        capabilities = body.get("capabilities", sorted(self._capabilities))
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) for item in capabilities
        ):
            raise ApiError(400, "BAD_REQUEST", "'capabilities' must be a list of strings")
        try:
            workflow = Workflow.model_validate(document)
        except ValidationError as exc:
            return self._compile_result(False, _validation_errors(exc))
        try:
            WorkflowCompiler(self._catalog).compile(workflow, set(capabilities))
        except WorkflowCompileError as exc:
            return self._compile_result(False, [{"message": str(exc)}])
        return self._compile_result(True, [])

    def list_workflows(self) -> dict:
        return {"workflows": self._store.list()}

    def get_workflow(self, name: str) -> dict:
        try:
            return self._store.read(name)
        except WorkflowNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        except WorkflowStoreError as exc:
            raise ApiError(400, "BAD_REQUEST", str(exc)) from exc

    def put_workflow(self, name: str, body: Any) -> dict:
        try:
            size = self._store.write(name, body)
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        except WorkflowStoreError as exc:
            raise ApiError(400, "BAD_REQUEST", str(exc)) from exc
        return {"name": name, "bytes": size}

    def capture_desktop(self, action: str, body: Any) -> dict:
        if action not in _CAPTURE_ACTIONS:
            raise ApiError(404, "NOT_FOUND", f"unknown capture action: {action}")
        body = body if isinstance(body, dict) else {}
        factory = self._require_capture(self._desktop_capture_factory, "desktop capture")
        if action == "start":
            kwargs: dict[str, Any] = {}
            if body.get("hotkey"):
                kwargs["hotkey"] = str(body["hotkey"])
            if body.get("timeoutSeconds"):
                kwargs["timeout_seconds"] = float(body["timeoutSeconds"])
            if isinstance(body.get("point"), dict):
                kwargs["point"] = body["point"]
            if body.get("windowHandle"):
                kwargs["window_handle"] = int(body["windowHandle"])
            if body.get("hover"):
                kwargs["hover"] = True
                # 混合捕获：已配对扩展时自动双通道（网页走扩展、桌面走 UIA），
                # 可用 body hybrid:false 显式关闭
                if body.get("hybrid", True) and self._read_token() is not None:
                    kwargs["hybrid"] = True
            with self._capture_lock:
                session_id = self._next_capture_id("desktop")
                self._desktop_sessions[session_id] = factory(**kwargs)
            return {
                "sessionId": session_id,
                "mode": "hybrid" if kwargs.get("hybrid")
                else "hover" if kwargs.get("hover")
                else "point" if kwargs.get("point") else "hotkey",
            }
        if action == "pick":
            session_id = self._capture_session_id(self._desktop_sessions, body)
            session = self._desktop_sessions[session_id]
            timeout = float(body.get("timeoutSeconds", 90))
            result = session.pick(timeout_seconds=timeout)
            if result.get("kind") in ("desktop", "browser") and body.get("saveAs"):
                flow = self._flow_from_body(body, require=True)
                result.update(self._save_element(flow, result, str(body["saveAs"])))
            return result
        session_id = self._capture_session_id(self._desktop_sessions, body)
        self._desktop_sessions.pop(session_id, None).cancel()
        return {"cancelled": True, "sessionId": session_id}

    def capture_browser(self, action: str, body: Any) -> dict:
        if action not in _CAPTURE_ACTIONS:
            raise ApiError(404, "NOT_FOUND", f"unknown capture action: {action}")
        body = body if isinstance(body, dict) else {}
        transport = body.get("transport") if action == "start" else "persistent"
        if action == "start" and transport not in _BROWSER_TRANSPORTS:
            raise ApiError(
                400,
                "BAD_REQUEST",
                "'transport' must be one of: persistent, user-browser, bsk",
            )
        factory = self._require_capture(self._browser_capture_factory, "browser capture")
        if action == "start":
            kwargs: dict[str, Any] = {"transport": transport}
            if transport == "persistent":
                if body.get("userDataDir"):
                    kwargs["user_data_dir"] = str(body["userDataDir"])
                kwargs["headless"] = bool(body.get("headless", False))
            elif transport == "bsk":
                if body.get("browserInstanceId"):
                    kwargs["browser_instance_id"] = str(body["browserInstanceId"])
                if body.get("pageUrl"):
                    kwargs["page_url"] = str(body["pageUrl"])
            elif transport == "extension":
                pass  # content-script 扩展：无启动参数，picker 已在所有页面待命
            else:
                kwargs["browser_type"] = str(body.get("browserType", "edge"))
                if body.get("userDataDir"):
                    kwargs["user_data_dir"] = str(body["userDataDir"])
                if body.get("pageUrl"):
                    kwargs["page_url"] = str(body["pageUrl"])
            if body.get("startUrl"):
                kwargs["start_url"] = str(body["startUrl"])
            with self._capture_lock:
                session_id = self._next_capture_id("browser")
                session = self._browser_sessions[session_id] = factory(**kwargs)
            try:
                pages = session.start()
            except Exception as exc:
                self._browser_sessions.pop(session_id, None)
                session.close()
                raise ApiError(502, "CAPTURE_START_FAILED", str(exc)) from exc
            return {"sessionId": session_id, "pages": pages}
        if action == "pick":
            session_id = self._capture_session_id(self._browser_sessions, body)
            session = self._browser_sessions[session_id]
            timeout = float(body.get("timeoutSeconds", 60))
            result = session.pick(
                timeout_seconds=timeout,
                click_css=body.get("clickCss"),
            )
            if result.get("kind") == "browser" and body.get("saveAs"):
                flow = self._flow_from_body(body, require=True)
                result.update(self._save_element(flow, result, str(body["saveAs"])))
            return result
        session_id = self._capture_session_id(self._browser_sessions, body)
        session = self._browser_sessions.pop(session_id)
        session.cancel()
        session.close()
        return {"cancelled": True, "sessionId": session_id}

    # -- content-script 扩展捕获（M14 无缝路线）：token 配对 + pending/result --

    @property
    def _token_path(self):
        return self._store.root / ".capture-extension-token"

    def _read_token(self) -> str | None:
        try:
            return self._token_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def extension_token(self, body: Any) -> dict:
        """配对 token：PUT/POST body {"token": "..."} 显式写入；GET 返回当前状态与值。"""
        if isinstance(body, dict) and body.get("token"):
            self._token_path.write_text(str(body["token"]), encoding="utf-8")
            return {"configured": True, "token": str(body["token"])}
        return {"configured": self._read_token() is not None,
                "token": self._read_token()}

    def _require_extension_token(self, headers) -> None:
        expected = self._read_token()
        provided = headers.get("X-Capture-Token", "")
        if not provided:
            raise ApiError(403, "FORBIDDEN", "missing capture extension token")
        if expected is None:
            # TOFU（trust on first use）：loopback 本地工具，首次接触自动采纳并持久化，
            # 免手动配对；此后只认这个 token，轮换需删 .capture-extension-token
            self._token_path.write_text(provided, encoding="utf-8")
            return
        if provided != expected:
            raise ApiError(403, "FORBIDDEN", "invalid capture extension token")

    def _pending_extension_session(self) -> str | None:
        for registry in (self._browser_sessions, self._desktop_sessions):
            for session_id, session in registry.items():
                if getattr(session, "is_extension_capture", False) and session.pending:
                    return session_id
        return None

    def extension_pending(self, headers) -> dict:
        """扩展 background 轮询：是否有激活的扩展捕获会话。"""
        self._require_extension_token(headers)
        session_id = self._pending_extension_session()
        return {"pending": session_id is not None, "sessionId": session_id}

    def extension_result(self, headers, body: Any) -> dict:
        """扩展 content script 捕获结果回传。"""
        self._require_extension_token(headers)
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "result body must be a JSON object")
        session_id = str(body.get("sessionId") or self._pending_extension_session() or "")
        session = self._browser_sessions.get(session_id) or self._desktop_sessions.get(
            session_id
        )
        if session is None or not getattr(session, "is_extension_capture", False):
            raise ApiError(404, "NOT_FOUND", f"no pending extension session: {session_id}")
        session.submit(body.get("descriptor", body))
        return {"received": True, "sessionId": session_id}

    # -- 自研扩展执行通道（M15）：命令队列 + 长轮询 + 权限（默认整个浏览器）--

    def set_extension_hub_url(self, base_url: str) -> None:
        """run 子进程经该地址回连命令队列（扩展执行通道的宿主端点）。"""
        self._runs.hub_url = base_url.rstrip("/")

    def extension_hub_status(self) -> dict:
        return self._extension_hub.status()

    def extension_hub_permissions(self, body: Any) -> dict:
        """权限查询/收窄：默认 `{"mode": "browser"}`（整个浏览器）；预留 tabs/origins。"""
        if not body:
            return self._extension_hub.permissions
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "permissions body must be a JSON object")
        try:
            return self._extension_hub.set_permissions(body)
        except ValueError as exc:
            raise ApiError(400, "BAD_REQUEST", str(exc)) from exc

    def extension_command_next(
        self, headers, wait_seconds: float, host_report: dict | None = None
    ) -> dict:
        """扩展 background 长轮询领命令（同时作为在线心跳 + 宿主身份上报）。"""
        self._require_extension_token(headers)
        return {"command": self._extension_hub.next_command(wait_seconds, host_report)}

    def extension_command_result(self, headers, body: Any) -> dict:
        """扩展回传命令结果。未知/已超时 id 返回 received=false（不报错）。"""
        self._require_extension_token(headers)
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "result body must be a JSON object")
        matched = self._extension_hub.deliver_result(body)
        return {"received": matched, "id": body.get("id")}

    def extension_command_submit(self, body: Any) -> dict:
        """执行器侧提交命令并阻塞等结果（run 子进程 → devserver）。"""
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "request body must be a JSON object")
        op = str(body.get("op") or "")
        if not op:
            raise ApiError(400, "BAD_REQUEST", "missing 'op'")
        try:
            timeout_seconds = float(body.get("timeoutSeconds") or DEFAULT_COMMAND_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            raise ApiError(400, "BAD_REQUEST", "'timeoutSeconds' must be a number") from None
        command = {"op": op, "args": body.get("args") or {}}
        return self._extension_hub.submit(command, max(0.1, timeout_seconds) + 1.0)

    # -- 扩展静默安装托管（update manifest XML + CRX，见 docs/extension-install.md）--

    def _packed_extension(self):
        packed = load_packed_extension(self._extension_build_dir)
        if packed is None:
            raise ApiError(
                503, "EXTENSION_NOT_PACKED", "扩展尚未打包：先运行 rpa-core install-extension"
            )
        return packed

    def extension_crx_bytes(self) -> bytes:
        return self._packed_extension().crx_path.read_bytes()

    def extension_manifest_xml(self, base_url: str) -> str:
        return update_manifest_xml(self._packed_extension(), f"{base_url}/api/extension/crx")

    def extension_status_view(self) -> dict:
        """只读状态：双浏览器 registry/开发者模式(Load unpacked) 已装与已启用。

        同时返回 extensionDir（开发者模式引导需要加载的源码目录）。
        """
        source_dir = extension_root()
        status = extension_status(
            "",
            self._extension_build_dir,
            extension_dir=source_dir,
        )
        status["extensionDir"] = str(source_dir)
        status["enableHint"] = {
            "chrome": "chrome://extensions", "edge": "edge://extensions",
        }
        status["installMode"] = "load-unpacked"
        return status

    def extension_open_dir_view(self) -> dict:
        """在系统文件管理器打开扩展源码目录（Load unpacked 引导：方便定位/复制路径）。"""
        source = extension_root()
        if not source.is_dir():
            raise ApiError(404, "NOT_FOUND", f"extension source dir missing: {source}")
        opened = open_path_in_explorer(source)
        return {"opened": opened, "extensionDir": str(source)}

    def extension_open_browser_view(self, body: Any) -> dict:
        """启动指定浏览器（引导第 2 步：先把浏览器打开/聚焦）。"""
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "request body must be a JSON object")
        browser = str(body.get("browser") or "")
        if browser not in ("chrome", "edge"):
            raise ApiError(400, "BAD_REQUEST", "browser must be one of: chrome, edge")
        opened = open_browser(browser)
        if not opened:
            raise ApiError(404, "NOT_FOUND", f"{browser} executable not found")
        return {"opened": True, "browser": browser}

    def extension_install_view(self, body: Any) -> dict:
        """免管理员外部注册表安装（浏览器可选 chrome/edge/both）。写 HKCU，需本机 Windows。"""
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "request body must be a JSON object")
        browser = str(body.get("browser") or "both")
        if browser not in ("chrome", "edge", "both"):
            raise ApiError(400, "BAD_REQUEST", "browser must be one of: chrome, edge, both")
        if browser == "both":
            targets = ("chrome", "edge")
        else:
            targets = (browser,)
        # 未打包则先打包（devserver 进程内复用能力层；CRX 托管已依赖同一 build 目录）
        packed = load_packed_extension(self._extension_build_dir)
        if packed is None:
            try:
                packed = pack_extension(extension_root(), self._extension_build_dir)
            except ExtensionInstallError as exc:
                raise ApiError(503, exc.code, str(exc)) from exc
        try:
            guided = install_external_guided(
                packed.extension_id, packed.crx_path, packed.version, browsers=targets
            )
        except ExtensionInstallError as exc:
            raise ApiError(502, exc.code, str(exc)) from exc
        status = extension_status(packed.extension_id, self._extension_build_dir)
        return {
            "extensionId": packed.extension_id,
            "version": packed.version,
            "browsers": guided["installed"],
            "unblocked": guided["unblocked"],
            "runningBrowsers": guided["runningBrowsers"],
            "enableHint": {"chrome": "chrome://extensions", "edge": "edge://extensions"},
            "note": guided["note"],
            "status": status,
        }

    def extension_unblock_view(self, body: Any) -> dict:
        """清除浏览器 external_uninstalls 卸载记忆（装不上/loader 跳过时用）。

        写浏览器 profile 的 Preferences；须在浏览器关闭时执行才不会被覆写。
        """
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "request body must be a JSON object")
        browser = str(body.get("browser") or "both")
        if browser not in ("chrome", "edge", "both"):
            raise ApiError(400, "BAD_REQUEST", "browser must be one of: chrome, edge, both")
        packed = load_packed_extension(self._extension_build_dir)
        if packed is None:
            raise ApiError(503, "EXTENSION_NOT_PACKED", "扩展尚未打包；先运行安装")
        if browser == "both":
            cleared = clear_uninstall_block(packed.extension_id)
        else:
            result = clear_uninstall_block(packed.extension_id)
            cleared = {browser: result.get(browser, [])}
        return {
            "extensionId": packed.extension_id,
            "cleared": cleared,
            "note": "请先关闭浏览器再执行（运行中会被覆写），随后冷启动浏览器使 loader 重新扫描",
        }

    def _save_element(self, flow: str, descriptor: dict, save_as: str) -> dict:
        store = self._element_store(flow)
        element = ElementDescriptor(
            kind=descriptor["kind"],
            selector=descriptor["selector"],
            verify_count=descriptor.get("verifyCount", 0),
            metadata=descriptor.get("metadata", {}),
        )
        store.write(save_as, element.document())
        return {"savedAs": save_as, "flow": flow}

    def list_elements(self, flow: str) -> dict:
        try:
            return {"elements": self._element_store(flow).list()}
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc

    def get_element(self, flow: str, name: str) -> dict:
        try:
            return self._element_store(flow).read(name)
        except WorkflowNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        except WorkflowStoreError as exc:
            raise ApiError(400, "BAD_REQUEST", str(exc)) from exc

    def put_element(self, flow: str, name: str, body: Any) -> dict:
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_REQUEST", "element document must be a JSON object")
        element = _validate_element_document(body)
        try:
            self._element_store(flow).write(name, element.document())
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        return {"name": name, "flow": flow}

    def delete_element(self, flow: str, name: str) -> dict:
        try:
            self._element_store(flow).delete(name)
        except WorkflowNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        return {"name": name, "flow": flow, "deleted": True}

    def verify_element(self, flow: str, name: str) -> dict:
        """结构校验：描述符模型 + selector 语义。活体验证需捕获会话内完成。"""
        try:
            document = self._element_store(flow).read(name)
        except WorkflowNotFoundError as exc:
            raise ApiError(404, "NOT_FOUND", str(exc)) from exc
        except WorkflowNameError as exc:
            raise ApiError(403, "FORBIDDEN", str(exc)) from exc
        errors: list[dict] = []
        try:
            element = _validate_element_document(document)
        except ApiError as exc:
            return {"valid": False, "errors": [{"message": exc.message}], "verifyCount": None}
        errors.extend(_selector_errors(element))
        return {
            "valid": not errors,
            "errors": errors,
            "verifyCount": None,
            "note": "结构校验；活体验证（命中数）需在捕获会话内完成",
        }

    def _compile_result(self, valid: bool, errors: list[dict]) -> dict:
        return {"valid": valid, "errors": errors, "catalog_digest": self._catalog.digest}


def _validation_errors(exc: ValidationError) -> list[dict]:
    errors = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        errors.append({"path": location, "message": error["msg"]})
    return errors


def _validate_element_document(body: dict) -> ElementDescriptor:
    try:
        return validate_element_document(body)
    except ElementDocumentError as exc:
        raise ApiError(400, "BAD_REQUEST", str(exc)) from exc


def _selector_errors(element: ElementDescriptor) -> list[dict]:
    return selector_errors(element)
