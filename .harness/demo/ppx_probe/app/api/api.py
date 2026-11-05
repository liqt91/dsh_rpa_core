"""PPX 原型后端：把 rpa_core 的真实能力层挂到 PPX 的 RPC 上。

职责——读 catalog 快照、把 workflow AST 摊平成卡片、给节点算出可编辑字段、
保存一个节点的参数（走与 Qt 完全一致的编码口径）、跑一次编译校验、
把「运行」spawn 成子进程并按行读事件流。
它**不承载** run（ADR 0011：编辑进程不承载运行），也不复制任何能力层逻辑：
运行 = `subprocess` 起 `rpa-core run`，进度来自 `events.jsonl`，结论来自 `result.json`。

★ 保存**不走 `model_dump`**：实测 8 个样例流程里 0 个无损——`model_dump` 会把
`elementRefs`/`output_aliases`/`retry_*`/`_exprModes` 等默认值全灌进文件。
所以采用「在原始 dict 上定点改一个键」+ `Workflow.model_validate` 复校 + Qt 同款编码。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, get_args

from ppx_py import api_method


def _find_repo_root() -> Path | None:
    """源码树形态的仓库根（向上找 src/rpa_core）；冻结态（PyInstaller 打出的 exe）没有。

    有这个根就保持原有开发态行为（工作区 = 仓库）；没有就走「工作区 + 包内 commands」
    —— 这条是让窗口能脱离仓库运行的前提，不然打包出来的 exe 一启动就找不到 rpa_core。
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "src" / "rpa_core").is_dir():
            return parent
    return None


REPO = _find_repo_root()
if REPO is not None and str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

def _frozen_resources() -> Path | None:
    """冻结态（PyInstaller）里业务资源的落地目录 `_MEIPASS/resources`；开发态 None。

    ppx 的 spec 只收三样 data：前端产物、ppx.toml、`api/resources`。所以 rpa_core 的
    数据文件（commands 目录、i18n.js）一律由构建脚本复制进 `api/resources`，
    运行时从这里读——包内的 `rpa_core/commands` 在冻结态是不存在的。
    """
    base = getattr(sys, "_MEIPASS", None)
    return (Path(base) / "resources") if base else None


FROZEN_RES = _frozen_resources()
if FROZEN_RES is not None:
    # i18n.js 的路径在 import 时就定死了，必须赶在 import rpa_core 之前指过去。
    os.environ.setdefault("RPA_I18N_JS", str(FROZEN_RES / "i18n.js"))

import rpa_core  # noqa: E402
from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler  # noqa: E402
from rpa_core.gui.i18n import load_command_display_names, load_op_labels  # noqa: E402
from rpa_core.model.workflow import Condition, Workflow  # noqa: E402

def _caps_dir() -> Path:
    """能力目录（每个命令一个 json）的三条来路，按形态选：

    - 冻结态：构建脚本复制进 `_MEIPASS/resources/commands` 的那份；
    - 源码树：仓库根 `commands/`（pyproject 的 force-include 只在打 wheel 时才把它
      映射成包内 `rpa_core/commands`，源码树里没有这一层）；
    - pip 形态：wheel 里的包内 `rpa_core/commands`。
    """
    if FROZEN_RES is not None:
        return FROZEN_RES / "commands"
    if REPO is not None:
        return REPO / "commands"
    return Path(rpa_core.__file__).resolve().parent / "commands"


CAPS = _caps_dir()


def _launcher() -> dict[str, str]:
    """窗口外部的运行时清单：安装器写 `~/.rpa-core/editor.json`（也可用 env 覆盖）。

    键：`rpa-core`（可执行文件）、`python`（解释器）、`workspace`（工作区根）、
    `editor`（窗口自己的 exe，只给 studio.py 用）。
    冻结态**只能**靠它找到 rpa-core —— 窗口包里没有 Python，sys.executable 是
    `RPA Probe.exe` 自己，拿去跑 `-m rpa_core.cli` 会变成「再开一个窗口」。

    定义在这里而不是用到的地方：`_workspace_root()` 在模块顶层就要读它。
    """
    data: dict[str, str] = {}
    # 注意不是 RPA_CORE_HOME：那个名字在技能侧（scripts/_runtime.py）表示「源码根」。
    # 一个变量两种含义会互相打脸。三处必须对齐：这里、bin/rpa-skill.mjs 的 coreHome()、
    # studio.py 的 _core_home()。
    home = Path(os.environ.get("RPA_HOME") or (Path.home() / ".rpa-core"))
    explicit = os.environ.get("RPA_EDITOR_MANIFEST")
    for path in ([Path(explicit)] if explicit else []) + [home / "editor.json"]:
        try:
            data.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    for env_key, key in (
        ("RPA_CORE_EXE", "rpa-core"),
        ("RPA_CORE_PYTHON", "python"),
        ("RPA_WORKSPACE", "workspace"),
    ):
        if os.environ.get(env_key):
            data[key] = os.environ[env_key]
    return data


def _workspace_root() -> Path:
    """工作区根：放 workflows/ 与 _sandbox/，也是界面上「流程路径」的相对基准。

    优先级：`RPA_WORKSPACE` → 源码树仓库根（开发态行为不变）→ 安装器写进
    `editor.json` 的 `workspace` → `~/.rpa-core/workspace`。
    冻结态没有仓库，写包内目录也写不进去（_MEIPASS 是临时的），所以必须落到用户目录。
    """
    explicit = os.environ.get("RPA_WORKSPACE")
    if explicit:
        return Path(explicit).expanduser()
    if REPO is not None:
        return REPO
    recorded = _launcher().get("workspace")
    if recorded:
        return Path(recorded).expanduser()
    home = Path(os.environ.get("RPA_HOME") or (Path.home() / ".rpa-core"))
    return home / "workspace"


WORKSPACE = _workspace_root()

_BOOT = time.perf_counter()
_catalog = None
_labels: dict[str, str] | None = None
_flows: dict[str, dict[str, Any]] = {}
# 保存试验台：复制一份样例进来改，绝不碰 examples/ 与 workflows/ 里的既有流程。
# 放工作区里而不是包内：_MEIPASS 每次启动都是新的临时目录，存那儿等于存到垃圾桶。
SANDBOX = WORKSPACE / "_sandbox"


def _catalog_snapshot():
    global _catalog
    if _catalog is None:
        _catalog = load_catalog(CAPS)
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
    # rel 是界面拿到的「相对工作区」路径（listFlows 里 relative_to(WORKSPACE) 的同一套），
    # 不是相对仓库 —— 冻结态没有仓库，拼错就变成「流程文件不存在」。
    target = (WORKSPACE / rel).resolve()
    # 越界守卫：界面上给的路径不许跑到工作区外面去（冻结态下这个根就是用户目录）。
    if not target.is_relative_to(WORKSPACE.resolve()):
        raise ValueError(f"流程路径越界: {rel}")
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
    # 一律相对 **WORKSPACE**：开发态它就是仓库根（行为与以前一致），冻结态是用户目录。
    # 以前这里写死 REPO/examples|workflows，打包后这两个目录不在包里 ⇒ 界面流程列表永远空。
    for base in (WORKSPACE / "examples", WORKSPACE / "workflows", SANDBOX):
        if not base.is_dir():
            continue
        if base == SANDBOX:
            for wf in sorted(base.glob("*.json")):
                flows.append(
                    {
                        "path": wf.relative_to(WORKSPACE).as_posix(),
                        "label": f"sandbox/{wf.stem}",
                    }
                )
            continue
        for wf in sorted(base.glob("*/workflow.json")):
            flows.append(
                {
                    "path": wf.relative_to(WORKSPACE).as_posix(),
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
        # 原型进程与「跑流程的子进程」是两个解释器，界面上必须能看出这一点。
        # 冻结态没有 python，只能走 rpa-core 可执行文件（见 _rpa_argv）。
        "runnerPython": str(_runner_python() or "未配置（用 rpa-core 可执行文件）"),
        "runsRoot": str(RUNS),
        "workspace": str(WORKSPACE),
        # 保留旧键：外部脚本按 `repo` 认工作区，冻结态它为空（没有仓库）。
        "repo": str(REPO) if REPO is not None else None,
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
# 插入 / 删除节点
#
# 落点语义对齐 Qt `FlowTreeModel.insert_command`：**插到 anchor 所属分支的末尾**，
# 不是「紧接 anchor 之后」。anchor 本身是容器就进它（if 进 then），是叶子就进
# 它的父容器、沿用父容器里那条分支。Qt 用「否则」虚拟行的行号来表达分支，
# AST 里 then/else 本来就是两个列表，直接按列表分即可。
# ---------------------------------------------------------------------------

_CONTAINERS = ("sequence", "forEach", "try", "if")
# 容器里"新节点默认进哪条分支"
_DEFAULT_BRANCH = {"sequence": "children", "forEach": "children", "try": "children", "if": "then"}


def _locate(
    node: dict[str, Any],
    node_id: str,
    parent: dict[str, Any] | None = None,
    key: str | None = None,
    index: int | None = None,
) -> tuple[dict[str, Any] | None, str | None, int | None, dict[str, Any]] | None:
    """返回 (parent, 分支键, 下标, 节点本身)；找不到返回 None。root 的 parent 为 None。"""
    if not isinstance(node, dict):
        return None
    if node.get("id") == node_id:
        return parent, key, index, node
    for branch in _NESTED:
        for i, child in enumerate(node.get(branch) or []):
            found = _locate(child, node_id, node, branch, i)
            if found is not None:
                return found
    return None


def _collect_ids(node: dict[str, Any], out: list[str]) -> None:
    if not isinstance(node, dict):
        return
    if node.get("id"):
        out.append(node["id"])
    for branch in _NESTED:
        for child in node.get(branch) or []:
            _collect_ids(child, out)


def _allocate_id(document: dict[str, Any]) -> str:
    """`n1` / `n2`…跳过已占用——与 Qt `allocate_node_id` 同一个命名习惯。"""
    used: list[str] = []
    _collect_ids(document.get("root") or {}, used)
    used_set = set(used)
    counter = 1
    while f"n{counter}" in used_set:
        counter += 1
    return f"n{counter}"


def _insert_plan(
    document: dict[str, Any], anchor_id: str
) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    """算出「插到哪个 dict 的哪个列表里」。

    返回 (目标容器, 分支键, 锚点节点)。第三个值只用于回报位置：真正的落点由
    「分支末尾」决定，锚点本身不参与定位（这正是与「紧跟锚点之后」的区别）。
    """
    root = document.get("root") or {}
    if not anchor_id:
        if root.get("type") not in _CONTAINERS:
            raise ValueError(f"根节点是 {root.get('type')}，不是一个可以追加子节点的容器")
        return root, _DEFAULT_BRANCH[root["type"]], None

    located = _locate(root, anchor_id)
    if located is None:
        raise ValueError(f"锚点节点不存在：{anchor_id}")
    parent, key, _index, anchor = located
    kind = anchor.get("type")
    if kind in _CONTAINERS:
        return anchor, _DEFAULT_BRANCH[kind], anchor
    if parent is None:
        raise ValueError("锚点是根节点且不是容器，无处可插")
    return parent, key or "children", anchor


@api_method("rpa.insertNode")
def insert_node(path: str, command: str, anchor_id: str = "") -> dict[str, Any]:
    """按命令插一个新 action 节点（`with` 为空，与 Qt `create_action_item` 一致）。"""
    started = time.perf_counter()
    target = _flow_path(path)
    document, raw = _read_document(target)

    entry = _flows.get(path)
    if entry is not None and entry["sha"] != _sha256(raw):
        return {
            "ok": False,
            "code": "FLOW_CHANGED",
            "error": "文件在打开后被其他程序改过，已拒绝覆盖（重新打开流程再改）",
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
    if command not in _catalog_snapshot():
        return {
            "ok": False,
            "code": "UNKNOWN_COMMAND",
            "error": f"目录里没有这个命令：{command}",
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    try:
        container, branch, anchor = _insert_plan(document, anchor_id)
    except ValueError as exc:
        return {
            "ok": False,
            "code": "INVALID_ANCHOR",
            "error": str(exc),
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    node_id = _allocate_id(document)
    new_node = {"type": "action", "id": node_id, "command": command, "with": {}}
    bucket = container.setdefault(branch, [])
    bucket.append(new_node)

    try:
        Workflow.model_validate(document)
    except Exception as exc:  # noqa: BLE001 - 校验失败按「不写盘」处理
        return {
            "ok": False,
            "code": "INVALID_WORKFLOW",
            "error": f"插入后结构不合法，未写盘：{type(exc).__name__}: {exc}",
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    encoded = _encode_document(document)
    target.write_bytes(encoded)
    compile_result = _compile(document)
    flow = open_flow(path)
    return {
        "ok": True,
        "newNodeId": node_id,
        "placed": {
            "parent": container.get("id") or "root",
            "branch": branch,
            "index": len(bucket) - 1,
            "anchor": anchor.get("id") if anchor else None,
        },
        "bytes": [len(raw), len(encoded)],
        "sha": _sha256(encoded)[:12],
        "ms": round((time.perf_counter() - started) * 1000, 1),
        "compile": compile_result,
        "flow": flow,
    }


@api_method("rpa.deleteNode")
def delete_node(path: str, node_id: str) -> dict[str, Any]:
    """删一个节点连同它的整棵子树。根节点不可删。"""
    started = time.perf_counter()
    target = _flow_path(path)
    document, raw = _read_document(target)

    entry = _flows.get(path)
    if entry is not None and entry["sha"] != _sha256(raw):
        return {
            "ok": False,
            "code": "FLOW_CHANGED",
            "error": "文件在打开后被其他程序改过，已拒绝覆盖（重新打开流程再改）",
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    located = _locate(document.get("root") or {}, node_id)
    if located is None:
        return {
            "ok": False,
            "code": "NODE_NOT_FOUND",
            "error": f"节点不存在：{node_id}",
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
    parent, branch, index, node = located
    if parent is None or branch is None or index is None:
        return {
            "ok": False,
            "code": "ROOT_NOT_DELETABLE",
            "error": "根节点不能删（流程至少要有一个 root）",
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    doomed: list[str] = []
    _collect_ids(node, doomed)
    parent[branch].pop(index)

    try:
        Workflow.model_validate(document)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "code": "INVALID_WORKFLOW",
            "error": f"删除后结构不合法，未写盘：{type(exc).__name__}: {exc}",
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    encoded = _encode_document(document)
    target.write_bytes(encoded)
    compile_result = _compile(document)
    flow = open_flow(path)
    return {
        "ok": True,
        "removed": {"id": node_id, "type": node.get("type"), "ids": doomed},
        "bytes": [len(raw), len(encoded)],
        "sha": _sha256(encoded)[:12],
        "ms": round((time.perf_counter() - started) * 1000, 1),
        "compile": compile_result,
        "flow": flow,
    }


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


# ---------------------------------------------------------------------------
# 运行（M18 口径 / ADR 0011）：本进程只 spawn + 读文件，执行全在子进程里
#
# 三件事必须在这层落地，界面才可能「看见运行」：
#   1. 早期 run_id —— CLI 启动后立刻打一行 {"run_id": ...}，据此在运行中就能
#      定位 `artifacts/<uuid>/events.jsonl`（否则只能等进程退出读末尾 RunResult）；
#   2. 增量事件 —— 每次都重读整个文件再按 since 切片，所以界面只追加、不重绘；
#   3. 收口判据 —— `proc.poll()` 结束 + `result.json` 落地才算完；两者不一致时
#      （非零退出且没有 result）说明是**启动即失败**，原因只在 stderr 里。
# ---------------------------------------------------------------------------

RUNS = SANDBOX / "runs"
_runs: dict[str, dict[str, Any]] = {}
_RUN_ID_RE = re.compile(r"^run-(\d+)$")


def _runner_python() -> Path | None:
    """跑流程用的解释器：源码树优先仓库 venv，其余形态读安装器清单。

    原型进程活在 ppx 的 venv 里（只装 pydantic/jsonschema），`rpa-core run` 却要
    真的拉起执行器。共用一个解释器的话，执行器哪天多一个依赖，错会攒到「点运行」
    那一刻才炸。所以按「谁更可能是完整运行时」排序。

    冻结态找不到就返回 None —— 窗口自己（`RPA Probe.exe`）**不是** python，
    拿它去跑 `-m rpa_core.cli` 等于再开一个窗口（这个错误第一次就踩到了）。
    """
    for folder, name in (("Scripts", "python.exe"), ("bin", "python")):
        if REPO is not None:
            candidate = REPO / ".venv" / folder / name
            if candidate.is_file():
                return candidate
    recorded = _launcher().get("python")
    if recorded and Path(recorded).is_file():
        return Path(recorded)
    if getattr(sys, "_MEIPASS", None):
        return None
    return Path(sys.executable)


def _rpa_argv() -> list[str]:
    """调 rpa-core 的 argv 前缀：有独立可执行文件就直接用，否则退回 `python -m`。

    冻结态必须走 exe：窗口自己的解释器是 PyInstaller 引导器，不是 python。
    """
    exe = _launcher().get("rpa-core")
    if exe and Path(exe).is_file():
        return [exe]
    python = _runner_python()
    if python is None:
        raise RuntimeError(
            "找不到 rpa-core：设 RPA_CORE_EXE，或在 ~/.rpa-core/editor.json 里写 "
            '{"rpa-core": "<rpa-core.exe 路径>"}（窗口包不带 Python，不能自己跑）'
        )
    return [str(python), "-m", "rpa_core.cli"]


def _run_env() -> dict[str, str]:
    env = dict(os.environ)
    if REPO is not None:
        source = str(REPO / "src")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = source + (os.pathsep + existing if existing else "")
    env["PYTHONIOENCODING"] = "utf-8"  # 子进程 stdout 里是 JSON，别让 cp936 插手
    env["PYTHONUNBUFFERED"] = "1"  # 早期 run_id 那一行必须马上出来
    return env


def _pump(stream: Any, sink: list[str], entry: dict[str, Any]) -> None:
    """持续读一条管道。不读会写满缓冲把子进程堵死（stderr 尤其）。"""
    try:
        for line in stream:
            text = line.rstrip("\n")
            sink.append(text)
            if entry.get("realRunId") is None and '"run_id"' in text:
                try:
                    payload = json.loads(text.strip())
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict) and isinstance(payload.get("run_id"), str):
                    entry["realRunId"] = payload["run_id"]
    finally:
        stream.close()


def _next_run_id() -> str:
    """`run-<n>`：进程内自增，跨进程重启靠扫目录续号（不重复用旧目录）。"""
    highest = 0
    if RUNS.is_dir():
        for child in RUNS.iterdir():
            match = _RUN_ID_RE.match(child.name)
            if match:
                highest = max(highest, int(match.group(1)))
    for entry in _runs.values():
        match = _RUN_ID_RE.match(entry["runId"])
        if match:
            highest = max(highest, int(match.group(1)))
    return f"run-{highest + 1}"


def _events_of(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """读全量事件。**坏行跳过**：写入方可能正在追加最后一行。"""
    real = entry.get("realRunId")
    if not real:
        return []
    path = entry["artifacts"] / real / "events.jsonl"
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            events.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return events


def _result_of(entry: dict[str, Any]) -> dict[str, Any] | None:
    real = entry.get("realRunId")
    if not real:
        return None
    path = entry["artifacts"] / real / "result.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:  # 正在写，下一轮再读
        return None


def _preview(value: Any, limit: int = 200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"…（共 {len(text)} 字符）"


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    """**摘要**而不是整份 RunResult：一步 `readText` 就能把 outputs 撑到几百 KB，
    整份塞给 WebView 会让轮询变成卡顿。事件流里的 outputs 仍是原样（那是进度）。"""
    outputs: dict[str, Any] = {}
    for node_id, step in (result.get("outputs") or {}).items():
        step = step or {}
        outputs[node_id] = {
            "keys": sorted((step.get("outputs") or {}).keys()),
            "effects": len(step.get("effects") or []),
        }
    return {
        "status": result.get("status"),
        "workflowId": result.get("workflow_id"),
        "runIdReal": result.get("run_id"),
        "startedAt": result.get("started_at"),
        "endedAt": result.get("ended_at"),
        "returnValue": _preview(result.get("return_value")),
        "error": result.get("error"),
        "outputs": outputs,
    }


def _startup_error(entry: dict[str, Any]) -> dict[str, Any]:
    """没有 result 就非零退出 = 编译/加载/校验失败，原因在 stderr 的结构化行里。"""
    lines = [line for line in entry["stderr"] if line.strip()]
    message = None
    code = None
    used = -1
    for index in range(len(lines) - 1, -1, -1):
        text = lines[index].strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("message"):
            message = str(payload["message"])
            code = payload.get("error")
            used = index
            break
    # 那一行已经被提成 message 了，别再原样贴一遍（界面上就是同一句话说两次）
    tail = [line for index, line in enumerate(lines) if index != used][-8:]
    return {"code": code, "message": message, "tail": tail}


def _purge_runs() -> None:
    """进程退出时收掉还在跑的子进程：不留孤儿浏览器/worker。"""
    for entry in list(_runs.values()):
        proc = entry["proc"]
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001 - 退出路径上不再抛
                pass


atexit.register(_purge_runs)


@api_method("rpa.startRun")
def start_run(path: str) -> dict[str, Any]:
    """起一个 `rpa-core run` 子进程。同一时刻只允许一个（点两次会起两个浏览器）。"""
    started = time.perf_counter()
    busy = [entry["runId"] for entry in _runs.values() if entry["proc"].poll() is None]
    if busy:
        return {
            "ok": False,
            "code": "RUN_ACTIVE",
            "error": f"已有运行中的任务：{busy[0]}（先停止它）",
            "runId": busy[0],
        }
    try:
        target = _flow_path(path)
    except ValueError as exc:
        return {"ok": False, "code": "FLOW_NOT_FOUND", "error": str(exc)}

    run_id = _next_run_id()
    root = RUNS / run_id
    artifacts = root / "artifacts"
    work = root / "work"
    artifacts.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    python = _runner_python()
    argv = [
        # 有独立的 rpa-core 可执行文件就用它（冻结态唯一可行）；否则 python -m。
        *_rpa_argv(),
        "run",
        str(target),
        "--artifacts",
        str(artifacts),
    ]
    entry: dict[str, Any] = {
        "runId": run_id,
        "path": path,
        "proc": None,
        "artifacts": artifacts,
        "work": work,
        "stdout": [],
        "stderr": [],
        "realRunId": None,
        "cancelled": False,
        # ★ 两个钟不能混用：perf_counter 算时长，time.time 只用于展示。
        #   混用会算出负数（第一版就是这么错的，探针打出来 -1793149862780 ms）。
        "t0": time.perf_counter(),
        "startedAt": time.time(),
        "argv": argv,
    }
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(work),
            env=_run_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return {"ok": False, "code": "SPAWN_FAILED", "error": f"起不了子进程：{exc}"}
    entry["proc"] = proc
    threading.Thread(
        target=_pump, args=(proc.stdout, entry["stdout"], entry), daemon=True
    ).start()
    threading.Thread(
        target=_pump, args=(proc.stderr, entry["stderr"], entry), daemon=True
    ).start()
    _runs[run_id] = entry
    return {
        "ok": True,
        "runId": run_id,
        "pid": proc.pid,
        "path": path,
        "argv": argv[1:],
        "python": str(python),
        "artifacts": str(artifacts),
        # 子进程的 cwd 收在 run 目录下：样例里 `"workspace": ""` 会 resolve 成 cwd，
        # 用仓库根当 cwd 的话点一次运行就在仓库根写出 hello.txt。
        "cwd": str(work),
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


@api_method("rpa.runPoll")
def run_poll(run_id: str, since: int = 0) -> dict[str, Any]:
    """轮询：只回传 `since` 之后的事件（界面追加即可，不必重绘）。"""
    entry = _runs.get(run_id)
    if entry is None:
        return {"ok": False, "code": "UNKNOWN_RUN", "error": f"没有这个 run：{run_id}"}
    proc = entry["proc"]
    events = _events_of(entry)
    cursor = max(0, min(int(since or 0), len(events)))
    running = proc.poll() is None
    result = _result_of(entry)
    payload: dict[str, Any] = {
        "ok": True,
        "runId": run_id,
        "running": running,
        "exitCode": proc.poll(),
        "pid": proc.pid,
        "realRunId": entry.get("realRunId"),
        "artifacts": str(entry["artifacts"]),
        "cancelled": bool(entry["cancelled"]),
        "events": events[cursor:],
        "total": len(events),
        "elapsedMs": round((time.perf_counter() - entry["t0"]) * 1000, 1) if running else None,
        "startedAt": entry["startedAt"],
        "result": _result_summary(result) if result else None,
    }
    # 被用户停掉的 run 也会「没有 result 就非零退出」——那是我们干的，不是启动失败
    if result is None and not running and not entry["cancelled"] and proc.returncode not in (0, None):
        payload["startupError"] = _startup_error(entry)
    if not running:
        payload["stderrTail"] = [line for line in entry["stderr"] if line.strip()][-8:]
    return payload


@api_method("rpa.cancelRun")
def cancel_run(run_id: str) -> dict[str, Any]:
    """停止 = 终止子进程；orchestrator 在子进程内落 cancelled 证据（若走到那一步）。"""
    entry = _runs.get(run_id)
    if entry is None:
        return {"ok": False, "code": "UNKNOWN_RUN", "error": f"没有这个 run：{run_id}"}
    proc = entry["proc"]
    was_running = proc.poll() is None
    if was_running:
        entry["cancelled"] = True
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    return {
        "ok": True,
        "runId": run_id,
        "cancelled": was_running,
        "exitCode": proc.returncode,
        "events": len(_events_of(entry)),
    }


# ---------------------------------------------------------------------------
# 元素：缺失检测 / 资产列表 / 触发捕获（复用 main 的 `rpa-core capture` 命令）
# 「AI 生成的工作流」只把交互元素写成 elementRefs 占位（模型不知道真实选择器），
# 运行期解析不到即 ELEMENT_REF_MISSING；这组 RPC 把缺口摊开并接上 main 的捕获器。
# ---------------------------------------------------------------------------

_CAPTURES: dict[str, dict[str, Any]] = {}


def _elements_dir(path: str) -> Path:
    return _flow_path(path).parent / "elements"


def _present_elements(path: str) -> set[str]:
    ed = _elements_dir(path)
    return {f.stem for f in ed.glob("*.json")} if ed.is_dir() else set()


@api_method("rpa.missingElements")
def missing_elements(path: str) -> dict[str, Any]:
    """扫描 workflow 的 elementRefs：引用了、但 elements/ 下没有同名文件 = 待补元素。

    这正是「AI 生成的工作流」与「真正能跑的工作流」之间的缺口：生成时交互元素只能
    写成 elementRefs 占位（模型不知道真实选择器），运行期解析不到即 ELEMENT_REF_MISSING。
    这里把缺口摊开，前端据此提示用户「去捕获 / 提供」每个待补元素。
    """
    started = time.perf_counter()
    data, _ = _read_document(_flow_path(path))
    present = _present_elements(path)
    missing: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> None:
        if not isinstance(node, dict):
            return
        for param, name in (node.get("elementRefs") or {}).items():
            if name not in present:
                missing.append(
                    {
                        "nodeId": node.get("id"),
                        "command": node.get("command"),
                        "param": param,
                        "name": name,
                    }
                )
        for key in _NESTED:
            for child in node.get(key) or []:
                walk(child)

    walk(data.get("root"))
    return {
        "ok": True,
        "missing": missing,
        "resolved": sorted(present),
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


@api_method("rpa.listElements")
def list_elements(path: str) -> dict[str, Any]:
    """列出流程已捕获的元素资产（elements/*.json）。"""
    started = time.perf_counter()
    ed = _elements_dir(path)
    items: list[dict[str, Any]] = []
    if ed.is_dir():
        for f in sorted(ed.glob("*.json")):
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
                sel = doc.get("selector") or {}
                items.append(
                    {
                        "name": f.stem,
                        "kind": doc.get("kind"),
                        "selector": sel.get("css") or sel.get("locator"),
                    }
                )
            except Exception:
                items.append({"name": f.stem, "kind": None, "selector": None, "error": "解析失败"})
    return {
        "ok": True,
        "elements": items,
        "dir": str(ed),
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


@api_method("rpa.startCapture")
def start_capture(
    path: str,
    target: str,
    name: str,
    browserType: str = "edge",
) -> dict[str, Any]:
    """起一个 `rpa-core capture` 子进程，复用 main 的捕获器（扩展 / 桌面）。

    capture 是**阻塞到用户手势**的一次性会话（浏览器 Ctrl+Click / 桌面 F9），所以这里
    spawn 后立刻返回，前端用 `rpa.capturePoll` 轮询元素文件是否落盘——不要 await 它。
    元素文件落盘即用 `name` 命名（与节点 elementRefs 同名 = 自动绑定，引用模型无需单独绑定）。
    """
    started = time.perf_counter()
    flow_file = _flow_path(path)
    flow_dir = flow_file.parent.name
    # --workflows 取**流程目录的父目录**：流程可能在 examples/ 或 workflows/ 或沙箱里，
    # 写死 WORKSPACE/workflows 会让其它来源的流程捕获时找不到自己的目录。
    flows_root = flow_file.parent.parent
    python = _runner_python()
    argv = [
        *_rpa_argv(),
        "capture",
        target,
        "--save-as",
        name,
        "--flow",
        flow_dir,
        "--workflows",
        str(flows_root),
    ]
    if target == "browser" and browserType:
        argv += ["--browser-type", browserType]
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(WORKSPACE),
            env=_run_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return {"ok": False, "code": "SPAWN_FAILED", "error": f"起不了捕获子进程：{exc}"}
    entry: dict[str, Any] = {
        "name": name,
        "flow": flow_dir,
        "target": target,
        "proc": proc,
        "path": path,
        "stdout": [],
        "stderr": [],
        "t0": time.perf_counter(),
        "startedAt": time.time(),
        "argv": argv,
    }
    _CAPTURES[name] = entry
    threading.Thread(
        target=_pump, args=(proc.stdout, entry["stdout"], entry), daemon=True
    ).start()
    threading.Thread(
        target=_pump, args=(proc.stderr, entry["stderr"], entry), daemon=True
    ).start()
    return {
        "ok": True,
        "name": name,
        "pid": proc.pid,
        "argv": argv[1:],
        "python": str(python),
        "instructions": (
            "去浏览器 Ctrl+Click 目标元素（桌面按 F9）；抓完自动落盘并绑定"
            if target == "browser"
            else "把鼠标移到目标控件按 F9"
        ),
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


@api_method("rpa.capturePoll")
def capture_poll(name: str) -> dict[str, Any]:
    """轮询某个捕获会话：是否还在跑、元素文件是否落盘。"""
    entry = _CAPTURES.get(name)
    if entry is None:
        return {"ok": False, "code": "UNKNOWN_CAPTURE", "error": f"没有这个捕获会话：{name}"}
    proc = entry["proc"]
    alive = proc.poll() is None
    elem = _elements_dir(entry["path"]) / f"{name}.json"
    captured = elem.is_file()
    return {
        "ok": True,
        "name": name,
        "running": alive,
        "captured": captured,
        "path": str(elem) if captured else None,
        "exitCode": None if alive else proc.returncode,
    }


# ---------------------------------------------------------------------------
# 会话通道（WorkBuddy Agent 侧，协议见 workbuddy-suite 的 references/protocol.md）
# 「可选依赖」：Agent 没在跑、端口没起，编辑器照常能用；任何一次调用不超 1 秒。
# ---------------------------------------------------------------------------

_STUDIO_HOME = Path(
    os.environ.get("STUDIO_HOME") or (Path(os.environ.get("TEMP") or "/tmp") / "workbuddy-studio")
)
_STUDIO_PORT = int(os.environ.get("STUDIO_PORT") or 7788)
# ★ 关掉代理：Windows 上 http_proxy 常驻，urllib 会把 127.0.0.1 也交给代理。
_STUDIO_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _studio_endpoint() -> int:
    try:
        meta = json.loads((_STUDIO_HOME / "endpoint.json").read_text(encoding="utf-8"))
        return int(meta.get("port") or _STUDIO_PORT)
    except (OSError, ValueError, TypeError):
        return _STUDIO_PORT


def _studio_http(
    method: str,
    route: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 1.0,
) -> dict[str, Any] | None:
    port = _studio_endpoint()
    url = f"http://127.0.0.1:{port}{route}"
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("Origin", "http://127.0.0.1:5173")
    try:
        with _STUDIO_OPENER.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


@api_method("studio.pollSession")
def studio_poll_session(since: int = 0) -> dict[str, Any]:
    inbox = _studio_http("GET", f"/inbox?since={int(since)}&client=editor", timeout=1.0)
    if inbox is None:
        return {"ok": True, "online": False, "port": _studio_endpoint(), "cursor": since, "requests": []}
    return {
        "ok": True,
        "online": True,
        "port": inbox.get("port", _studio_endpoint()),
        "cursor": inbox.get("cursor", since),
        "requests": inbox.get("requests") or [],
    }


@api_method("studio.notify")
def studio_notify(kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = {"kind": kind, "at": time.time(), "by": "editor", **(payload or {})}
    result = _studio_http("POST", "/events", body)
    return {"ok": True, "notified": bool(result and result.get("ok"))}


# ---------------------------------------------------------------------------
# 诊断入口（冻结态没有控制台，窗口起不来时这是唯一能看到的现场）
#
#   set RPA_EDITOR_SELFTEST=1 && "RPA Probe.exe"
#
# 它跑一遍 bootstrap（catalog / 中文名 / 流程列表 / 运行器），把结果写成 JSON 到
# `RPA_EDITOR_SELFTEST` 指定的路径（不指定就写 stdout），然后**不启动窗口**退出。
# 冻结态最常见的三种崩法（缺数据、缺模块、路径对不上）都能在这一份输出里看出来。
# ---------------------------------------------------------------------------
def _selftest_payload() -> dict[str, Any]:
    import traceback

    payload: dict[str, Any] = {
        "frozen": bool(getattr(sys, "_MEIPASS", None)),
        "repo": str(REPO) if REPO is not None else None,
        "workspace": str(WORKSPACE),
        "caps": str(CAPS),
        "capsExists": CAPS.is_dir(),
        "resources": str(FROZEN_RES) if FROZEN_RES is not None else None,
        "i18n": os.environ.get("RPA_I18N_JS"),
    }
    try:
        info = bootstrap()
        payload["bootstrap"] = {
            "ok": True,
            "commands": len(info.get("commands") or []),
            "catalogDigest": info.get("catalogDigest"),
            "flows": [f.get("path") for f in info.get("flows") or []],
            "runnerPython": info.get("runnerPython"),
        }
        # 再走一遍「打开流程」：bootstrap 只证数据在，openFlow 才证卡片摊平 / 编译链路通。
        flows = [f.get("path") for f in info.get("flows") or []]
        if flows:
            try:
                flow = open_flow(flows[0])
                payload["openFlow"] = {
                    "ok": True,
                    "path": flows[0],
                    "cards": len(flow.get("cards") or []),
                    "sha": flow.get("sha"),
                }
            except Exception:
                payload["openFlow"] = {"ok": False, "traceback": traceback.format_exc()}
    except Exception:
        payload["bootstrap"] = {"ok": False, "traceback": traceback.format_exc()}
    return payload


if os.environ.get("RPA_EDITOR_SELFTEST"):
    import json as _json

    _target = os.environ.get("RPA_EDITOR_SELFTEST")
    _text = _json.dumps(_selftest_payload(), ensure_ascii=False, indent=2)
    if _target and _target not in ("1", "true", "stdout"):
        Path(_target).write_text(_text, encoding="utf-8")
    else:
        print(_text)
    os._exit(0)
