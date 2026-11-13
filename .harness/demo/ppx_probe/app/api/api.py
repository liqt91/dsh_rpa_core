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
import copy
import hashlib
import json
import logging
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

from ppx_py import BridgeError, api_method


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
from rpa_core.capture_kit import (  # noqa: E402
    CaptureController,
    CaptureFloat,
    ElementEditDialog,
)
from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.compiler.compiler import WorkflowCompileError, WorkflowCompiler  # noqa: E402
from rpa_core.gui.i18n import load_command_display_names, load_op_labels  # noqa: E402
from rpa_core.model.capture import ElementDescriptor  # noqa: E402
from rpa_core.model.inputs import (  # noqa: E402
    InputDeclarationError,
    InputEntry,
    declaration_from_entries,
)
from rpa_core.model.workflow import Condition, Workflow  # noqa: E402

# 捕获的会话 / 浮窗 / 「无超时」全在 rpa_core.capture_kit 里，ppx 只负责 RPC 与落盘。


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


def _install_bridge_logging() -> Path | None:
    """把桥层的异常日志落到用户目录 —— 冻结态没有控制台，traceback 不能丢。

    `ppx_py/bridge.py` 对任何 Python 异常只做一次 `LOGGER.exception(...)`，然后给 JS 回
    `INTERNAL_ERROR: Python API 执行失败`。exe 没有控制台、框架也没配 FileHandler，
    于是现场只剩「无日志的内部错误」。这里给那个 logger 单独挂滚动文件，不改 ppx 代码；
    幂等（同一个文件不重复挂），失败也不影响窗口启动。
    """
    from logging.handlers import RotatingFileHandler

    home = Path(os.environ.get("RPA_HOME") or (Path.home() / ".rpa-core"))
    path = Path(os.environ.get("RPA_EDITOR_LOG") or (home / "editor.log"))
    logger = logging.getLogger("ppx_py.bridge")
    for handler in logger.handlers:
        if (
            isinstance(handler, RotatingFileHandler)
            and Path(handler.baseFilename) == path.resolve()
        ):
            return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1 << 20, backupCount=2, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        return path
    except OSError:
        return None


WORKSPACE = _workspace_root()
EDITOR_LOG = _install_bridge_logging()
if EDITOR_LOG is not None:
    logging.getLogger("ppx_py.bridge").info(
        "editor start · workspace=%s · log=%s · frozen=%s",
        WORKSPACE,
        EDITOR_LOG,
        bool(getattr(sys, "_MEIPASS", None)),
    )

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


def _session_root() -> Path | None:
    """会话服务在 endpoint.json 里声明的 `--root`；没声明就是 None。

    会话侧 `check_path` 按这个根放行绝对路径，窗口侧原先只认自己的 `WORKSPACE`，
    于是同一条路径在桥里必然越界。把它列为第二个允许根，两边才共用一个真值源；
    窗口独立启动（不经过 `studio.py gui`）时这条是唯一的对齐路径。
    """
    home = Path(
        os.environ.get("STUDIO_HOME")
        or (Path(os.environ.get("TEMP") or "/tmp") / "workbuddy-studio")
    )
    try:
        meta = json.loads((home / "endpoint.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = meta.get("root")
    return Path(raw).expanduser().resolve() if raw else None


def _allowed_roots() -> list[Path]:
    roots = [WORKSPACE.resolve()]
    session = _session_root()
    if session is not None and session not in roots:
        roots.append(session)
    return roots


def _bad_path_message(path: str, roots: list[Path], why: str) -> str:
    expected = " 或 ".join(str(root) for root in roots)
    return (
        f"打开流程失败（{why}）：{path}\n"
        f"窗口认的根：{expected}\n"
        "把文件放进 <窗口根>/workflows/ 后从窗口下拉框选；或让会话服务用 "
        "studio.py serve --root <流程所在目录> / studio.py gui --root <流程所在目录> "
        "起，再重新 open。"
    )


def _flow_path(rel: str) -> Path:
    """把界面/会话给的 path 落到磁盘，并守住工作区边界。

    两种合法口径，共用同一组允许根：
    - 相对路径：相对 `WORKSPACE`（窗口下拉框与 listFlows 用的就是这套）；
    - 绝对路径：只要落在 `WORKSPACE` 或会话声明的 `--root` 内。
      `Path(root) / absolute` 在 pathlib 里直接返回 absolute，所以下面不必分叉。

    ★ 越界/不存在不能抛裸 ValueError：桥层会把任意异常吞成
    `INTERNAL_ERROR: Python API 执行失败`，Agent 只拿到一句没有原因的话。
    抛 BridgeError，桥才会把 `BAD_PATH` 和上面那段可照抄的修法原样带回去。
    """
    target = (WORKSPACE / rel).resolve()
    roots = _allowed_roots()
    if not any(target.is_relative_to(root) for root in roots):
        raise BridgeError("BAD_PATH", _bad_path_message(rel, roots, "路径越界"))
    if not target.is_file():
        raise BridgeError("BAD_PATH", _bad_path_message(rel, roots, "文件不存在"))
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
    # 新插入的 if 条件左值留空（不能编造 ${inputs.xxx}，编译期会查引用是否存在），
    # 卡片上要直说「待设置」，别显示成 " truthy" 这种像坏了的样子。
    if condition.get("left") in (None, ""):
        return "待设置条件"
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

    def add(
        self, parent_id: str = "", branch: str = "", index: int = 0, **card: Any
    ) -> None:
        """每张卡片都带上自己在 AST 里的位置，拖拽落点才不用前端去猜树形。"""
        if card.get("kind") != "group":
            self._seq += 1
            card.setdefault("no", self._seq)
        card.setdefault("parentId", parent_id)
        card.setdefault("branch", branch)
        card.setdefault("index", index)
        self.cards.append(card)

    def group(
        self,
        label: str,
        depth: int,
        parent_id: str = "",
        branch: str = "",
        index: int = 0,
    ) -> None:
        """虚拟组行同时是「往这条分支插节点」的锚点：前端选中它后把 parentId+branch 传回来。

        空分支也要出组行，否则用户插入一个 if/try 之后根本找不到「否则 / 异常」的落点。
        index 取该分支当前长度：拖到组行上一律插在这条分支的末尾。
        """
        self.add(
            parent_id,
            branch,
            index,
            kind="group",
            label=label,
            depth=depth,
            command="",
            summary="",
            id=f"{parent_id}::{branch}" if parent_id and branch else "",
            parentId=parent_id,
            # branch 已由 add 的位置参数 setdefault，这里不能再传同名关键字
        )

    def walk(
        self,
        node: dict[str, Any],
        depth: int,
        parent_id: str = "",
        branch: str = "",
        index: int = 0,
    ) -> None:
        """parent_id / branch / index 是这张卡片在 AST 中的落点，递归时逐层往下带。"""
        kind = node.get("type")
        node_id = node.get("id", "")

        def emit(**card: Any) -> None:
            self.add(parent_id, branch, index, **card)

        def children(key: str, child_depth: int) -> None:
            for i, child in enumerate(node.get(key) or []):
                self.walk(child, child_depth, node_id, key, i)

        if kind == "action":
            command = node.get("command", "")
            emit(
                kind="action",
                id=node_id,
                depth=depth,
                command=command,
                label=self.labels.get(command, command),
                summary=_summarize(node.get("with") or {}),
            )
        elif kind == "return":
            emit(kind="return", id=node_id, depth=depth, command="return", label="返回", summary="")
        elif kind == "sequence":
            children("children", depth)
        elif kind == "if":
            cond = node.get("condition") or {}
            emit(
                kind="if",
                id=node_id,
                depth=depth,
                command="if",
                label="如果",
                summary=_condition_summary(cond),
            )
            self.group("则执行", depth + 1, node_id, "then", len(node.get("then") or []))
            children("then", depth + 1)
            self.group("否则", depth + 1, node_id, "else", len(node.get("else") or []))
            children("else", depth + 1)
        elif kind == "forEach":
            emit(
                kind="forEach",
                id=node_id,
                depth=depth,
                command="forEach",
                label="循环每一项",
                # 用 JSON 而不是 Python 的 repr：卡片上出现 `['a', 'b']` 的单引号
                # 和旁边参数摘要的 JSON 风格不一致
                summary=f"{_compact(node.get('items'), 24)} as {node.get('item_var')}",
            )
            children("children", depth + 1)
        elif kind == "try":
            emit(kind="try", id=node_id, depth=depth, command="try", label="尝试", summary="")
            children("children", depth + 1)
            self.group("异常", depth + 1, node_id, "catch", len(node.get("catch") or []))
            children("catch", depth + 1)
        else:
            emit(
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


@api_method("rpa.saveFlowInputs")
def save_flow_inputs(path: str, values: dict[str, str]) -> dict[str, Any]:
    """全量替换 ``workflow.inputs``（流程级全局参数声明 / 运行参数默认值）。

    前端每行给「名称 → 默认值的 JSON 文本」，与 Qt ``RunParamsDialog`` 和
    ``rpa_core.model.inputs.InputEntry`` 同口径：``"abc"`` 得字符串、``3`` 得数字、
    ``[1,2]`` 得数组。名称格式、保留名、重复、默认值可解析**一律交给权威校验器**，
    这里不另写一套规则——否则编辑面与 compiler 的 ``validate_declaration`` 会分叉。

    顺序：SHA 闸门 → 权威声明校验 → 全量模型复校 → 编码比对 → 落盘 →
    编译（``${inputs.x}`` 是否还在声明里由 compiler 查）→ 重开流程刷新 ``_flows``。
    任一步不过就**不写盘**；提交的是**整份声明**，不是补丁（漏传 = 删除该参数）。
    """
    started = time.perf_counter()
    target = _flow_path(path)
    document, raw = _read_document(target)

    def reject(code: str, error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "code": code,
            "error": error,
            "saved": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    entry = _flows.get(path)
    if entry is not None and entry["sha"] != _sha256(raw):
        return reject("FLOW_CHANGED", "文件在打开后被其他程序改过，已拒绝覆盖（重新打开流程再改）")
    if not isinstance(values, dict):
        return reject("INVALID_SUBMIT", "values 必须是「名称 → JSON 文本」的对象")

    try:
        entries = [
            InputEntry(name=str(name), json_text="" if text is None else str(text))
            for name, text in values.items()
        ]
        declaration = declaration_from_entries(entries)
    except InputDeclarationError as exc:
        return reject("INVALID_DECLARATION", f"全局参数不合法，未写盘：{exc}")
    except Exception as exc:  # noqa: BLE001 - 前端传来的形状不可信，结论要带回去
        return reject(
            "INVALID_DECLARATION", f"全局参数不合法，未写盘：{type(exc).__name__}: {exc}"
        )

    previous = document.get("inputs") if isinstance(document.get("inputs"), dict) else {}
    document["inputs"] = declaration
    try:
        Workflow.model_validate(document)
    except Exception as exc:  # noqa: BLE001 - 校验失败按「不写盘」处理
        return reject("INVALID_WORKFLOW", f"改完结构不合法，未写盘：{type(exc).__name__}: {exc}")

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
        "inputs": declaration,
        "previous": previous,
        "added": sorted(set(declaration) - set(previous)),
        "removed": sorted(set(previous) - set(declaration)),
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
# 容器「允许哪些分支」：比 _NESTED 更严，拖拽落点先挡掉 if.children 这类非法形状，
# 比等到 Workflow.model_validate 才报错更能说清「哪儿放错了」。
_BRANCHES_BY_TYPE = {
    "sequence": ("children",),
    "forEach": ("children",),
    "try": ("children", "catch"),
    "if": ("then", "else"),
}


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
    document: dict[str, Any], anchor_id: str, branch: str = ""
) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    """算出「插到哪个 dict 的哪个列表里」。

    返回 (目标容器, 分支键, 锚点节点)。第三个值只用于回报位置：真正的落点由
    「分支末尾」决定，锚点本身不参与定位（这正是与「紧跟锚点之后」的区别）。

    branch 非空时来自画布上选中的虚拟组行（如「否则」「异常」）：anchor_id 是
    该组行的父容器节点 id，落点直接取它的这条分支。
    """
    root = document.get("root") or {}
    if branch:
        if branch not in _NESTED:
            raise ValueError(f"未知分支：{branch}")
        located = _locate(root, anchor_id) if anchor_id else None
        if located is None:
            raise ValueError(f"分支所属节点不存在：{anchor_id}")
        container = located[3]
        if container.get("type") not in _CONTAINERS:
            raise ValueError(f"{anchor_id} 不是容器，没有 {branch} 分支")
        return container, branch, container
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


def _explicit_container(
    document: dict[str, Any], parent_id: str, branch: str
) -> tuple[dict[str, Any], str]:
    """拖拽落点：按「父容器 id + 分支键」定位，不走 anchor 的分支末尾语义。

    parent_id 为空 = 落到 document.root（画布空白处）；分支留空取该容器默认分支。
    """
    root = document.get("root") or {}
    if parent_id:
        located = _locate(root, parent_id)
        if located is None:
            raise ValueError(f"目标容器不存在：{parent_id}")
        container = located[3]
    else:
        container = root
    kind = container.get("type")
    if kind not in _BRANCHES_BY_TYPE:
        raise ValueError(f"{parent_id or 'root'} 不是可以挂子节点的容器（{kind}）")
    key = branch or _DEFAULT_BRANCH[kind]
    if key not in _BRANCHES_BY_TYPE[kind]:
        raise ValueError(f"{kind} 没有 {key} 分支")
    return container, key


def _insert_at(bucket: list[Any], node: dict[str, Any], index: int) -> int:
    """按前端给的落点下标插入；-1 / 越界一律当「末尾」，与点击通道一致。"""
    if 0 <= index <= len(bucket):
        bucket.insert(index, node)
        return index
    bucket.append(node)
    return len(bucket) - 1


def _resolve_target(
    document: dict[str, Any],
    anchor_id: str,
    branch: str,
    parent_id: str,
    explicit: bool,
) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    """点击 = anchor 所属分支末尾；拖拽 = 显式 (parent_id, branch) 落点。"""
    if explicit:
        container, key = _explicit_container(document, parent_id, branch)
        return container, key, None
    return _insert_plan(document, anchor_id, branch)


@api_method("rpa.insertNode")
def insert_node(
    path: str,
    command: str,
    anchor_id: str = "",
    branch: str = "",
    parent_id: str = "",
    index: int = -1,
    explicit: bool = False,
) -> dict[str, Any]:
    """按命令插一个新 action 节点（`with` 为空，与 Qt `create_action_item` 一致）。

    branch 由前端选中「否则 / 异常」这类虚拟组行时给出，指定落到容器的哪条分支。
    explicit=True 是拖拽通道：落点由 (parent_id, branch, index) 决定，index 为
    「目标行的上半区 = 它的下标，下半区 = 下标 + 1」；点击通道保持旧语义不变。
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
        container, target_branch, anchor = _resolve_target(
            document, anchor_id, branch, parent_id, explicit
        )
    except ValueError as exc:
        return {
            "ok": False,
            "code": "INVALID_TARGET" if explicit else "INVALID_ANCHOR",
            "error": str(exc),
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    node_id = _allocate_id(document)
    new_node = {"type": "action", "id": node_id, "command": command, "with": {}}
    bucket = container.setdefault(target_branch, [])
    placed_index = _insert_at(bucket, new_node, index if explicit else -1)

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
            "branch": target_branch,
            "index": placed_index,
            "anchor": anchor.get("id") if anchor else None,
        },
        "bytes": [len(raw), len(encoded)],
        "sha": _sha256(encoded)[:12],
        "ms": round((time.perf_counter() - started) * 1000, 1),
        "compile": compile_result,
        "flow": flow,
    }


# 控制流节点的标准初始形状，对齐 src/rpa_core/model/workflow.py 的判别联合。
# ★ 控制流是 AST 节点，不是 manifest 命令（AGENTS.md 规则 1；compiler.py 会主动拒绝
#   `command="if"` 这类写法）。所以另开一条插入通道，不往 catalog 里塞任何东西。
_CONTROL_TEMPLATES: dict[str, dict[str, Any]] = {
    "if": {"type": "if", "condition": {"op": "truthy", "left": ""}, "then": [], "else": []},
    "forEach": {"type": "forEach", "items": [], "item_var": "item", "children": []},
    "try": {"type": "try", "children": [], "catch": [], "error_var": "error"},
    "return": {"type": "return", "value": None},
}


@api_method("rpa.insertControlNode")
def insert_control_node(
    path: str,
    kind: str,
    anchor_id: str = "",
    branch: str = "",
    parent_id: str = "",
    index: int = -1,
    explicit: bool = False,
) -> dict[str, Any]:
    """插入控制流结构节点，带齐标准空分支；落点语义与 insert_node 完全一致。"""
    started = time.perf_counter()
    target = _flow_path(path)
    document, raw = _read_document(target)

    def failed(code: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "code": code,
            "error": message,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    entry = _flows.get(path)
    if entry is not None and entry["sha"] != _sha256(raw):
        return failed("FLOW_CHANGED", "文件在打开后被其他程序改过，已拒绝覆盖（重新打开流程再改）")
    template = _CONTROL_TEMPLATES.get(kind)
    if template is None:
        return failed("UNKNOWN_CONTROL_KIND", f"不支持的控制流节点：{kind}")

    try:
        container, target_branch, anchor = _resolve_target(
            document, anchor_id, branch, parent_id, explicit
        )
    except ValueError as exc:
        return failed("INVALID_TARGET" if explicit else "INVALID_ANCHOR", str(exc))

    node_id = _allocate_id(document)
    new_node = {"id": node_id, **copy.deepcopy(template)}
    bucket = container.setdefault(target_branch, [])
    placed_index = _insert_at(bucket, new_node, index if explicit else -1)

    try:
        Workflow.model_validate(document)
    except Exception as exc:  # noqa: BLE001 - 校验失败按「不写盘」处理
        return failed("INVALID_WORKFLOW", f"插入后结构不合法，未写盘：{type(exc).__name__}: {exc}")

    encoded = _encode_document(document)
    target.write_bytes(encoded)
    compile_result = _compile(document)
    flow = open_flow(path)
    return {
        "ok": True,
        "newNodeId": node_id,
        "kind": kind,
        "placed": {
            "parent": container.get("id") or "root",
            "branch": target_branch,
            "index": placed_index,
            "anchor": anchor.get("id") if anchor else None,
        },
        "bytes": [len(raw), len(encoded)],
        "sha": _sha256(encoded)[:12],
        "ms": round((time.perf_counter() - started) * 1000, 1),
        "compile": compile_result,
        "flow": flow,
    }


@api_method("rpa.moveNode")
def move_node(
    path: str,
    node_id: str,
    parent_id: str = "",
    branch: str = "",
    index: int = -1,
) -> dict[str, Any]:
    """拖拽排序 / 跨分支移动：源按 id 定位，目标按 (parent_id, branch, index)。

    前端只报「鼠标落在哪一行的上半区还是下半区」，AST 怎么改全由后端把关：
    防环（容器不能进自己的后代）、目标必须是容器且分支合法、同分支移动要修正
    pop 后左移的下标、写回前 Workflow.model_validate，任何一步失败都不落盘。
    """
    started = time.perf_counter()
    target = _flow_path(path)
    document, raw = _read_document(target)

    def failed(code: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "code": code,
            "error": message,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }

    # move 的乐观锁：与参数保存共用 _flows 里的 sha，挡住「别处改过盘还继续写」。
    entry = _flows.get(path)
    if entry is not None and entry["sha"] != _sha256(raw):
        return failed("FLOW_CHANGED", "文件在打开后被其他程序改过，已拒绝覆盖（重新打开流程再改）")

    root = document.get("root") or {}
    located = _locate(root, node_id)
    if located is None:
        return failed("NODE_NOT_FOUND", f"节点不存在：{node_id}")
    src_parent, src_branch, src_index, node = located
    if src_parent is None or src_branch is None or src_index is None:
        return failed("ROOT_NOT_MOVABLE", "根节点不能移动（流程至少要有一个 root）")

    try:
        container, target_branch = _explicit_container(document, parent_id, branch)
    except ValueError as exc:
        return failed("INVALID_TARGET", str(exc))

    # _collect_ids 含源节点自身，一并挡住「拖进自己」与「拖进自己的子分支」。
    subtree: list[str] = []
    _collect_ids(node, subtree)
    if (container.get("id") or "root") in subtree:
        return failed("CYCLE", "不能把节点拖进它自己或它的子分支里")

    bucket = container.setdefault(target_branch, [])
    # 同一条分支里，前端给的下标是 pop 之前的；源在目标之前时目标会左移一格。
    if container is src_parent and target_branch == src_branch and index > src_index:
        index -= 1
    src_parent[src_branch].pop(src_index)
    actual = _insert_at(bucket, node, index)

    try:
        Workflow.model_validate(document)
    except Exception as exc:  # noqa: BLE001 - 校验失败按「不写盘」处理
        return failed(
            "INVALID_WORKFLOW", f"移动后结构不合法，未写盘：{type(exc).__name__}: {exc}"
        )

    encoded = _encode_document(document)
    target.write_bytes(encoded)
    compile_result = _compile(document)
    flow = open_flow(path)
    return {
        "ok": True,
        "moved": {
            "nodeId": node_id,
            "parent": container.get("id") or "root",
            "branch": target_branch,
            "index": actual,
            "from": {
                "parent": src_parent.get("id") or "root",
                "branch": src_branch,
                "index": src_index,
            },
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
# 元素：缺失检测 / 资产列表 / 捕获 → 确认入库 / 详情改名删除
# 「AI 生成的工作流」只把交互元素写成 elementRefs 占位（模型不知道真实选择器），
# 运行期解析不到即 ELEMENT_REF_MISSING；这组 RPC 把缺口摊开并接上 main 的捕获器。
# 捕获（startCapture/capturePoll）只**暂存**描述符，落盘由用户在编辑框里确认
# （commitElement）——名字允许中文，改名时流程里的同名引用一起改。
# ---------------------------------------------------------------------------

# key 必须是 (path, captureId) 复合键：同一流程可能先后起两次捕获，
# 单键会让后一次静默盖掉前一次的状态 —— 用户看到的是「等了半天没反应」。
# ★ captureId 而不是元素名：名字要等抓完、用户在编辑框里确认，捕获期还没有名字。
_CAPTURES: dict[tuple[str, str], dict[str, Any]] = {}


def _capture_key(path: str, capture_id: str) -> tuple[str, str]:
    return (path, capture_id)


def _new_edit_dialog() -> ElementEditDialog | None:
    """win32 原生元素编辑框；非 Windows 返回 None（宿主回退页面弹窗形态）。

    与提示浮窗同一个理由：「捕获 → 确认入库」不该依赖宿主页面活着——
    页面关了、桥断了，桌面框照样能改名入库。
    """
    if sys.platform != "win32":
        return None
    return ElementEditDialog()


def _selector_edit_text(doc: dict[str, Any]) -> str:
    """可编辑的定位文本：网页给 css，桌面给 locator JSON（供编辑框预填）。"""
    selector = doc.get("selector") or {}
    if doc.get("kind") == "desktop":
        return json.dumps(selector, ensure_ascii=False, indent=2)
    return str(selector.get("css") or "")


def _selector_edit_error(doc: dict[str, Any], text: str) -> str | None:
    """选择器编辑的校验（**不改 doc**）：返回错误文案或 None。"""
    text = (text or "").strip()
    if doc.get("kind") == "browser":
        if not text:
            return "选择器不能为空"
        if "\n" in text or "\r" in text:
            return "网页选择器只能是一行"
        return None
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        return f"定位不是合法 JSON：{exc}"
    if not isinstance(parsed, dict) or not isinstance(parsed.get("locator"), dict):
        return '桌面定位必须是含 "locator" 字段的 JSON'
    return None


def _apply_selector_edit(doc: dict[str, Any], text: str) -> None:
    """把校验过的定位写回 doc（调用前必须先过 _selector_edit_error）。"""
    text = (text or "").strip()
    if doc.get("kind") == "browser":
        doc.setdefault("selector", {})["css"] = text
    else:
        doc["selector"] = json.loads(text)


def _capture_edit_model(entry: dict[str, Any]) -> dict[str, str]:
    """捕获确认框的内容：建议名 + 可编辑定位 + 只读 meta（多行 EDIT 只吃 CRLF 折行）。"""
    preview = entry.get("preview") or {}
    kind = preview.get("kind")
    lines = ["网页元素" if kind == "browser" else "桌面元素"]
    if preview.get("matches"):
        lines.append(f"命中 {preview['matches']} 个")
    node = preview.get("nodeId")
    lines.append(f"将绑定到 {node}" if node else "存为资产（不绑定节点）")
    return {
        "title": "捕获到元素",
        "name": str(preview.get("name") or entry.get("suggestName") or ""),
        "selector": _selector_edit_text(entry.get("captured") or {}),
        "meta": "\r\n".join(lines),
    }


def _commit_from_dialog(entry: dict[str, Any], name: str, selector: str) -> str | None:
    """win32 确认框的保存回调：返回 None=成功关框 / 文案=错误回显留框。

    先校验定位再动盘：定位非法时用户留在框里改，盘上不落半成品。
    """
    captured = entry.get("captured") or {}
    error = _selector_edit_error(captured, selector)
    if error:
        return error
    _apply_selector_edit(captured, selector)
    res = _do_commit(entry, name)
    return None if res["ok"] else str(res["error"])


def _elements_dir(path: str) -> Path:
    return _flow_path(path).parent / "elements"


def _present_elements(path: str) -> set[str]:
    ed = _elements_dir(path)
    return {f.stem for f in ed.glob("*.json")} if ed.is_dir() else set()


def _session_reachable() -> bool:
    """会话服务端口是否可达。**只是可达**，不等于「会话里已经有插件回传」。

    名字故意叫 reachable：把它当能力真值用，就会重演「进程起来了 ⇒ 能力在线」的假绿灯。
    """
    import socket

    try:
        with socket.create_connection(("127.0.0.1", _studio_endpoint()), timeout=0.3):
            return True
    except OSError:
        return False


# env-status 实测热机 ~3.6s（冷启动更久），3s 预算必然误杀——误杀不只是「报错离线」，
# 还会把处理线程钉在管道收尾上（见 _env_status_json 的注释）。
_ENV_STATUS_TIMEOUT = 10.0


def _env_status_json(argv: list[str]) -> tuple[int, dict[str, Any]]:
    """跑 ``rpa-core env-status``，返回 ``(rc, JSON)``；超时抛 ``TimeoutExpired``。

    ★ 不能用 ``subprocess.run`` 的 ``capture_output`` + ``timeout``：``rpa-core.exe``
    是 shim，会再 spawn python 孙进程；超时 kill 掉 shim 后孙进程仍持有 stdout 管道，
    ``run`` 的收尾读**一直堵到孙进程自己退出**——HTTP 处理线程就这么被钉死，前端
    30s 超时（2026-11-13 早真机：开机冷启动 env-status >3s 被 3s 预算误杀，
    线程跟着挂死，用户看到「RPA Probe TIMEOUT: PPX API 调用超时（30000ms）」）。
    改成落临时文件：kill 完 shim 立刻能走，孙进程接着写文件无害。
    """
    import tempfile

    with tempfile.TemporaryFile(mode="w+b") as tmp:
        proc = subprocess.Popen(
            [*argv, "env-status"],
            cwd=str(WORKSPACE),
            env=_run_env(),
            stdout=tmp,
            stderr=subprocess.DEVNULL,
        )
        try:
            rc = proc.wait(timeout=_ENV_STATUS_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise
        tmp.seek(0)
        raw = tmp.read()
    return rc, json.loads(raw.decode("utf-8", errors="replace"))


@api_method("rpa.capabilitySnapshot")
def capability_snapshot() -> dict[str, Any]:
    """一次取回能力真值：运行时 / 扩展在线 / 各浏览器 / 会话端口。

    真值来自 `rpa-core env-status`（与 devserver 同一份结构），不猜、不缓存。
    **探测必须发生在 spawn 之前**：旧实现起完子进程才知道行不行，用户会在
    「看起来在等」的状态里空等一分钟。任何异常都按离线处理，并给出具体修法。
    """
    started = time.perf_counter()
    snap: dict[str, Any] = {
        "ok": True,
        "runtime": False,
        "extensionOnline": False,
        "browsers": {},
        "sessionReachable": _session_reachable(),
        "fix": None,
    }
    try:
        argv = _rpa_argv()
    except RuntimeError:
        # 异常里带的是实现名词（路径、模块名），不能直接当界面文案。
        snap["fix"] = "运行时没连上：重新打开窗口，或在设置里重装运行时"
        snap["ms"] = round((time.perf_counter() - started) * 1000, 1)
        return snap
    try:
        rc, doc = _env_status_json(argv)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        snap["fix"] = f"连不上运行时（{type(exc).__name__}）：重新打开窗口再试"
        snap["ms"] = round((time.perf_counter() - started) * 1000, 1)
        return snap
    browsers = doc.get("browsers") or {}
    snap["browsers"] = browsers
    snap["runtime"] = rc == 0
    snap["extensionOnline"] = any(
        bool(info.get("online")) for info in browsers.values() if isinstance(info, dict)
    )
    if not snap["runtime"]:
        snap["fix"] = "运行时没连上：重新打开窗口再试"
    elif not snap["extensionOnline"]:
        installed = [n for n, i in browsers.items() if isinstance(i, dict) and i.get("installed")]
        snap["fix"] = (
            f"浏览器还没连上（已装插件：{', '.join(sorted(installed)) or '无'}）；"
            "先打开装好插件的浏览器再试"
        )
    snap["ms"] = round((time.perf_counter() - started) * 1000, 1)
    return snap


def resolve_target(
    command: str | None, capabilities: dict[str, Any], override: str | None = None
) -> dict[str, Any]:
    """**唯一权威**：把「命令类型 + 能力真值」收敛成一个 target。

    前端不再传 target / browserType，也不再让用户选网页还是桌面。
    `desktop` 一律带 `--hover`（桌面 hover 本身就是双通道仲裁，混合流程靠它接住先回传者）。
    返回值带 decidedBy，自动决策可解释；override 只在本次调用生效，不写回任何配置。
    """
    if override in ("browser", "desktop"):
        return {
            "target": override,
            "hover": override == "desktop",
            "reason": "你选的方式（只本次生效）",
            "decidedBy": "override",
        }
    cmd = (command or "").strip().lower()
    if cmd.startswith("browser."):
        return {
            "target": "browser",
            "hover": False,
            "reason": "这一步作用在网页上",
            "decidedBy": "command",
        }
    if cmd.startswith("desktop."):
        return {
            "target": "desktop",
            "hover": True,
            "reason": "这一步作用在桌面控件上",
            "decidedBy": "command",
        }
    online = bool(capabilities.get("extensionOnline"))
    return {
        "target": "browser" if online else "desktop",
        "hover": not online,
        "reason": "这一步没写明位置，按当前能用的方式",
        "decidedBy": "capability",
    }


def _scan_missing(path: str) -> list[dict[str, Any]]:
    """扫 elementRefs 缺口的**唯一权威**；渲染与启动捕获共用，避免两处各算一份。"""
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
    return missing


@api_method("rpa.missingElements")
def missing_elements(path: str) -> dict[str, Any]:
    """扫描 workflow 的 elementRefs：引用了、但 elements/ 下没有同名文件 = 待补元素。

    这正是「AI 生成的工作流」与「真正能跑的工作流」之间的缺口：生成时交互元素只能
    写成 elementRefs 占位（模型不知道真实选择器），运行期解析不到即 ELEMENT_REF_MISSING。
    这里把缺口摊开，前端据此提示用户「去捕获 / 提供」每个待补元素。
    """
    started = time.perf_counter()
    missing = _scan_missing(path)
    # M66：能力与缺口同批取，避免「渲染时说在线、点下去已离线」的两次探测漂移。
    capabilities = capability_snapshot()
    for item in missing:
        decision = resolve_target(item.get("command"), capabilities)
        item["decision"] = decision
        item["capabilityFix"] = (
            capabilities["fix"]
            if decision["target"] == "browser" and not capabilities["extensionOnline"]
            else None
        )
    return {
        "ok": True,
        "missing": missing,
        "resolved": sorted(_present_elements(path)),
        "capabilities": capabilities,
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


# ---- 提示面：宿主界面里不出现提示（2026-11-12 交互改版） --------------------
# 元素面板只留「一个捕获按钮 + 资产清单」。检环境、没连上、这次没成、删不掉这些消息
# 全部走桌面浮窗——捕获期主窗要让开目标，浮窗是唯一能被看见的那块界面。


def _new_float() -> Any:
    """一个新的桌面浮窗；非 Windows 或建不起来时返回 None（引擎层仍可用）。"""
    if sys.platform != "win32":
        return None
    try:
        return CaptureFloat()
    except Exception:  # noqa: BLE001 - 没有浮窗不该把 RPC 拖死
        return None


def _notify(title: str, detail: str = "", *, auto_hide_ms: int = 12000) -> None:
    """把一条提示显示在桌面浮窗；点「关闭」或到点自动收起。"""
    window = _new_float()
    if window is None:
        return
    try:
        window.show_notice(title, detail, on_close=window.hide, auto_hide_ms=auto_hide_ms)
    except Exception:  # noqa: BLE001 - 提示失败不影响主流程
        pass


def _selector_text(doc: dict[str, Any]) -> str:
    """描述符 → 一行给人看的定位（网页给 CSS，桌面给控件名）。"""
    selector = doc.get("selector") or {}
    if doc.get("kind") == "desktop":
        steps = (selector.get("locator") or {}).get("path") or []
        last = steps[-1] if steps else {}
        label = last.get("name") or last.get("automationId") or last.get("controlType")
        return str(label or "桌面控件")
    return str(selector.get("css") or "")


def _suggest_name(path: str) -> str:
    """新资产的默认名：元素1、元素2…（撞名就换下一个）。名字允许中文。"""
    present = _present_elements(path)
    for index in range(1, 1000):
        candidate = f"元素{index}"
        if candidate not in present:
            return candidate
    return "元素"


_capture_seq = 0
_capture_seq_lock = threading.Lock()


def _next_capture_id() -> str:
    """捕获会话 id。**不再拿元素名当键**：名字要等抓完、用户在编辑框里确认。"""
    global _capture_seq
    with _capture_seq_lock:
        _capture_seq += 1
        return f"c{_capture_seq}"


def _hold_captured(entry: dict[str, Any], descriptor: dict[str, Any]) -> dict[str, Any]:
    """捕获成功**只暂存不落盘**：写哪个名字、要不要留，等用户在编辑框里确认。

    ``on_captured`` 的返回值会被 ``CaptureController`` 当作结果（带 ``kind`` 才算 ready），
    所以这里把归一化后的描述符原样返回。
    """
    doc = ElementDescriptor(
        kind=descriptor["kind"],
        selector=descriptor["selector"],
        verify_count=descriptor.get("verifyCount", 0),
        metadata=descriptor.get("metadata", {}),
    ).document()
    entry["captured"] = doc
    entry["preview"] = {
        "name": entry.get("name") or entry.get("suggestName"),
        "kind": doc["kind"],
        "selector": _selector_text(doc),
        "matches": doc.get("verifyCount"),
        "nodeId": entry.get("nodeId"),
        "ref": entry.get("name"),
        "overwrite": bool(entry.get("overwrite")),
    }
    return doc


def _pick_plan(path: str, name: str | None, override: str | None = None) -> dict[str, Any]:
    """探测 + 决策的**唯一权威**：spawn 之前把「能不能开始、用哪条腿」一次算清。

    只有 ``rpa.startCapture`` 走这里。探测与 spawn 分两处判断就会出现
    「界面说就绪、后端说离线」的假绿灯。
    """
    item = next((m for m in _scan_missing(path) if m.get("name") == name), None)
    capabilities = capability_snapshot()
    decision = resolve_target((item or {}).get("command"), capabilities, override)
    blocked = not capabilities["runtime"] or (
        decision["target"] == "browser" and not capabilities["extensionOnline"]
    )
    return {
        "item": item,
        "capabilities": capabilities,
        "decision": decision,
        "blocked": blocked,
        "fix": capabilities["fix"],
    }


class _WebviewHost:
    """PPX 宿主适配：同进程的 pywebview 主窗（与 ppx 内置 ``window.minimize`` 同一对象）。

    后端直接调 ``window.minimize()/restore()``，不依赖前端 JS 定时器——窗口最小化后
    脚本可能被节流，「还原」不能押在它身上。非 pywebview 环境（门禁/纯浏览器）静默跳过。
    """

    @staticmethod
    def _window():
        try:
            import webview  # ppx 运行时已加载；门禁环境没有就跳过
        except Exception:  # noqa: BLE001
            return None
        windows = getattr(webview, "windows", None) or []
        return windows[0] if windows else None

    def minimize(self) -> None:
        window = self._window()
        if window is not None:
            window.minimize()

    def restore(self) -> None:
        window = self._window()
        if window is not None:
            window.restore()


@api_method("rpa.startCapture")
def start_capture(
    path: str, name: str | None = None, override: str | None = None
) -> dict[str, Any]:
    """点一下就抓：**不预填名字、不选网页/桌面、不写选择器**。

    界面只负责说「抓一个元素」，怎么抓由后端 ``_pick_plan`` 决定，抓到什么由
    ``CaptureController``（桌面 hover + 浏览器扩展双通道，先回传者胜）产出：

    - 桌面腿 ``hybrid=True``：鼠标移到浏览器内容区时**让位给扩展页内捕获**；
    - 扩展腿**永远 arm**：只要插件已连接，网页内 Ctrl+Click 即捕获页面元素；
    - 没装扩展时自动退化为纯桌面 hover（扩展腿 start 即离线，永不触发）。

    **整条链路没有「超时」概念**：捕获窗一直开着，直到用户点选（→ ready）或取消。
    抓到之后**不落盘**：名字与是否入库要等用户在编辑框里确认（``rpa.commitElement``）。
    环境检查要一次子进程往返，所以提示浮窗在检查**之前**就亮出来——界面里不显示任何提示。

    返回 ``captureId``；前端用 ``rpa.capturePoll`` 轮询。被挡住时不 spawn，浮窗里给修法。
    """
    started = time.perf_counter()
    capture_id = _next_capture_id()
    window = _new_float()
    if window is not None:
        window.show_notice("正在准备…", "检查运行环境和浏览器")
    plan = _pick_plan(path, name, override)
    decision = plan["decision"]
    if plan["blocked"]:
        fix = plan["fix"] or "现在还不能开始"
        _show_blocked(window, "还不能开始", fix)
        return {
            "ok": False,
            "state": "blocked",
            "captureId": capture_id,
            "code": "CAPABILITY_OFFLINE",
            "capabilities": plan["capabilities"],
            "fix": fix,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
    entry: dict[str, Any] = {
        "id": capture_id,
        "path": path,
        "name": (name or "").strip() or None,
        # 只有「按已有名字重抓」才允许覆盖同名资产；新抓的撞名一律要求改名。
        "overwrite": bool((name or "").strip()),
        "nodeId": (plan["item"] or {}).get("nodeId"),
        "decision": decision,
        "controller": None,
        "captured": None,
        "preview": None,
        "notified": False,
        "startedAt": time.time(),
    }
    entry["suggestName"] = entry["name"] or _suggest_name(path)
    key = _capture_key(path, capture_id)
    _CAPTURES[key] = entry
    try:
        controller = CaptureController(
            on_captured=lambda captured: _hold_captured(entry, captured),
            host=_WebviewHost(),
            # 与「正在准备…」共用同一个浮窗：就绪时**原地**变成捕获窗，不闪窗。
            float_factory=lambda: window if window is not None else CaptureFloat(),
            # 编辑框收进 capture_kit（win32）：抓完控制器直接弹桌面确认框，
            # 「捕获 → 确认入库」不再依赖宿主页面活着；非 Windows 缺省走旧的
            # ready 语义，前端弹自己的页面框兜底。
            edit_dialog_factory=_new_edit_dialog,
            edit_model=lambda doc: _capture_edit_model(entry),
            on_commit=lambda name: _commit_from_dialog(entry, name),
        )
        launched = controller.start()
    except Exception as exc:  # noqa: BLE001 - 起捕获失败必须如实回前端，别闷成等待态
        _CAPTURES.pop(key, None)
        _show_blocked(window, "起不了捕获", f"这次没开始：{exc}")
        return {
            "ok": False,
            "code": "CAPTURE_START_FAILED",
            "captureId": capture_id,
            "error": f"起不了捕获：{exc}",
        }
    if not launched.get("ok"):
        # 双通道都没了（非 Windows + 插件离线）：明确挡下，别让用户空等。
        _CAPTURES.pop(key, None)
        reason = plan["fix"] or "桌面捕获只在 Windows 上可用，浏览器插件也没连上"
        _show_blocked(window, "起不了捕获", reason)
        return {
            "ok": False,
            "state": "blocked",
            "captureId": capture_id,
            "code": launched.get("code") or "NO_CAPTURE_CHANNEL",
            "capabilities": plan["capabilities"],
            "fix": reason,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
    entry["controller"] = controller
    return {
        "ok": True,
        "state": "waiting",
        "captureId": capture_id,
        "name": entry["name"],
        "suggestedName": entry["suggestName"],
        "nodeId": entry["nodeId"],
        "offline": bool(launched.get("offline", False)),
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _show_blocked(window: Any, title: str, detail: str) -> None:
    """被挡住时把原因写在浮窗里（界面里不显示任何提示）。"""
    if window is not None:
        try:
            window.show_notice(title, detail, on_close=window.hide)
            return
        except Exception:  # noqa: BLE001 - 浮窗失败就退回新开一个
            pass
    _notify(title, detail, auto_hide_ms=0)


@api_method("rpa.capturePoll")
def capture_poll(path: str, capture_id: str) -> dict[str, Any]:
    """轮询状态机：pending / ready / cancelled / offline / failed。

    **没有 deadline、没有 timeout**：状态只看后台 pick 线程产出，不靠时间判断。
    ``ready`` 只回一个 ``preview``（名字待确认），**盘上还没有文件**——入库是
    ``rpa.commitElement`` 的事。失败/离线时顺手把原因写到桌面浮窗（界面里不显示）。
    """
    key = _capture_key(path, capture_id)
    entry = _CAPTURES.get(key)
    if entry is None:
        return {
            "ok": False,
            "code": "UNKNOWN_CAPTURE",
            "error": f"没有这次捕获：{capture_id}",
        }
    controller = entry.get("controller")
    if controller is None:
        return {"ok": False, "code": "CAPTURE_NOT_ARMED", "error": "这次捕获没有起来"}
    state = controller.poll()
    payload: dict[str, Any] = {
        "ok": True,
        "captureId": capture_id,
        "path": path,
        "state": state["state"],
    }
    if state["state"] == "ready":
        payload["preview"] = entry.get("preview")
    elif state["state"] == "committed":
        # win32 确认框已完成入库：带上最终名字，会话读后即焚（同 cancel 语义）。
        payload["name"] = state.get("name")
        _CAPTURES.pop(key, None)
    elif state["state"] == "discarded":
        _CAPTURES.pop(key, None)
    elif state["state"] in ("offline", "failed"):
        payload["reason"] = state.get("reason")
        if not entry.get("notified"):
            entry["notified"] = True
            _notify(
                "这次没成",
                state.get("reason") or "没收到点选，点一下按钮再试一次",
            )
    else:
        payload["offline"] = bool(state.get("offline", False))
        payload["armed"] = bool(state.get("armed", False))
    return payload


@api_method("rpa.cancelCapture")
def cancel_capture(path: str, capture_id: str) -> dict[str, Any]:
    """取消一次捕获：浮窗与等待态一起收掉（编辑框取消也走这里）。"""
    key = _capture_key(path, capture_id)
    entry = _CAPTURES.get(key)
    if entry is None:
        return {"ok": False, "code": "UNKNOWN_CAPTURE", "error": f"没有这次捕获：{capture_id}"}
    controller = entry.get("controller")
    if controller is not None:
        try:
            controller.cancel()
        except Exception:  # noqa: BLE001 - 取消失败也要退出等待态
            pass
    _CAPTURES.pop(key, None)
    return {"ok": True, "state": "cancelled", "captureId": capture_id, "path": path}


def _valid_element_name(name: str | None) -> bool:
    """资产名唯一口径：入库、改名、详情、删除共用，同时挡掉路径穿越。

    ★ **允许中文**（用户侧要求「资产名称支持中文输入」）。只挡真正会弄坏磁盘名的字符：
    路径分隔符、Windows 保留字符、控制字符、点（避免与 ``.json`` 后缀混淆）、
    首尾空白、以及 ``CON``/``NUL`` 这类设备名。
    """
    if not isinstance(name, str):
        return False
    if not name or len(name) > 48 or name != name.strip():
        return False
    if any(ch in _NAME_BAD_CHARS or ord(ch) < 32 for ch in name):
        return False
    return name.split(".")[0].lower() not in _RESERVED_NAMES


_NAME_BAD_CHARS = frozenset('/\\:*?"<>|.')
_RESERVED_NAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{index}" for index in range(1, 10)]
    + [f"lpt{index}" for index in range(1, 10)]
)


def _element_error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "error": message}


def _write_element_doc(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _rewrite_refs(path: str, old: str, new: str) -> list[str]:
    """把流程里引用 ``old`` 的 elementRefs 一起改成 ``new``（改名/入库即绑定）。

    名字是元素与节点之间**唯一**的接线（ref-only，ADR-002）。只在文件侧改名会留下
    「元素在盘上、流程还指着旧名」的悬空引用，用户看到的就是「明明抓过了还说没抓」。
    """
    target = _flow_path(path)
    if not target.is_file():
        return []
    document, _ = _read_document(target)
    hits: list[str] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        refs = node.get("elementRefs")
        if isinstance(refs, dict):
            for param, value in list(refs.items()):
                if value == old:
                    refs[param] = new
                    if node.get("id"):
                        hits.append(str(node["id"]))
        for key in _NESTED:
            for child in node.get(key) or []:
                walk(child)

    walk(document.get("root"))
    if not hits:
        return []
    target.write_bytes(_encode_document(document))
    return sorted(set(hits))


def _do_commit(entry: dict[str, Any], name: str) -> dict[str, Any]:
    """入库的**唯一权威**：web 弹窗（``rpa.commitElement``）与 win32 确认框
    （``_commit_from_dialog``）共用。**不碰会话表**——pop 归各自的调用方：
    RPC 路径提交即 pop；win32 路径由 ``capture_poll`` 读到 committed 才 pop。
    """
    name = (name or "").strip()
    if not _valid_element_name(name):
        return _element_error("BAD_NAME", "名字不能为空，也不能含 / \\ : * ? \" < > | . 这些字符")
    path = entry["path"]
    target = _elements_dir(path) / f"{name}.json"
    overwrite = bool(entry.get("overwrite")) and name == (entry.get("name") or "")
    if target.exists() and not overwrite:
        return _element_error("NAME_TAKEN", f"已经有一个叫「{name}」的元素了，换个名字")
    doc = dict(entry["captured"] or {})
    doc["name"] = name
    _write_element_doc(target, doc)
    renamed: list[str] = []
    ref = entry.get("name") or ""
    if ref and ref != name:
        try:
            renamed = _rewrite_refs(path, ref, name)
        except OSError:
            renamed = []
    return {
        "ok": True,
        "name": name,
        "file": str(target),
        "boundNodeId": entry.get("nodeId"),
        "renamedRefs": renamed,
    }


@api_method("rpa.commitElement")
def commit_element(path: str, capture_id: str, name: str) -> dict[str, Any]:
    """用户确认后入库：捕获描述符 + 用户改好的名字 → ``elements/<name>.json``。

    这是捕获链路上**唯一**写元素文件的地方（捕获本身只暂存）。名字被改过时，
    流程里同名的 elementRefs 一起改名——否则资产与引用就对不上，等于白抓。
    """
    started = time.perf_counter()
    key = _capture_key(path, capture_id)
    entry = _CAPTURES.get(key)
    if entry is None or not entry.get("captured"):
        return _element_error("NO_CAPTURE", "这次捕获已经结束了，重新抓一次")
    res = _do_commit(entry, name)
    if not res["ok"]:
        return res
    _CAPTURES.pop(key, None)
    res["ms"] = round((time.perf_counter() - started) * 1000, 1)
    return res


@api_method("rpa.discardCapture")
def discard_capture(path: str, capture_id: str) -> dict[str, Any]:
    """用户在编辑框里点了取消：把暂存的描述符丢掉，盘上什么都不留。"""
    key = _capture_key(path, capture_id)
    entry = _CAPTURES.pop(key, None)
    if entry is None:
        return {"ok": True, "discarded": False, "captureId": capture_id}
    return {"ok": True, "discarded": True, "captureId": capture_id}


def _do_rename(path: str, name: str, new_name: str | None) -> dict[str, Any]:
    """改名的**唯一权威**：web 详情弹窗（``rpa.saveElement``）与 win32 详情框
    （``edit_element`` 的 on_save）共用。失败回滚文件名与引用，绝不留悬空引用。"""
    old = (name or "").strip()
    if not _valid_element_name(old):
        return _element_error("BAD_NAME", "这个名字用不了")
    ed = _elements_dir(path)
    source = ed / f"{old}.json"
    if not source.is_file():
        return _element_error("NOT_FOUND", f"没有这个资产：{old}")
    new = (new_name or "").strip() or old
    if not _valid_element_name(new):
        return _element_error("BAD_NAME", "名字不能为空，也不能含 / \\ : * ? \" < > | . 这些字符")
    if new == old:
        return {"ok": True, "name": old, "renamedRefs": [], "unchanged": True}
    target = ed / f"{new}.json"
    if target.exists():
        return _element_error("NAME_TAKEN", f"已经有一个叫「{new}」的元素了，换个名字")
    try:
        source.replace(target)
    except OSError as exc:
        return _element_error("RENAME_FAILED", f"改名没成：{exc}")
    renamed: list[str] = []
    try:
        renamed = _rewrite_refs(path, old, new)
        doc = json.loads(target.read_text(encoding="utf-8"))
        doc["name"] = new
        _write_element_doc(target, doc)
    except (OSError, ValueError) as exc:
        # 引用没改成，名字也退回去：宁可这次白改，也不能留下悬空引用。
        try:
            target.replace(source)
            if renamed:
                _rewrite_refs(path, new, old)
        except OSError:
            pass
        return _element_error("RENAME_FAILED", f"改名没成：{exc}")
    return {"ok": True, "name": new, "file": str(target), "renamedRefs": renamed}


@api_method("rpa.saveElement")
def save_element(path: str, name: str, new_name: str | None = None) -> dict[str, Any]:
    """详情框的保存：改的只有资产名称（重名/改名后引用一起更新）。"""
    started = time.perf_counter()
    res = _do_rename(path, name, new_name)
    res["ms"] = round((time.perf_counter() - started) * 1000, 1)
    return res


class _EditSession:
    """已入库资产的详情编辑会话：复用 ``capture_poll`` 的状态机（editing/committed/discarded）。

    与捕获会话同一个轮询面，前端不用为「详情」学第二条通道。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"state": "editing"}

    def poll(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def finish(self, state: dict[str, Any]) -> None:
        with self._lock:
            self._state = state

    def cancel(self) -> None:  # cancel_capture 的兼容面（详情会话没有腿可撤）
        pass


@api_method("rpa.editElement")
def edit_element(path: str, name: str) -> dict[str, Any]:
    """已入库资产的详情编辑：桌面原生框（win32），与捕获确认框同一个。

    框里可改名字（改名联动 elementRefs），meta 是资产文档全文。非 Windows 返回
    ``EDIT_DIALOG_UNAVAILABLE``，前端回退页面弹窗。
    """
    dialog = _new_edit_dialog()
    if dialog is None:
        return {
            "ok": False,
            "code": "EDIT_DIALOG_UNAVAILABLE",
            "error": "桌面编辑框不可用（非 Windows）",
        }
    name = (name or "").strip()
    doc_path = _elements_dir(path) / f"{name}.json"
    if not doc_path.is_file():
        return _element_error("NOT_FOUND", f"没有这个资产：{name}")
    try:
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _element_error("READ_FAILED", f"读资产失败：{exc}")
    edit_id = _next_capture_id()
    session = _EditSession()
    entry = {
        "id": edit_id,
        "path": path,
        "kind": "edit",
        "name": name,
        "controller": session,
        "captured": None,
        "preview": None,
        "notified": True,
    }
    _CAPTURES[_capture_key(path, edit_id)] = entry

    def _save(new_name: str, new_selector: str) -> str | None:
        # 先校验定位再动文件：名字改了、定位非法会留下半成品。
        error = _selector_edit_error(doc, new_selector)
        if error:
            return error
        res = _do_rename(path, name, new_name)
        if not res["ok"]:
            return str(res["error"])
        # 改名后回写的是打开时读出的那份 doc：名字与定位都要同步成最新
        doc["name"] = res["name"]
        _apply_selector_edit(doc, new_selector)
        _write_element_doc(_elements_dir(path) / f"{res['name']}.json", doc)
        session.finish({"state": "committed", "name": res["name"]})
        return None

    dialog.show(
        title=f"元素详情 · {name}",
        name=name,
        selector=_selector_edit_text(doc),
        meta=json.dumps(doc, ensure_ascii=False, indent=2),
        on_save=_save,
        on_close=lambda saved: saved or session.finish({"state": "discarded"}),
    )
    return {"ok": True, "editId": edit_id, "state": "editing"}


# ---- 元素资产库：详情 / 删除（ADR 0022-R3 后原型目录共享） -------------------
# 与「补一下」的区别：界面新建的是**预置资产**，不改写工作流、不自动绑到任何节点
# （ref-only 契约：运行期只认 elementRefs，没被引用就是一条库存，不会生效）。
# 删除反过来必须挡住仍被引用的资产，否则流程会在运行期硬失败 ELEMENT_REF_MISSING。


def _element_references(path: str, name: str) -> list[str]:
    """扫工作流全部 elementRefs，返回引用了 name 的节点 id（删除护栏用）。

    按 _NESTED 递归真实 AST 分支（then/else/catch/children），不能只扫 children——
    那会漏掉控制流里的引用，删完等运行才炸。
    """
    data, _ = _read_document(_flow_path(path))
    hits: set[str] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if any(ref == name for ref in (node.get("elementRefs") or {}).values()) and node.get("id"):
            hits.add(str(node["id"]))
        for key in _NESTED:
            for child in node.get(key) or []:
                walk(child)

    walk(data.get("root"))
    return sorted(hits)


@api_method("rpa.elementDetail")
def element_detail(path: str, name: str) -> dict[str, Any]:
    """资产详情：完整描述符 + 被哪些节点引用（编辑框的数据来源）。"""
    started = time.perf_counter()
    if not _valid_element_name(name):
        return _element_error("BAD_NAME", "这个名字用不了")
    target = _elements_dir(path) / f"{name}.json"
    if not target.is_file():
        return _element_error("NOT_FOUND", f"没有这个资产：{name}")
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _element_error("BAD_JSON", f"资产文件读不了：{exc}")
    return {
        "ok": True,
        "name": name,
        "file": str(target),
        "size": target.stat().st_size,
        "document": doc,
        "selectorText": _selector_text(doc),
        "referencedBy": _element_references(path, name),
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


@api_method("rpa.deleteElement")
def delete_element(path: str, name: str) -> dict[str, Any]:
    """删一条资产；仍被 elementRefs 引用时拒绝，并把原因写到桌面浮窗。"""
    started = time.perf_counter()
    if not _valid_element_name(name):
        return _element_error("BAD_NAME", "这个名字用不了")
    target = _elements_dir(path) / f"{name}.json"
    if not target.is_file():
        return _element_error("NOT_FOUND", f"没有这个资产：{name}")
    referenced = _element_references(path, name)
    if referenced:
        _notify("删不掉", f"「{name}」还被流程里 {len(referenced)} 处引用着，先改掉引用再删")
        return {
            "ok": False,
            "code": "ELEMENT_IN_USE",
            "error": "这个资产还被流程引用着，先改掉引用再删",
            "referencedBy": referenced,
        }
    target.unlink()
    return {
        "ok": True,
        "name": name,
        "deleted": True,
        "remaining": sorted(f.stem for f in _elements_dir(path).glob("*.json")),
        "ms": round((time.perf_counter() - started) * 1000, 1),
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
        "sessionRoot": str(_session_root()) if _session_root() is not None else None,
        "allowedRoots": [str(root) for root in _allowed_roots()],
        "logPath": str(EDITOR_LOG) if EDITOR_LOG is not None else None,
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
