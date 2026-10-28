"""PPX 原型后端：把 rpa_core 的真实能力层挂到 PPX 的 RPC 上。

这一层只做三件事——读 catalog 快照、把 workflow AST 摊平成卡片、跑一次编译校验。
它不承载 run（ADR 0011：编辑进程不承载运行），也不复制任何能力层逻辑。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from ppx_py import api_method


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "src" / "rpa_core").is_dir():
            return parent
    raise RuntimeError("找不到 rpa_core 仓库根（src/rpa_core 不在任何父目录下）")


REPO = _repo_root()
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler  # noqa: E402
from rpa_core.gui.i18n import load_command_display_names  # noqa: E402
from rpa_core.model.workflow import Workflow  # noqa: E402

_BOOT = time.perf_counter()
_catalog = None
_labels: dict[str, str] | None = None
_flows: dict[str, dict[str, Any]] = {}


def _catalog_snapshot():
    global _catalog
    if _catalog is None:
        _catalog = load_catalog(REPO / "commands")
    return _catalog


def _display_names() -> dict[str, str]:
    global _labels
    if _labels is None:
        _labels = load_command_display_names()
    return _labels


def _all_capabilities() -> set[str]:
    """原型简化：把 catalog 里声明过的能力全部视为已授予。

    真正的授权来源（用户勾选 / 流程声明）属 M 系列未落地的部分，这里只求编译链路能跑通。
    """
    granted: set[str] = set()
    for manifest in _catalog_snapshot().values():
        granted.update(getattr(manifest, "capabilities", ()) or ())
    return granted


def _flow_path(rel: str) -> Path:
    target = (REPO / rel).resolve()
    if not target.is_file():
        raise ValueError(f"流程文件不存在: {rel}")
    return target


def _summarize(with_: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in with_.items():
        if key.startswith("_"):
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if len(text) > 16:
            text = text[:15] + "…"
        parts.append(f"{key}={text}")
        if len(parts) == 2:
            break
    total = len([k for k in with_ if not k.startswith("_")])
    tail = f"  +{total - 2}" if total > 2 else ""
    return " ".join(parts) + tail


def _compact(value: Any, limit: int = 18) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _condition_summary(condition: dict[str, Any]) -> str:
    left = _compact(condition.get("left"))
    op = condition.get("op", "")
    right = condition.get("right")
    if right in (None, ""):
        return f"{left} {op}"
    return f"{left} {op} {_compact(right)}"


class _Flattener:
    """AST → 卡片行。容器节点占一行，if/try 的 then/catch 用虚拟组行表示。"""

    def __init__(self, labels: dict[str, str]) -> None:
        self.labels = labels
        self.cards: list[dict[str, Any]] = []
        self._seq = 0

    def add(self, **card: Any) -> None:
        if card.get("kind") != "group":
            self._seq += 1
            card.setdefault("no", self._seq)
        self.cards.append(card)

    def group(self, label: str, depth: int) -> None:
        self.add(kind="group", label=label, depth=depth, command="", summary="")

    def walk(self, node: dict[str, Any], depth: int) -> None:
        kind = node.get("type")
        node_id = node.get("id", "")
        if kind == "action":
            command = node.get("command", "")
            self.add(
                kind="action",
                id=node_id,
                depth=depth,
                command=command,
                label=self.labels.get(command, command),
                summary=_summarize(node.get("with") or {}),
            )
        elif kind == "return":
            self.add(
                kind="return", id=node_id, depth=depth, command="return", label="返回", summary=""
            )
        elif kind == "sequence":
            for child in node.get("children") or []:
                self.walk(child, depth)
        elif kind == "if":
            cond = node.get("condition") or {}
            self.add(
                kind="if",
                id=node_id,
                depth=depth,
                command="if",
                label="如果",
                summary=_condition_summary(cond),
            )
            self.group("则执行", depth + 1)
            for child in node.get("then") or []:
                self.walk(child, depth + 1)
            alt = node.get("else") or []
            if alt:
                self.group("否则", depth + 1)
                for child in alt:
                    self.walk(child, depth + 1)
        elif kind == "forEach":
            self.add(
                kind="forEach",
                id=node_id,
                depth=depth,
                command="forEach",
                label="循环每一项",
                summary=f"{node.get('items')} as {node.get('item_var')}",
            )
            for child in node.get("children") or []:
                self.walk(child, depth + 1)
        elif kind == "try":
            self.add(
                kind="try", id=node_id, depth=depth, command="try", label="尝试", summary=""
            )
            for child in node.get("children") or []:
                self.walk(child, depth + 1)
            caught = node.get("catch") or []
            if caught:
                self.group("异常", depth + 1)
                for child in caught:
                    self.walk(child, depth + 1)
        else:
            self.add(
                kind="unknown",
                id=node_id,
                depth=depth,
                command=kind or "?",
                label=f"未知节点 {kind}",
                summary="",
            )


@api_method("rpa.bootstrap")
def bootstrap() -> dict[str, Any]:
    started = time.perf_counter()
    catalog = _catalog_snapshot()
    labels = _display_names()
    commands = [
        {"id": cid, "label": labels.get(cid, cid), "ns": cid.split(".")[0]}
        for cid in sorted(catalog)
    ]
    flows: list[dict[str, str]] = []
    for base in (REPO / "examples", REPO / "workflows"):
        if not base.is_dir():
            continue
        for wf in sorted(base.glob("*/workflow.json")):
            flows.append(
                {
                    "path": wf.relative_to(REPO).as_posix(),
                    "label": f"{base.name}/{wf.parent.name}",
                }
            )
    return {
        "commands": commands,
        "flows": flows,
        "catalogDigest": catalog.digest[:12],
        "catalogMs": round((time.perf_counter() - started) * 1000, 1),
        "bootMs": round((time.perf_counter() - _BOOT) * 1000, 1),
        "python": sys.version.split()[0],
        "repo": str(REPO),
    }


@api_method("rpa.openFlow")
def open_flow(path: str) -> dict[str, Any]:
    started = time.perf_counter()
    target = _flow_path(path)
    data = json.loads(target.read_text(encoding="utf-8"))
    workflow = Workflow.model_validate(data)
    dumped = workflow.root.model_dump(mode="json", by_alias=True)
    flattener = _Flattener(_display_names())
    flattener.walk(dumped, 0)

    index: dict[str, Any] = {}

    def collect(node: dict[str, Any]) -> None:
        if node.get("id"):
            index[node["id"]] = node
        for key in ("children", "then", "else", "catch"):
            for child in node.get(key) or []:
                collect(child)

    collect(dumped)
    _flows[path] = {"nodes": index}
    return {
        "id": workflow.id,
        "name": workflow.name,
        "inputs": workflow.inputs,
        "path": path,
        "cards": flattener.cards,
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


@api_method("rpa.nodeDetail")
def node_detail(path: str, node_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    entry = _flows.get(path)
    if entry is None:
        open_flow(path)
        entry = _flows[path]
    return {
        "id": node_id,
        "node": entry["nodes"].get(node_id),
        "rpcMs": round((time.perf_counter() - started) * 1000, 2),
    }


@api_method("rpa.compileCheck")
def compile_check(path: str) -> dict[str, Any]:
    started = time.perf_counter()
    data = json.loads(_flow_path(path).read_text(encoding="utf-8"))
    workflow = Workflow.model_validate(data)
    try:
        plan = WorkflowCompiler(_catalog_snapshot()).compile(workflow, _all_capabilities())
    except WorkflowCompileError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
    return {
        "ok": True,
        "digest": plan.catalog_digest[:12],
        "capabilities": sorted(plan.required_capabilities),
        "workflowId": plan.workflow.id,
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }
