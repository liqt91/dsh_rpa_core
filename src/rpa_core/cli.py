import argparse
import asyncio
import json
from pathlib import Path

from rpa_core.capture import (
    BrowserBskCaptureSession,
    BrowserCaptureSession,
    DesktopCaptureSession,
)
from rpa_core.catalog import load_catalog
from rpa_core.compiler import WorkflowCompiler
from rpa_core.devserver import DevServer
from rpa_core.executors import (
    DesktopExecutor,
    ExecutorRegistry,
    PlaywrightExecutor,
    PythonWorkerExecutor,
    Win32DesktopExecutor,
)
from rpa_core.model.workflow import Workflow
from rpa_core.runtime import Orchestrator
from rpa_core.runtime.checkpoint import CheckpointError


def _load_workflow(path: Path) -> Workflow:
    return Workflow.model_validate_json(path.read_text(encoding="utf-8"))


def _compile(path: Path):
    root = Path(__file__).resolve().parents[2]
    catalog = load_catalog(root / "commands")
    workflow = _load_workflow(path)
    plan = WorkflowCompiler(catalog).compile(
        workflow,
        {
            "browser.read",
            "browser.control",
            "desktop.control",
            "process.start",
            "workspace.write",
        },
    )
    return root, catalog, plan


def _browser_capture_factory(**kwargs):
    """devserver 捕获工厂分发：transport=bsk 走 BrowserBskCaptureSession（用户真实浏览器）。"""
    if kwargs.get("transport") == "bsk":
        return BrowserBskCaptureSession(**kwargs)
    return BrowserCaptureSession(**kwargs)


def _cmd_catalog() -> int:
    """能力层 catalog 摘要（ADR 0006 §6 通道对齐：CLI 与 devserver 同权）。"""
    root = Path(__file__).resolve().parents[2]
    catalog = load_catalog(root / "commands")
    commands = [
        {
            "id": manifest.id,
            "version": manifest.version,
            "kind": manifest.kind.value,
            "effect": manifest.effect.model_dump(mode="json"),
        }
        for manifest in (catalog[command_id] for command_id in sorted(catalog))
    ]
    print(
        json.dumps({"digest": catalog.digest, "commands": commands},
                   ensure_ascii=False, indent=2)
    )
    return 0


def _save_flow_element(workflows_root: Path, flow: str, name: str, descriptor: dict) -> dict:
    """描述符存入流程元素资产（与 devserver `_save_element` 同一能力，store 层复用）。"""
    from rpa_core.devserver.store import WorkflowDirStore, WorkflowStore
    from rpa_core.model.capture import ElementDescriptor

    folder = WorkflowDirStore(workflows_root).directory(flow)
    element = ElementDescriptor(
        kind=descriptor["kind"],
        selector=descriptor["selector"],
        verifyCount=descriptor.get("verifyCount", 0),
        metadata=descriptor.get("metadata", {}),
    )
    WorkflowStore(folder / "elements").write(name, element.document())
    return {"savedAs": name, "flow": flow}


def _cmd_capture(args) -> int:
    """一次性捕获会话：start → pick（阻塞至用户手势/超时）→ 可选落库 → close。"""
    if args.target == "browser":
        session = _browser_capture_factory(
            transport=args.transport,
            browser_instance_id=args.browser_instance_id,
            start_url=args.start_url,
            user_data_dir=args.user_data_dir,
            headless=args.headless,
            browser_type=args.browser_type,
            page_url=args.page_url,
        )
        start = session.start
        pick = lambda timeout: session.pick(timeout_seconds=timeout, click_css=args.click_css)  # noqa: E731
    else:
        session = DesktopCaptureSession(
            hotkey=args.hotkey,
            timeout_seconds=args.timeout,
            point=_parse_point(args.point),
            window_handle=args.window_handle,
            hover=args.hover,
        )
        start = lambda: None  # noqa: E731
        pick = lambda timeout: session.pick(timeout_seconds=timeout)  # noqa: E731
    try:
        start()
        try:
            result = pick(args.timeout)
        except KeyboardInterrupt:
            print(json.dumps({"cancelled": True, "reason": "keyboard interrupt"}))
            return 130
        if result.get("kind") and args.save_as:
            if not args.flow:
                print(json.dumps({"error": "BAD_REQUEST", "message": "--save-as 需要 --flow"}))
                return 2
            result.update(_save_flow_element(args.workflows, args.flow, args.save_as, result))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        try:
            session.cancel()
        except Exception:
            pass
        try:
            session.close()
        except Exception:
            pass


def _parse_point(raw: str | None) -> dict | None:
    if not raw:
        return None
    x, y = raw.split(",")
    return {"x": int(x), "y": int(y)}


def _cmd_elements(args) -> int:
    from rpa_core.devserver.store import (
        WorkflowDirStore,
        WorkflowNameError,
        WorkflowNotFoundError,
        WorkflowStore,
        WorkflowStoreError,
    )
    from rpa_core.model.capture import (
        ElementDocumentError,
        selector_errors,
        validate_element_document,
    )

    try:
        folder = WorkflowDirStore(args.workflows).directory(args.flow)
    except WorkflowNameError as exc:
        print(json.dumps({"error": "FORBIDDEN", "message": str(exc)}))
        return 3
    store = WorkflowStore(folder / "elements", create=False)
    try:
        if args.elements_action == "list":
            print(json.dumps({"elements": store.list()}, indent=2))
            return 0
        document = store.read(args.name)
        if args.elements_action == "show":
            print(json.dumps(document, ensure_ascii=False, indent=2))
            return 0
        # verify
        try:
            element = validate_element_document(document)
            errors = selector_errors(element)
        except ElementDocumentError as exc:
            errors = [{"path": exc.path, "message": exc.message}]
        print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
        return 0
    except WorkflowNotFoundError as exc:
        print(json.dumps({"error": "NOT_FOUND", "message": str(exc)}))
        return 1
    except WorkflowNameError as exc:
        print(json.dumps({"error": "FORBIDDEN", "message": str(exc)}))
        return 3
    except WorkflowStoreError as exc:
        print(json.dumps({"error": "BAD_REQUEST", "message": str(exc)}))
        return 2


def _serve(args) -> int:
    root = Path(__file__).resolve().parents[2]
    server = DevServer(
        commands_root=root / "commands",
        workflows_root=args.workflows,
        port=args.port,
        browser_capture_factory=_browser_capture_factory,
        desktop_capture_factory=DesktopCaptureSession,
    )
    print(
        json.dumps(
            {"listening": f"http://{server.host}:{server.port}", "workflows": str(args.workflows)},
            indent=2,
        ),
        flush=True,
    )
    try:
        server.serve()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="rpa-core")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("validate", "run", "resume", "devserver", "catalog", "capture", "elements"):
        sub = subparsers.add_parser(action)
        if action == "devserver":
            sub.add_argument("--port", type=int, default=8765)
            sub.add_argument("--workflows", type=Path, default=Path("workflows"))
            continue
        if action == "catalog":
            continue
        if action == "capture":
            cap_sub = sub.add_subparsers(dest="target", required=True)
            browser = cap_sub.add_parser("browser")
            browser.add_argument("--transport", choices=["bsk", "persistent", "user-browser"],
                                 default="bsk")
            browser.add_argument("--browser-instance-id")
            browser.add_argument("--browser-type", default="edge")
            browser.add_argument("--start-url")
            browser.add_argument("--page-url")
            browser.add_argument("--user-data-dir")
            browser.add_argument("--headless", action="store_true")
            browser.add_argument("--timeout", type=float, default=60.0)
            browser.add_argument("--click-css", help="自动化验收：CDP 合成 Ctrl+Click")
            browser.add_argument("--save-as")
            browser.add_argument("--flow")
            browser.add_argument("--workflows", type=Path, default=Path("workflows"))
            desktop = cap_sub.add_parser("desktop")
            desktop.add_argument("--hotkey", default="F9")
            desktop.add_argument("--timeout", type=float, default=90.0)
            desktop.add_argument("--point", help="测试模式：x,y 坐标直接捕获")
            desktop.add_argument("--window-handle", type=int)
            desktop.add_argument("--hover", action="store_true",
                                 help="hover 模式：鼠标移动实时高亮，热键或 Ctrl+Click 捕获")
            desktop.add_argument("--save-as")
            desktop.add_argument("--flow")
            desktop.add_argument("--workflows", type=Path, default=Path("workflows"))
            continue
        if action == "elements":
            el_sub = sub.add_subparsers(dest="elements_action", required=True)
            for elements_action in ("list", "show", "verify"):
                el = el_sub.add_parser(elements_action)
                el.add_argument("--flow", required=True)
                el.add_argument("--workflows", type=Path, default=Path("workflows"))
                if elements_action != "list":
                    el.add_argument("name")
            continue
        sub.add_argument("workflow", type=Path)
        sub.add_argument("--artifacts", type=Path, default=Path("run_artifacts"))
        if action == "resume":
            sub.add_argument("--run-id", required=True)
            sub.add_argument("--allow-indeterminate", action="store_true")
    args = parser.parse_args()
    if args.action == "devserver":
        return _serve(args)
    if args.action == "catalog":
        return _cmd_catalog()
    if args.action == "capture":
        return _cmd_capture(args)
    if args.action == "elements":
        return _cmd_elements(args)
    _root, catalog, plan = _compile(args.workflow)
    if args.action == "validate":
        print(json.dumps({"valid": True, "catalogDigest": catalog.digest}, indent=2))
        return 0

    async def run() -> int:
        browser = PlaywrightExecutor()
        registry = ExecutorRegistry(
            {
                "browser.playwright": browser,
                "desktop.uia": DesktopExecutor(),
                "desktop.win32": Win32DesktopExecutor(),
                "python.worker": PythonWorkerExecutor(),
            }
        )
        try:
            orchestrator = Orchestrator(catalog, registry, args.artifacts)
            if args.action == "resume":
                result = await orchestrator.resume(
                    plan,
                    args.run_id,
                    allow_indeterminate=args.allow_indeterminate,
                ).wait()
            else:
                result = await orchestrator.run(plan)
            print(result.model_dump_json(indent=2))
            return 0 if result.status.value == "succeeded" else 1
        except CheckpointError as exc:
            print(json.dumps({"error": "CHECKPOINT_FAILED", "message": str(exc)}, indent=2))
            return 2
        finally:
            await registry.close()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
