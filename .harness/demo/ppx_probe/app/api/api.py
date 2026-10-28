"""PPX 原型后端：把 rpa_core 的真实能力层挂到 PPX 的 RPC 上。

职责——读 catalog 快照、把 workflow AST 摊平成卡片、给节点算出可编辑字段、
保存一个节点的参数（走与 Qt 完全一致的编码口径）、跑一次编译校验。
它不承载 run（ADR 0011：编辑进程不承载运行），也不复制任何能力层逻辑。

★ 保存**不走 `model_dump`**：实测 8 个样例流程里 0 个无损——`model_dump` 会把
`elementRefs`/`output_aliases`/`retry_*`/`_exprModes` 等默认值全灌进文件。
所以采用「在原始 dict 上定点改一个键」+ `Workflow.model_validate` 复校 + Qt 同款编码。
"""

from __future__ import annotations

import hashlib
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
# 保存试验台：复制一份样例进来改，绝不碰 examples/ 与 workflows/ 里的既有流程。
SANDBOX = Path(__file__).resolve().parents[2] / "_sandbox"


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


# ---------------------------------------------------------------------------
# 保存：读 → 定点改 → 复校 → 编码落盘（与 Qt `save_workflow` 同口径）
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_document(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), raw


def _encode_document(document: dict[str, Any]) -> bytes:
    """Qt `save_workflow()` 的编码：UTF-8、indent=2、ensure_ascii=False、末尾换行。"""
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


_NESTED = ("children", "then", "else", "catch")


def _find_node(document: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    """在**原始 dict**（不是模型）上按 id 找节点，改完才能保持未触碰部分逐字节不变。"""

    def walk(node: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None
        if node.get("id") == node_id:
            return node
        for key in _NESTED:
            for child in node.get(key) or []:
                found = walk(child)
                if found is not None:
                    return found
        return None

    return walk(document.get("root") or {})


def _json_ready(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
}


def _infer_type(value: Any) -> str:
    """manifest 没声明时的兜底：按磁盘上现有值的类型定契约。"""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return ""


def _type_contract(declared: dict[str, Any], current: Any) -> tuple[str, str]:
    """返回 (类型契约, 来源)。manifest 有声明就以声明为准，没有才按既有值推断。

    ★ 不这么做就会静默降级：`headless: false` 在 manifest 里没声明，若统一按 string 处理，
    用户输入 `true` 会被存成 `"true"`——保存成功、编译通过、运行期才炸。
    """
    declared_type = str(declared.get("type") or "")
    if declared_type:
        return declared_type, "manifest"
    inferred = _infer_type(current)
    return inferred or "string", "existing"


def _coerce(declared: str, text: str) -> Any:
    """按类型契约把用户输入变成 JSON 值；不合型就报错，绝不静默转字符串。"""
    if declared in ("", "string", None):
        return text
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"不是合法 JSON（{declared} 类型需要字面量，如 5 / true / [1,2]）") from exc
    check = _TYPE_CHECKS.get(declared)
    if check is not None and not check(value):
        raise ValueError(f"类型不符：声明 {declared}，输入解析成 {type(value).__name__}")
    return value


def _field_descriptors(command: str, node: dict[str, Any]) -> list[dict[str, Any]]:
    """把「文件里显式写了的参数」× manifest 声明，摊成前端可渲染的字段表。

    S5 只覆盖**已有键**：新增参数涉及 require/default/oneOf 语义（Qt 的 `param_form.py`
    是 1391 行），不在这一片里做，见 M65 §五。
    """
    manifest = _catalog_snapshot().get(command)
    schema = dict(getattr(manifest, "input_schema", None) or {})
    properties = dict(schema.get("properties") or {})
    required = set(schema.get("required") or ())
    current = dict(node.get("with") or {})

    fields: list[dict[str, Any]] = []
    for key, value in current.items():
        if key.startswith("_"):
            continue
        declared = dict(properties.get(key) or {})
        contract, source = _type_contract(declared, value)
        labels = dict(declared.get("x-enum-labels") or {})
        enum = [
            {"value": option, "label": labels.get(option, str(option))}
            for option in (declared.get("enum") or [])
        ]
        fields.append(
            {
                "key": key,
                "type": contract,
                "typeSource": source,
                "value": value,
                "valueText": _json_ready(value),
                "enum": enum,
                "required": key in required,
                "declared": bool(declared),
                "description": str(declared.get("description") or ""),
            }
        )
    return fields


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
    for base in (REPO / "examples", REPO / "workflows", SANDBOX):
        if not base.is_dir():
            continue
        if base == SANDBOX:
            for wf in sorted(base.glob("*.json")):
                flows.append(
                    {
                        "path": wf.relative_to(REPO).as_posix(),
                        "label": f"sandbox/{wf.stem}",
                    }
                )
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
    data, raw = _read_document(target)
    workflow = Workflow.model_validate(data)
    dumped = workflow.root.model_dump(mode="json", by_alias=True)
    flattener = _Flattener(_display_names())
    flattener.walk(dumped, 0)

    index: dict[str, Any] = {}

    def collect(node: dict[str, Any]) -> None:
        if node.get("id"):
            index[node["id"]] = node
        for key in _NESTED:
            for child in node.get(key) or []:
                collect(child)

    collect(dumped)
    # SHA 用于「保存前发现文件被别处改过」——打不开这个闸门就等于允许覆盖别人的编辑
    _flows[path] = {"nodes": index, "sha": _sha256(raw)}
    return {
        "id": workflow.id,
        "name": workflow.name,
        "inputs": workflow.inputs,
        "path": path,
        "sha": _sha256(raw)[:12],
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
    node = entry["nodes"].get(node_id)
    fields: list[dict[str, Any]] = []
    if node and node.get("type") == "action":
        fields = _field_descriptors(node.get("command", ""), node)
    return {
        "id": node_id,
        "node": node,
        "fields": fields,
        "editable": bool(fields),
        "rpcMs": round((time.perf_counter() - started) * 1000, 2),
    }


def _compile(document: dict[str, Any]) -> dict[str, Any]:
    try:
        workflow = Workflow.model_validate(document)
        plan = WorkflowCompiler(_catalog_snapshot()).compile(workflow, _all_capabilities())
    except WorkflowCompileError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 模型校验失败也是结论，不能吞
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": True,
        "digest": plan.catalog_digest[:12],
        "capabilities": sorted(plan.required_capabilities),
        "workflowId": plan.workflow.id,
    }


@api_method("rpa.saveNodeParams")
def save_node_params(path: str, node_id: str, params: dict[str, str]) -> dict[str, Any]:
    """改一个 action 节点的参数并落盘。

    顺序：SHA 闸门 → 定位 → 逐键按声明类型转换 → 全量模型复校 → 编码比对 → 落盘 → 编译。
    任一步不过就**不写盘**，返回 `ok: False` 与原因。
    """
    started = time.perf_counter()
    target = _flow_path(path)
    document, raw = _read_document(target)

    entry = _flows.get(path)
    if entry is not None and entry["sha"] != _sha256(raw):
        return {
            "ok": False,
            "code": "FLOW_CHANGED",
            "error": "文件在打开后被其他程序改过，已拒绝覆盖（重新打开流程再改）",
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    node = _find_node(document, node_id)
    if node is None:
        return {
            "ok": False,
            "code": "NODE_NOT_FOUND",
            "error": f"节点不存在：{node_id}",
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
    if node.get("type") != "action":
        return {
            "ok": False,
            "code": "NODE_NOT_EDITABLE",
            "error": f"S5 只改 action 节点参数，{node.get('type')} 节点不在范围内",
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    manifest = _catalog_snapshot().get(node.get("command", ""))
    properties = dict(
        ((getattr(manifest, "input_schema", None) or {}).get("properties")) or {}
    )
    with_map = dict(node.get("with") or {})

    rejected: list[dict[str, str]] = []
    applied: dict[str, Any] = {}
    for key, text in params.items():
        if key not in with_map:
            rejected.append({"key": key, "reason": "文件里没有这个键（新增参数属 S6，本片不做）"})
            continue
        contract, source = _type_contract(dict(properties.get(key) or {}), with_map[key])
        try:
            value = _coerce(contract, text)
        except ValueError as exc:
            rejected.append({"key": key, "reason": str(exc)})
            continue
        if source == "existing" and _infer_type(value) != _infer_type(with_map[key]):
            # 按既有值推断出来的契约，改完类型也必须一致，否则就是换了参数形状
            rejected.append(
                {
                    "key": key,
                    "reason": f"类型变了：原值 {_infer_type(with_map[key])}，输入 {_infer_type(value)}"
                    "（manifest 未声明该参数，只能沿用原类型）",
                }
            )
            continue
        applied[key] = value
    if rejected:
        return {
            "ok": False,
            "code": "INVALID_VALUE",
            "error": "参数不合法，未写盘：" + "；".join(f"{r['key']} — {r['reason']}" for r in rejected),
            "rejected": rejected,
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    node["with"] = {**with_map, **applied}
    try:
        Workflow.model_validate(document)
    except Exception as exc:  # noqa: BLE001 - 校验失败按「不写盘」处理，把原因带回去
        return {
            "ok": False,
            "code": "INVALID_WORKFLOW",
            "error": f"改完结构不合法，未写盘：{type(exc).__name__}: {exc}",
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    encoded = _encode_document(document)
    changed = encoded != raw
    if changed:
        target.write_bytes(encoded)

    compile_result = _compile(document)
    flow = open_flow(path)
    return {
        "ok": True,
        "saved": changed,
        "changed": changed,
        "bytes": [len(raw), len(encoded)],
        "sha": _sha256(encoded)[:12],
        "ms": round((time.perf_counter() - started) * 1000, 1),
        "compile": compile_result,
        "flow": flow,
    }


@api_method("rpa.compileCheck")
def compile_check(path: str) -> dict[str, Any]:
    started = time.perf_counter()
    data, _ = _read_document(_flow_path(path))
    result = _compile(data)
    result["ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result
