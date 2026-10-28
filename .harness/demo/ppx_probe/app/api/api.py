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
import re
import sys
import time
from pathlib import Path
from typing import Any, get_args

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
from rpa_core.gui.i18n import load_command_display_names, load_op_labels  # noqa: E402
from rpa_core.model.workflow import Condition, Workflow  # noqa: E402

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


def _type_contract(declared: dict[str, Any], current: Any, *, present: bool) -> tuple[str, str]:
    """返回 (类型契约, 来源)。

    manifest 有声明就以声明为准；否则按节点里既有值的类型推断；两者都没有
    （声明缺失 + 键不存在）只能回落到 string。

    ★ 声明里的 `type` **可能是列表**（`data.setVar.value` 是
    `["string","number","integer","boolean","object","array","null"]`）——直接 `str()`
    会得到 `"['string', 'number', ...]"` 这种垃圾契约，校验必然失败。取第一个非
    `null` 的候选，与 Qt `_effective_type()` 同口径。

    ★ 不区分「声明优先」就会静默降级：`headless: false` 在 manifest 里没声明，若统一按
    string 处理，用户输入 `true` 会被存成 `"true"`——保存成功、编译通过、运行期才炸。
    """
    raw = declared.get("type")
    if isinstance(raw, list):
        for candidate in raw:
            if candidate != "null":
                return str(candidate), "manifest"
        return "string", "manifest"
    if raw:
        return str(raw), "manifest"
    if present:
        return _infer_type(current) or "string", "existing"
    return "string", "absent"


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


def _is_reserved(key: str) -> bool:
    """`_` 前缀 = 编辑器私有键（`_exprModes` 之类），不是命令参数，不进表单也不许改。"""
    return key.startswith("_")


# manifest 里「不给用户填」的字段声明。`flowDir` 由运行期注入、`varName` 由插入器分配，
# 让用户手填等于给他一个改了也没用的假入口。
_HIDDEN_FLAGS = ("hidden", "x-hidden")


def _field_order(schema: dict[str, Any]) -> tuple[list[tuple[str, str | None]], list[dict[str, str]]]:
    """字段渲染顺序 + 分组定义。

    返回 ([(key, group_label|None), ...], [{label, collapsed}, ...])。
    顺序以 manifest 声明为准——**不是**以节点里已有的键为准：Qt 的 `ParamForm`
    遍历的就是 `properties`，「空 = 未设置」。只渲染已有键会让「可以设但还没设」
    的参数根本没有入口。
    """
    properties = dict(schema.get("properties") or {})
    groups = schema.get("x-param-groups")
    plan: list[tuple[str, str | None]] = []
    group_defs: list[dict[str, str]] = []
    claimed: set[str] = set()
    if isinstance(groups, list) and groups:
        for group in groups:
            fields = [
                k for k in (group.get("fields") or []) if k in properties and k not in claimed
            ]
            if not fields:
                continue
            label = str(group.get("label") or "参数")
            claimed.update(fields)
            group_defs.append({"label": label, "collapsed": bool(group.get("collapsed"))})
            plan.extend((k, label) for k in fields)
        rest = [k for k in properties if k not in claimed]
        if rest:
            group_defs.append({"label": "其他", "collapsed": False})
            plan.extend((k, "其他") for k in rest)
    else:
        plan.extend((k, None) for k in properties)
    return plan, group_defs


def _field_descriptors(command: str, node: dict[str, Any]) -> dict[str, Any]:
    """把 manifest 声明 × 节点当前值摊成前端可渲染的字段表。

    覆盖**声明了但节点里没写**的参数（`set=False`），前端据此渲染「未设置」态。
    反过来，节点里有、manifest 没声明的键也保留（归入「未声明」组）——它们是既有
    文件里的真实内容，藏起来用户就改不了，也看不见自己踩了什么。
    """
    manifest = _catalog_snapshot().get(command)
    schema = dict(getattr(manifest, "input_schema", None) or {})
    properties = dict(schema.get("properties") or {})
    required = set(schema.get("required") or ())
    current = {k: v for k, v in dict(node.get("with") or {}).items() if not _is_reserved(k)}
    plan, group_defs = _field_order(schema)

    fields: list[dict[str, Any]] = []
    for key, group in plan:
        declared = dict(properties.get(key) or {})
        if any(declared.get(flag) for flag in _HIDDEN_FLAGS):
            continue
        present = key in current
        contract, source = _type_contract(declared, current.get(key), present=present)
        labels = dict(declared.get("x-enum-labels") or {})
        fields.append(
            {
                "key": key,
                "label": key,
                "type": contract,
                "typeSource": source,
                "semantic": "param",
                "declared": True,
                "required": key in required,
                "set": present,
                "value": current.get(key) if present else None,
                "valueText": _json_ready(current[key]) if present else "",
                "default": declared.get("default"),
                "hasDefault": "default" in declared,
                "enum": [
                    {"value": option, "label": str(labels.get(option, option))}
                    for option in (declared.get("enum") or [])
                ],
                "description": str(declared.get("description") or ""),
                "multiline": bool(declared.get("x-multiline")),
                "group": group,
                "hidden": False,
            }
        )

    # 节点里有、manifest 没声明（或已声明为 hidden）的键 → 兜底组，仍然可改
    planned = {key for key, _ in plan}
    leftovers = [key for key in current if key not in planned]
    for key in leftovers:
        value = current[key]
        contract, source = _type_contract({}, value, present=True)
        fields.append(
            {
                "key": key,
                "label": key,
                "type": contract,
                "typeSource": source,
                "semantic": "param",
                "declared": key in properties,
                "required": False,
                "set": True,
                "value": value,
                "valueText": _json_ready(value),
                "default": None,
                "hasDefault": False,
                "enum": [],
                "description": "该参数不在命令声明里：类型按文件里的现有值推断，改动请谨慎",
                "multiline": False,
                "group": "未声明",
                "hidden": False,
            }
        )
    if leftovers:
        group_defs.append({"label": "未声明", "collapsed": False})

    return {"fields": fields, "groups": group_defs}


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
                # 用 JSON 而不是 Python 的 repr：卡片上出现 `['a', 'b']` 的单引号
                # 和旁边参数摘要的 JSON 风格不一致
                summary=f"{_compact(node.get('items'), 24)} as {node.get('item_var')}",
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
    described: dict[str, Any] = {"fields": [], "groups": []}
    if node and node.get("type") == "action":
        described = _field_descriptors(node.get("command", ""), node)
    return {
        "id": node_id,
        "node": node,
        "fields": described["fields"],
        "groups": described["groups"],
        "editable": bool(described["fields"]),
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
    """把参数表单的**全量**内容写成节点的 `with`。

    语义与 Qt `app.py` 的「应用参数」一致：`holder.raw["with"] = dict(values)` ——
    表单提交的是**整份参数**，不是补丁。空文本 = 未设置 = 该键不写进文件；布尔等于
    声明默认值同样不写（运行时缺省与显式设为默认等价，写进去只是噪声）。

    ★ 因此**必须**校验提交完整性：前端漏传一个键就成了「悄悄删掉一个参数」。
    提交键集合不覆盖「声明集合 ∪ 原有集合」时直接拒绝（`INCOMPLETE_SUBMIT`），
    宁可报错也不猜。

    顺序：SHA 闸门 → 定位 → 完整性校验 → 逐键转换 → 全量模型复校 → 编码比对 →
    落盘 → 编译。任一步不过就**不写盘**。
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
            "error": f"参数表单只改 action 节点，{node.get('type')} 节点请用结构表单",
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    manifest = _catalog_snapshot().get(node.get("command", ""))
    schema = dict(getattr(manifest, "input_schema", None) or {})
    properties = dict(schema.get("properties") or {})
    with_map = {k: v for k, v in dict(node.get("with") or {}).items() if not _is_reserved(k)}
    reserved = {k: v for k, v in dict(node.get("with") or {}).items() if _is_reserved(k)}

    editable = {
        key
        for key, declared in properties.items()
        if not any(dict(declared or {}).get(flag) for flag in _HIDDEN_FLAGS)
    }
    hidden_keys = {k for k in with_map if k in properties and k not in editable}
    # hidden 字段不给入口，所以也不能要求前端提交它——它的值由「原样保留」兜住。
    # 不做这一道，任何 with 里带着 flowDir 的节点都会永远保存不了（INCOMPLETE_SUBMIT）。
    expected = editable | (set(with_map) - hidden_keys)
    missing = sorted(expected - set(params))
    if missing:
        return {
            "ok": False,
            "code": "INCOMPLETE_SUBMIT",
            "error": f"提交缺少 {len(missing)} 个参数，拒绝按「未设置」处理（会静默丢参数）："
            + "、".join(missing[:6])
            + ("…" if len(missing) > 6 else ""),
            "missing": missing,
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    rejected: list[dict[str, str]] = []
    next_with: dict[str, Any] = {}
    cleared: list[str] = []
    for key, text in params.items():
        if key not in expected:
            rejected.append({"key": key, "reason": "既不在命令声明里、也不在节点现有参数里"})
            continue
        declared = dict(properties.get(key) or {})
        contract, source = _type_contract(declared, with_map.get(key), present=key in with_map)
        text = "" if text is None else str(text)
        if text == "":
            if key in with_map:
                cleared.append(key)
            continue  # 未设置 ⇒ 不写进 with
        try:
            value = _coerce(contract, text)
        except ValueError as exc:
            rejected.append({"key": key, "reason": str(exc)})
            continue
        if contract == "boolean" and declared.get("default") is value:
            if key in with_map:
                cleared.append(key)
            continue  # 与声明默认值相同 ⇒ 视为未设置
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
        next_with[key] = value
    if rejected:
        return {
            "ok": False,
            "code": "INVALID_VALUE",
            "error": "参数不合法，未写盘：" + "；".join(f"{r['key']} — {r['reason']}" for r in rejected),
            "rejected": rejected,
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    node["with"] = {**reserved, **{k: with_map[k] for k in hidden_keys}, **next_with}
    added = sorted(set(next_with) - set(with_map))
    removed = sorted(set(with_map) - set(next_with))
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
        "added": added,
        "removed": removed,
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


# ---------------------------------------------------------------------------
# 结构节点（if / forEach / try / return）：直接编辑 AST 结构字段
#
# 语义对齐 Qt 的 `ControlNodeForm`：字面量字段走 `parse_literal`（`${...}` 原样保留，
# 数字 / `[ { t f n " -` 开头尝试 JSON，失败回退原字符串）；JSON 字段留空 = 缺省；
# 变量名字段必须是标识符，不合法就拒绝而不是静默保留旧值。
# ---------------------------------------------------------------------------

_LITERAL_HEADS = '[{tfn"-'
# 与 rpa_core.model.workflow 的标识符约束同口径
NAME_PATTERN = re.compile(r"^[A-Za-z_]\w*$")


def _op_options() -> list[dict[str, str]]:
    """条件操作符取 `Condition.op` 的 Literal 实参——枚举值不在这里手抄一份。"""
    labels = load_op_labels()
    options = get_args(Condition.model_fields["op"].annotation)
    return [{"value": op, "label": labels.get(op, op)} for op in options]


def _parse_literal(text: str) -> Any:
    raw = text.strip()
    if raw == "":
        return ""
    if raw.startswith("${"):
        return raw
    if raw[0].isdigit() or raw[0] in _LITERAL_HEADS:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _format_literal(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


def _sf(
    key: str,
    label: str,
    semantic: str,
    value: Any,
    *,
    enum: list[dict[str, str]] | None = None,
    required: bool = False,
    description: str = "",
) -> dict[str, Any]:
    """造一个结构字段。形状与指令参数字段一致，前端才能复用同一个渲染器。"""
    if semantic == "json":
        text = "" if value is None else json.dumps(value, ensure_ascii=False)
        ctype = "json"
    else:
        text = _format_literal(value)
        ctype = "string"
    return {
        "key": key,
        "label": label,
        "type": ctype,
        "typeSource": "ast",
        "declared": True,
        "required": required,
        "set": value is not None and text != "",
        "value": value,
        "valueText": text,
        "default": None,
        "hasDefault": False,
        "enum": enum or [],
        "description": description,
        "multiline": semantic == "json",
        "group": None,
        "hidden": False,
        "semantic": semantic,
    }


def _structural_fields(node: dict[str, Any]) -> list[dict[str, Any]]:
    kind = node.get("type")
    if kind == "if":
        cond = dict(node.get("condition") or {})
        return [
            _sf("op", "条件操作符", "enum", cond.get("op"), enum=_op_options(), required=True),
            _sf("left", "左值", "literal", cond.get("left"), required=True,
                description="`${steps.x.outputs.y}` 或字面量"),
            _sf("right", "右值", "literal", cond.get("right"),
                description="引用 `${...}` 或 JSON 字面量；留空则不设右值（op=truthy 时本就该留空）"),
        ]
    if kind == "forEach":
        return [
            _sf("items", "遍历对象", "json", node.get("items"), required=True,
                description='JSON 数组；整体也可写带引号的引用，如 "${inputs.rows}"'),
            _sf("item_var", "循环变量名", "name", node.get("item_var") or "item", required=True,
                description="循环体内用 ${loop.<变量名>} 引用当前项，${loop.index} 是下标"),
        ]
    if kind == "try":
        return [_sf("error_var", "错误变量名", "name", node.get("error_var") or "error", required=True)]
    if kind == "return":
        return [
            _sf("value", "返回值", "json", node.get("value"),
                description='JSON 值，如 "ok"；留空返回 null。引用 `${...}` 需带引号书写'),
        ]
    return []


@api_method("rpa.structuralDetail")
def structural_detail(path: str, node_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    entry = _flows.get(path)
    if entry is None:
        open_flow(path)
        entry = _flows[path]
    node = entry["nodes"].get(node_id)
    if node is None:
        raise ValueError(f"节点不存在：{node_id}")
    fields = _structural_fields(node)
    return {
        "id": node_id,
        "kind": node.get("type"),
        "fields": fields,
        "groups": [],
        "editable": bool(fields),
        "rpcMs": round((time.perf_counter() - started) * 1000, 2),
    }


@api_method("rpa.saveStructuredParams")
def save_structured_params(path: str, node_id: str, params: dict[str, str]) -> dict[str, Any]:
    """改一个结构节点的 AST 字段并落盘。与 `saveNodeParams` 共用 SHA 闸门与编码口径。"""
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

    kind = node.get("type")
    expected = {f["key"] for f in _structural_fields(node)}
    if not expected:
        return {
            "ok": False,
            "code": "NODE_NOT_EDITABLE",
            "error": f"{kind} 节点没有可编辑的结构字段",
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
    missing = sorted(expected - set(params))
    if missing:
        return {
            "ok": False,
            "code": "INCOMPLETE_SUBMIT",
            "error": f"提交缺少 {len(missing)} 个结构字段：{'、'.join(missing)}",
            "missing": missing,
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    rejected: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    text_of = {k: ("" if v is None else str(v)) for k, v in params.items()}

    def need_name(key: str, label: str) -> str | None:
        value = text_of[key].strip()
        if not NAME_PATTERN.match(value):
            rejected.append({"key": key, "reason": f"{label}不是合法标识符：{value or '（空）'}"})
            return None
        return value

    def need_json(key: str, label: str, empty: Any) -> Any:
        text = text_of[key].strip()
        if not text:
            return empty
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            rejected.append({"key": key, "reason": f"{label}不是合法 JSON：{exc}"})
            return empty

    if kind == "if":
        condition: dict[str, Any] = {}
        op = text_of["op"].strip() or "truthy"
        valid_ops = {o["value"] for o in _op_options()}
        if op not in valid_ops:
            rejected.append({"key": "op", "reason": f"不认识的条件操作符：{op}"})
        condition["op"] = op
        condition["left"] = _parse_literal(text_of["left"])
        right = text_of["right"].strip()
        if right:
            condition["right"] = _parse_literal(right)
        if not rejected:
            node["condition"] = condition
    elif kind == "forEach":
        item_var = need_name("item_var", "循环变量名")
        items = need_json("items", "遍历对象", [])
        # ★ 不硬拦「不是数组」：`items: "${rows}"` 是合法写法（`resolve()` 会把纯引用
        # 求值成实际列表），拦下来等于禁掉整个引用写法。编译期也只查引用可解析性、
        # 不查类型，所以这里给 warning 而不是 rejected——但必须说出来，
        # 否则就是「编译通过、运行期 INVALID_INPUT」。
        if not rejected and not isinstance(items, (list, str)):
            warnings.append(
                {
                    "key": "items",
                    "reason": f"遍历对象是 {type(items).__name__}，运行期要求 list/tuple"
                    '（引用写法需带引号，如 "${inputs.rows}"）',
                }
            )
        if not rejected:
            node["items"] = items
            node["item_var"] = item_var
    elif kind == "try":
        error_var = need_name("error_var", "错误变量名")
        if not rejected:
            node["error_var"] = error_var
    elif kind == "return":
        value = need_json("value", "返回值", None)
        if not rejected:
            node["value"] = value

    if rejected:
        return {
            "ok": False,
            "code": "INVALID_VALUE",
            "error": "结构字段不合法，未写盘："
            + "；".join(f"{r['key']} — {r['reason']}" for r in rejected),
            "rejected": rejected,
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    try:
        Workflow.model_validate(document)
    except Exception as exc:  # noqa: BLE001 - 校验失败按「不写盘」处理
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
        "added": [],
        "removed": [],
        "warnings": warnings,
        "compile": compile_result,
        "flow": flow,
    }
