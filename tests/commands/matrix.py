"""L1 契约矩阵的公共驱动层：用例表加载、`expect` 解释、临时目录与落盘断言。

## 为什么抽这一层

两个驱动共用它：`test_browser_matrix.py`（浏览器扩展通道——假扩展 + 断言真实下发的
op/args/timeoutSeconds）与 `test_data_matrix.py`（数据/工作流通道——真子进程 + 断言
outputs/effects/错误码/落盘内容）。差别只在**插桩点**（假扩展 vs 真 worker 子进程），
`expect` 的键与语义是一份约定。分两份实现迟早各说各话——M38 S1.2 的假绿灯正是
「同一件事两处口径」的变体，不值得再赌一次。

## `expect` 支持的键（全部可选，按需组合）

| 键 | 含义 |
|---|---|
| `noCalls` | 断言没有向通道下发任何命令（仅浏览器驱动） |
| `onlyCall` | 断言**恰好一次**下发；`op`、`args`（子集）、`argsExact`（等值）、`timeoutSeconds` |
| `calls` | 断言完整调用序列（逐项同 `onlyCall` 的语义） |
| `outputs` | `result.outputs` 的子集（递归） |
| `outputListContains` | 指定键是**列表**且**至少有一项**匹配（详见下方说明） |
| `outputKeys` | `result.outputs` 必须包含的键（值不确定时用，如 uuid 形式的 sessionId） |
| `outputPaths` | 指定键的值是**路径**：按 `Path` 比较（分隔符无关；用例表写 `{tmp}/a/b.txt`） |
| `outputsMatch` | `result.outputs` 指定键的**正则**匹配（时间戳这类形状确定、值不确定的输出） |
| `effect` | `effects[0].kind` |
| `effectDetails` | `effects[0].details` 的子集（成功路径的证据断言，如 select 的 selectedItem） |
| `noEffects` | 断言**没有**任何 effect 记录（pure 命令不需要提交证据——与 `effect` 互斥） |
| `errorCode` / `errorDetails` | 失败路径：错误码（精确）/ details（子集） |
| `elapsedAtLeastMs` | 整条命令的墙钟耗时下界（验证 `postDelayMs` 这类「只花时间」的参数） |
| `files` | 磁盘断言（数据通道的主要证据面），见下方说明 |

`outputListContains`：`result.outputs` 的指定键必须是列表、且至少有一项匹配——

- dict 项按子集比、其它按等值；给「结果随本机环境漂移、但被操作对象本身恒定」的命令用
  （`getWindowList` 的整桌面枚举：列表内容随开着的窗口漂移，但靶子窗口那一项恒定）；
- 它同时收住「COM 拒绝 → pywinauto **静默返回空列表**」这条路径——形状断言
  分不清「枚举失败」和「真没有」，这个键能。

`files` 每项的形状：`path`（支持 `{tmp}`）+ 断言方式（可组合，也可以都不给）——

- `equals`：逐字节相等；`contains`：子串包含；
- `missing`：断言路径**不存在**（删除类命令）；
- `jsonContains`：实际 JSON 是期望的**子集**（绕开 `updated_at` 这类噪声字段）；
- **一个都不给**（或只给 `missing: false`）：只断言**存在**——二进制产物走这条
  （`desktop.screenshot` 的 PNG 不是 UTF-8，文本类断言会去 `read_text` 而崩）。

变体可选的键：`inputs` / `sessions`（覆盖默认会话表，浏览器用）/ `stub`（覆盖命令级桩应答）/
`errors`（op → 扩展侧错误）/ `processStub`（打桩进程面，`browser.closeBrowser` 专用）/
`setup`（预置磁盘状态，数据通道用：`files` / `dirs` / `table`）/ `negative`（标记负路径与
边界行为变体，供覆盖率校验器统计）/ `knownGap`（见下节）/ `l2`（L2 真机冒烟块：
`page` + `inputs` 覆盖 + 真机 `expect`；后端换成真扩展，见
`test_browser_l2_matrix.py`——L1 驱动不读这个键）。

## `knownGap`：已实测、已登记、但产品侧还没修的缺口

`knownGap: "缺口说明 + 出路"` 把一个变体钉成**严格 xfail**（`strict=True`）。判据是
「这条期望是对的，产品现在是错的」——与「实测出来实现就是这样，于是把期望改成实际行为」
相反。两者都要用，分界是**期望本身对不对**：

- 期望错（把实现语义想歪了）→ **改期望**（S3 的 `data.writeText` 相对路径那条）；
- 期望对（能力该有，产品没做到）→ **`knownGap`**，期望原样留着。

为什么是严格 xfail 而不是「让这行红着」或「把期望改成实测的错误行为」：

1. **红着不成立**：矩阵要能整段跑绿给人看，一条永久红灯会让「有没有新红」失效；
2. **改期望会把 bug 固化成契约**：下一手读者会以为「正路径就该报 EXECUTOR_FAILED」；
3. **严格模式自我收紧**：缺口一修（或换到装了缺失依赖的环境），这行 **XPASS → 用例失败**，
   强制回来把标记摘掉。与 `check_command_matrix.py` 里 `PENDING_NAMESPACES` /
   `KNOWN_DEAD_PARAMS` 的「台账过期即报红」是同一套自律方式。

配套约束在静态校验器里（同一份 JSON 的另一处口径）：`knownGap` 必须是非空字符串，且
**标记过的变体不计入任何覆盖率口径**——缺口不许用来凑覆盖（见该文件的
`_check_command`）。**不要**拿它盖住一条只是写错了的期望：那是把缺口变成被子。
工作台页签会把 xfail 显示成「跳过」（报告钩子记的是 pytest 的 outcome）——这条在
`tests/commands/conftest.py` §2 的数据合同里也一样，别当成「用例没跑」。

## `{tmp}` 占位符

用例表是纯数据、不能写死本机路径。`{tmp}` 由**各驱动**决定物化到哪：

- 浏览器驱动：整个矩阵共用一个临时目录（`matrix_tmp_dir`），因为真实落盘的命令只有
  `browser.screenshot`，且用例之间不共用文件；
- 数据驱动：**每个变体一个干净目录**（命令本身就是文件读写，用例之间必须互不干扰，
  并且要把「执行前预置了什么、执行后磁盘上是什么」逐字节对上）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

CASES_DIR = Path(__file__).resolve().parent / "cases"


def load_cases(*namespaces: str) -> dict[str, Any]:
    """读用例表：`cases/<命名空间>.json`（每个文件一个命名空间）。

    不传命名空间时合并全部文件（覆盖率校验器与工作台页签是这种用法——它们要看全局）；
    传了就只读那几个——**驱动必须按命名空间取**，否则新增一个通道的用例表会被别的
    驱动的插桩点拿去跑（数据命令走假扩展必然红）。
    """
    if not namespaces:
        paths = sorted(CASES_DIR.glob("*.json"))
    else:
        paths = [CASES_DIR / f"{namespace}.json" for namespace in namespaces]
    merged: dict[str, Any] = {}
    for path in paths:
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged


def namespace_of(command: str) -> str:
    """命令所属命名空间 = 命令 id 的第一段（`data.table.getCell` → `data`）。"""
    return command.split(".")[0]


def variant_marks(variant: Mapping[str, Any]) -> list[Any]:
    """变体级标记：目前只有 `knownGap` → 严格 xfail（语义见模块 docstring）。

    `strict=True` 是刻意的：缺口一旦被修好（或跑在装了缺失依赖的环境里），这行会
    **XPASS 并被判为失败**，逼着人来摘标记——台账不会悄悄过期。
    """
    gap = variant.get("knownGap")
    if not gap:
        return []
    return [pytest.mark.xfail(reason=str(gap), strict=True)]


def iter_variants(*namespaces: str) -> Iterator[Any]:
    """把用例表展开成 `pytest.param(command, spec, variant)`，id 为 `<命令>::<变体名>`。

    用例函数必须叫 `test_command_variant`：`tests/commands/conftest.py` 的结构化报告钩子按
    `test_command_variant[` + `::` 从 nodeid 里切命令与变体名（页面据此建两级结果树）。
    """
    for command, spec in sorted(load_cases(*namespaces).items()):
        for variant in spec.get("variants", []):
            yield pytest.param(
                command,
                spec,
                variant,
                id=f"{command}::{variant['name']}",
                marks=variant_marks(variant),
            )


def subset_problems(actual: Any, expected: Any, path: str = "args") -> list[str]:
    """`expected` 必须是 `actual` 的子集（递归）。返回违规说明列表（空 = 通过）。"""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: 期望 dict，实得 {type(actual).__name__}：{actual!r}"]
        problems: list[str] = []
        for key, value in expected.items():
            if key not in actual:
                problems.append(f"{path}.{key}: 缺失（实际键 {sorted(actual)}）")
                continue
            problems.extend(subset_problems(actual[key], value, f"{path}.{key}"))
        return problems
    if actual != expected:
        return [f"{path}: 期望 {expected!r}，实得 {actual!r}"]
    return []


def check_call(actual, expected: dict[str, Any], path: str) -> list[str]:
    """断言一次下发的调用（`actual` 需有 `op` / `args` / `timeout_seconds`）。"""
    problems: list[str] = []
    if "op" in expected and actual.op != expected["op"]:
        problems.append(f"{path}.op: 期望 {expected['op']!r}，实得 {actual.op!r}")
    if "timeoutSeconds" in expected:
        wanted_s = float(expected["timeoutSeconds"])
        if abs(actual.timeout_seconds - wanted_s) > 1e-6:
            problems.append(
                f"{path}.timeoutSeconds: 期望 {wanted_s}，实得 {actual.timeout_seconds}"
            )
    if "argsExact" in expected:
        if actual.args != expected["argsExact"]:
            problems.append(
                f"{path}.args: 期望完全相等 {expected['argsExact']!r}，实得 {actual.args!r}"
            )
    elif "args" in expected:
        problems.extend(subset_problems(actual.args, expected["args"], f"{path}.args"))
    return problems


def materialize_inputs(
    value: Any, tmp_dir: Path, extra: Mapping[str, Any] | None = None
) -> Any:
    """把用例表里的占位符替换成本次运行的真实值（递归，含列表与字典键值）。

    - `{tmp}`（所有通道）：本次运行的临时目录。用例表是纯数据、不能写死本机路径；
      真实落盘的命令（`browser.screenshot`、`data.*` 全套）必须写进临时目录，
      否则会污染仓库工作树。
    - `extra`（桌面通道专用）：调用方按变体的 `setup` **现算**出来的值——`{session}`
      （预置会话 id）、`{element:input}`（预置元素 id）、`{appTitle}`（靶子窗口标题）、
      `{pid}`（靶子进程 id）。这些值每次都不同（会话 id 是 uuid、pid 随进程变），
      所以只能由驱动在运行时注入，不能写进表里；其它通道不传 `extra`，行为与从前一致。

    `extra` 先替、`{tmp}` 后替：两者的键不重叠，顺序只为确定性。

    **整串占位符保留原类型**：`"{pid}"` 整个字符串就是一个占位符时，替回去的是原始的
    `int` 而不是 `"12345"`。`processId` 这类参数在 schema 里是 integer，而执行器层
    **不做类型转换**（它收到什么就 `==` 比什么），替成字符串会让过滤恒不命中——
    实测踩过：`title` + `processId` 的 attach 变体报 `ELEMENT_NOT_FOUND`，
    而 pid 明明是对的。
    """
    if isinstance(value, str):
        text = value
        for key, replacement in (extra or {}).items():
            token = "{" + key + "}"
            if text == token:
                return replacement
            text = text.replace(token, str(replacement))
        return text.replace("{tmp}", str(tmp_dir))
    if isinstance(value, dict):
        return {
            key: materialize_inputs(item, tmp_dir, extra) for key, item in value.items()
        }
    if isinstance(value, list):
        return [materialize_inputs(item, tmp_dir, extra) for item in value]
    return value


def substitute_extra(value: Any, extra: Mapping[str, Any]) -> Any:
    """只替换 `extra` 里的占位符（递归），**不动** `{tmp}`。

    给 `expect` 用：断言里同样会出现运行时才有的值（`outputs.title == "{appTitle}"`、
    `outputs.workWindowId == "{handle}"`、`outputs.processId == "{pid}"`），不替换就变成
    拿字面量 `'{appTitle}'` 去比真实标题，必然红。而 `{tmp}` 不能在这里替——
    `check_expect` 要自己把 `{tmp}/shot.png` 解析成真实路径再查磁盘。

    整串占位符同样保留原类型（见 `materialize_inputs`）。
    """
    if isinstance(value, str):
        for key, replacement in extra.items():
            token = "{" + key + "}"
            if value == token:
                return replacement
            value = value.replace(token, str(replacement))
        return value
    if isinstance(value, dict):
        return {key: substitute_extra(item, extra) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute_extra(item, extra) for item in value]
    return value


def prepare_setup(spec: dict[str, Any] | None, tmp_dir: Path) -> None:
    """按变体的 `setup` 预置磁盘状态（数据通道用例用）。

    - `dirs`：要预先建好的目录（如「非空目录」用例里的空目录）；
    - `files`：`{path, text}`，写入文本文件（父目录自动创建）；
    - `table`：`{path, data}`，把 `data` 以 `indent=2` 写进 `path`（流程数据表格的预置态，
      写成对象比在 JSON 里嵌一段转义字符串可读得多）。

    用例表是数据，数据会写错——**所有路径都只允许落在 `tmp_dir` 内**（越界直接红，
    见 `test_data_matrix.py` 的安全阀），所以这里不做破坏性操作，只往临时目录里放文件。
    """
    for item in (spec or {}).get("dirs") or []:
        (tmp_dir / str(item)).mkdir(parents=True, exist_ok=True)
    for item in (spec or {}).get("files") or []:
        path = tmp_dir / str(item["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(item["text"]), encoding="utf-8", newline="\n")
    table = (spec or {}).get("table")
    if table is not None:
        path = tmp_dir / str(table["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(table.get("data") or {}, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )


def _check_files(expect: dict[str, Any], tmp_dir: Path) -> list[str]:
    """`expect.files`：命令在磁盘上留下了什么（数据通道的主要证据面）。"""
    problems: list[str] = []
    for index, item in enumerate(expect.get("files") or []):
        path = Path(materialize_inputs(str(item["path"]), tmp_dir))
        if not path.is_absolute():
            # 相对路径按 `tmp_dir` 解析——否则会静默去查当前工作目录下的同名文件（假通过）
            path = tmp_dir / path
        where = f"files[{index}]({path.name})"
        if item.get("missing"):
            if path.exists():
                problems.append(f"{where}: 期望已不存在，实际仍在：{path}")
            continue
        if not path.exists():
            problems.append(f"{where}: 期望存在，实际不存在：{path}")
            continue
        # 文本类断言才读文件。`desktop.screenshot` 落的是 PNG——二进制不是 UTF-8，
        # 无条件 read_text 会 UnicodeDecodeError，把一条「文件确实被写出来了」的断言
        # 变成整个变体崩掉。只声明 `missing: false` 的用例需要的只是「存在」这一步。
        if not ({"equals", "contains", "jsonContains"} & set(item)):
            continue
        text = path.read_text(encoding="utf-8")
        if "equals" in item and text != item["equals"]:
            problems.append(f"{where}: 期望内容 {item['equals']!r}，实得 {text!r}")
        if "contains" in item and item["contains"] not in text:
            problems.append(f"{where}: 期望包含 {item['contains']!r}，实得 {text!r}")
        if "jsonContains" in item:
            try:
                actual_json = json.loads(text)
            except json.JSONDecodeError as exc:
                problems.append(f"{where}: 不是合法 JSON（{exc}）：{text!r}")
            else:
                problems.extend(
                    subset_problems(actual_json, item["jsonContains"], where)
                )
    return problems


def check_list_contains(expect: dict[str, Any], result) -> list[str]:
    """`expect.outputListContains`：outputs 里某个**列表**至少有一项匹配期望。

    为什么需要这个键（而不是拿 `outputs` 凑）：`outputs` 的语义是**子集比较**，列表值
    只能整体等值——而「整桌面枚举」这类命令的结果随本机环境漂移（维护者开着几个窗口就
    几项），于是正路径只能退化成断言形状（`outputKeys`）。形状断言的问题不是不精确，
    而是**收不了错**：`desktop.getWindowList` 走 pywinauto 的
    `uia_element_info._get_elements`，那里 `except (COMError, ValueError): return []`
    ——COM 拒绝调用时**静默返回空列表**（实测到的 `0x8001010d` 就是这条路径，见任务单
    §1.6），报出的 `windows: []` 与「本机真没有匹配窗口」**完全同形**。

    能按值断言而不漂移的锚点不是「整个列表」，而是**被操作对象自己**：靶子窗口的标题、
    刚写入的那条记录。所以这个键的口径是「列表里至少有这一项」，不看其余项。
    """
    problems: list[str] = []
    for key, wanted in (expect.get("outputListContains") or {}).items():
        actual = result.outputs.get(key)
        if not isinstance(actual, list):
            problems.append(
                f"outputListContains.{key}: 期望一个列表，实得 "
                f"{type(actual).__name__}：{actual!r}"
            )
            continue
        matched = [
            item
            for item in actual
            if not subset_problems(item, wanted, f"outputListContains.{key}")
        ]
        if not matched:
            problems.append(
                f"outputListContains.{key}: {len(actual)} 项里没有一项匹配 {wanted!r}"
                f"（实得 {actual!r}）"
            )
    return problems


def check_expect(
    expect: dict[str, Any],
    *,
    result,
    tmp_dir: Path,
    calls: list[Any] | None = None,
    elapsed_ms: float = 0.0,
) -> list[str]:
    """把 `expect` 全部键跑一遍，返回违规说明（空 = 通过）。

    `calls` 是**通道调用记录**（浏览器驱动给假扩展记录、数据驱动不给——它的证据面是
    outputs 与磁盘）。传 `None` 时若用例声明了调用类断言，直接算违规而不是静默跳过：
    「断言写了但没人执行」正是假绿灯的成因。
    """
    problems: list[str] = []

    # -- 调用面 --------------------------------------------------------------
    if expect.get("noCalls"):
        if calls is None:
            problems.append("noCalls: 本驱动没有调用记录，无法断言")
        elif calls:
            problems.append(f"期望不下发任何通道命令，实得 {[call.op for call in calls]}")
    for key in ("onlyCall", "calls"):
        if key not in expect:
            continue
        if calls is None:
            problems.append(f"{key}: 本驱动没有调用记录，无法断言")
            continue
        if key == "onlyCall":
            if len(calls) != 1:
                problems.append(
                    f"期望恰好 1 次下发，实得 {len(calls)}：{[call.op for call in calls]}"
                )
            else:
                problems.extend(check_call(calls[0], expect[key], "onlyCall"))
        else:
            if len(calls) != len(expect[key]):
                problems.append(
                    f"调用次数：期望 {len(expect[key])}，实得 {len(calls)}："
                    f"{[call.op for call in calls]}"
                )
            else:
                for index, (actual, wanted) in enumerate(
                    zip(calls, expect[key], strict=True)
                ):
                    problems.extend(check_call(actual, wanted, f"calls[{index}]"))

    # -- 结果面 --------------------------------------------------------------
    if "errorCode" in expect:
        if result.status != "error" or result.error is None:
            problems.append(f"期望失败（{expect['errorCode']}），实得 status={result.status}")
        elif result.error.code != expect["errorCode"]:
            problems.append(
                f"errorCode：期望 {expect['errorCode']!r}，实得 {result.error.code!r}"
                f"（消息：{result.error.message[:80]}）"
            )
        elif "errorDetails" in expect:
            problems.extend(
                subset_problems(
                    result.error.details or {}, expect["errorDetails"], "errorDetails"
                )
            )
    elif result.status != "success":
        detail = result.error.message if result.error else ""
        problems.append(f"期望成功，实得 status={result.status}：{detail[:120]}")

    if "outputs" in expect:
        problems.extend(subset_problems(result.outputs, expect["outputs"], "outputs"))
    problems.extend(check_list_contains(expect, result))
    if "outputKeys" in expect:
        missing = [key for key in expect["outputKeys"] if key not in result.outputs]
        if missing:
            problems.append(f"outputs 缺少键 {missing}（实际 {sorted(result.outputs)}）")
    for key, pattern in (expect.get("outputsMatch") or {}).items():
        actual = result.outputs.get(key)
        if not isinstance(actual, str) or re.fullmatch(str(pattern), actual) is None:
            problems.append(
                f"outputsMatch.{key}: 期望匹配 /{pattern}/，实得 {actual!r}"
            )
    for key, expected_path in (expect.get("outputPaths") or {}).items():
        actual_path = result.outputs.get(key)
        wanted = Path(str(materialize_inputs(str(expected_path), tmp_dir)))
        if actual_path is None:
            problems.append(
                f"outputPaths.{key}: outputs 里没有这个键（实际 {sorted(result.outputs)}）"
            )
        elif Path(str(actual_path)) != wanted:
            problems.append(f"outputPaths.{key}: 期望 {wanted}，实得 {actual_path!r}")
    if "effect" in expect:
        kinds = [effect.kind.value for effect in result.effects]
        if not kinds:
            problems.append(f"期望 effect={expect['effect']}，但没有任何 effect 记录")
        elif kinds[0] != expect["effect"]:
            problems.append(f"effect：期望 {expect['effect']!r}，实得 {kinds[0]!r}")
    if "effectDetails" in expect:
        if not result.effects:
            problems.append("期望 effectDetails，但没有任何 effect 记录")
        else:
            problems.extend(
                subset_problems(
                    result.effects[0].details or {},
                    expect["effectDetails"],
                    "effectDetails",
                )
            )
    if expect.get("noEffects"):
        kinds = [effect.kind.value for effect in result.effects]
        if kinds:
            problems.append(f"期望不产生任何 effect（pure），实得 {kinds}")
    if "elapsedAtLeastMs" in expect and elapsed_ms < float(expect["elapsedAtLeastMs"]):
        problems.append(
            f"耗时下界：期望 ≥{expect['elapsedAtLeastMs']}ms，实得 {elapsed_ms:.1f}ms"
        )

    # -- 磁盘面 --------------------------------------------------------------
    problems.extend(_check_files(expect, tmp_dir))

    return problems


def scripted_error(spec: dict[str, Any] | None, factory) -> dict[str, Exception]:
    """把 `errors` 声明（`op → {code, message}`）转成异常表。

    `factory` 是构造异常的工厂（浏览器驱动传 `ExtensionChannelError`）；
    数据通道没有通道错误可造（它是真子进程），不使用本函数。
    """
    errors: dict[str, Exception] = {}
    for key, body in (spec or {}).items():
        errors[key] = factory(
            str(body.get("code") or "EXECUTOR_FAILED"),
            str(body.get("message") or "stubbed failure"),
        )
    return errors


__all__ = [
    "CASES_DIR",
    "check_call",
    "check_expect",
    "check_list_contains",
    "iter_variants",
    "load_cases",
    "materialize_inputs",
    "namespace_of",
    "prepare_setup",
    "scripted_error",
    "subset_problems",
    "variant_marks",
]
