"""RPC 线上参数契约：main.js 里每一处 ``ppx.call('rpa.X', {...})`` 的参数键，
必须能按 api.py 的 handler 签名**绑定**（桥层 ``inspect.signature.bind`` 是严格匹配）。

2026-11-12 真机事故：前端发 ``{path, captureId}``，后端参数叫 ``capture_id``，
桥层回 INVALID_PARAMS ⇒ ``ppx.call`` 抛异常 ⇒ 前端轮询的 catch 静默收尾——
捕获明明已经 ``result_received`` 并暂存，用户却看不到编辑框、资产也没新增。
当时所有判据都绿：Python 测试用 snake_case 直连 handler、DOM shim 用假 ``ppx``——
**没有一条走「真实参数名」这条路**。本检查就是那道缺的闸：静态抽取
``ppx.call('字面量方法', {字面量对象})`` 的顶层键，与 AST 解析出的签名做
「键 ⊆ 参数 且 必填参数 ⊆ 键」的双向校验。动态方法/动态参数的调用单列计数、
不强判（它们的键在运行期才有意义）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "api" / "api.py"
MAIN = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "gui" / "src" / "main.js"

# 这些方法不在原型 api.py 注册（宿主/桥层提供，失败被前端显式容忍），不纳入校验
_HOST_METHODS = {"window.restore", "window.minimize"}


def _api_signatures() -> dict[str, dict]:
    """@api_method("rpa.X") → {参数名集合, 必填集合, 有 **kwargs}。"""
    tree = ast.parse(API.read_text(encoding="utf-8"))
    table: dict[str, dict] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if not (
                isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Name)
                and deco.func.id == "api_method"
                and deco.args
                and isinstance(deco.args[0], ast.Constant)
                and isinstance(deco.args[0].value, str)
            ):
                continue
            args = node.args
            names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
            with_default: set[str] = set()
            if args.defaults:  # ★ args.args[-0:] 是全表不是空表，必须先判空
                with_default = {a.arg for a in args.args[-len(args.defaults):]}
            with_default |= {
                a.arg
                for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)
                if d is not None
            }
            table[deco.args[0].value] = {
                "params": set(names),
                "required": set(names) - with_default,
                "varkw": args.kwarg is not None,
            }
    return table


# -- main.js 静态抽取（够用即可：字符串/模板/括号配平，不追求完整 JS 语法） ------

_WS = " \t\r\n"


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in _WS:
        i += 1
    return i


def _skip_string(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    raise AssertionError(f"main.js 字符串没闭合（offset {i}）")


def _find_matching(text: str, open_pos: int) -> int:
    """从 ``{`` 位置找配平的 ``}``，返回 ``}`` 的**下一个**位置。"""
    depth = 0
    i = open_pos
    while i < len(text):
        c = text[i]
        if c in "'\"":
            i = _skip_string(text, i)
            continue
        if c == "`":
            i = _skip_template(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise AssertionError(f"main.js 括号没配平（offset {open_pos}）")


def _skip_template(text: str, i: int) -> int:
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "`":
            return i + 1
        if text.startswith("${", i):
            i = _find_matching(text, i + 1)
            continue
        i += 1
    raise AssertionError("main.js 模板字符串没闭合")


def _read_string(text: str, i: int) -> tuple[str, int]:
    end = _skip_string(text, i)
    return text[i + 1 : end - 1], end


def _read_object(text: str, i: int) -> tuple[str, int]:
    end = _find_matching(text, i)
    return text[i + 1 : end - 1], end


def _skip_value(body: str, i: int) -> int:
    """跳过一个值，停在顶层 ``,`` 或本层 ``}`` 前。"""
    depth = 0
    while i < len(body):
        c = body[i]
        if c in "'\"":
            i = _skip_string(body, i)
            continue
        if c == "`":
            i = _skip_template(body, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return i
            depth -= 1
        elif c == "," and depth == 0:
            return i
        i += 1
    return i


_KEY = re.compile(r"[A-Za-z_$][\w$]*")


def _top_keys(body: str) -> list[str] | None:
    """顶层键列表；遇 spread / 计算键 / 解析不了返回 None（该条放弃静态判定）。"""
    keys: list[str] = []
    i = 0
    while True:
        i = _skip_ws(body, i)
        if i >= len(body):
            return keys
        if body.startswith("...", i):
            return None
        if body[i] in "'\"":
            key, i = _read_string(body, i)
        else:
            match = _KEY.match(body, i)
            if match is None:
                return None
            key = match.group(0)
            i = match.end()
        keys.append(key)
        i = _skip_ws(body, i)
        if i < len(body) and body[i] == ":":
            i = _skip_value(body, i + 1)
        i = _skip_ws(body, i)
        if i < len(body) and body[i] == ",":
            i += 1
            continue
        return keys


def _js_calls(text: str) -> list[tuple[int, str | None, list[str] | None]]:
    """(行号, 字面量方法|None, 字面量顶层键|None)。动态方法/参数为 None。"""
    out: list[tuple[int, str | None, list[str] | None]] = []
    i = 0
    while True:
        j = text.find("ppx.call(", i)
        if j == -1:
            return out
        line = text.count("\n", 0, j) + 1
        k = _skip_ws(text, j + len("ppx.call("))
        method: str | None = None
        if k < len(text) and text[k] in "'\"":
            method, k = _read_string(text, k)
        keys: list[str] | None = None
        k = _skip_ws(text, k)
        if k < len(text) and text[k] == ",":
            k = _skip_ws(text, k + 1)
            if k < len(text) and text[k] == "{":
                body, k = _read_object(text, k)
                keys = _top_keys(body)
        out.append((line, method, keys))
        i = max(k, j + 1)


def main() -> int:
    signatures = _api_signatures()
    if not signatures:
        print("RPC WIRE CHECK FAILED: api.py 里没有解析到任何 @api_method 注册")
        return 1
    text = MAIN.read_text(encoding="utf-8")
    errors: list[str] = []
    checked = 0
    skipped_dynamic: list[int] = []
    host_only = 0
    for line, method, keys in _js_calls(text):
        if method is None or keys is None:
            skipped_dynamic.append(line)
            continue
        if not method.startswith("rpa."):
            host_only += 1
            continue
        checked += 1
        signature = signatures.get(method)
        if signature is None:
            errors.append(f"main.js:{line} {method} 未在 api.py 注册（调用必 METHOD_NOT_FOUND）")
            continue
        key_set = set(keys)
        unknown = key_set - signature["params"]
        if unknown and not signature["varkw"]:
            errors.append(
                f"main.js:{line} {method} 的参数键 {sorted(unknown)} 不在签名"
                f" {sorted(signature['params'])} 里——桥层严格绑定，键名必须与参数名"
                "逐字一致（仓内约定 snake_case）"
            )
        missing = signature["required"] - key_set
        if missing and not signature["varkw"]:
            errors.append(
                f"main.js:{line} {method} 少了必填参数 {sorted(missing)}（桥层 INVALID_PARAMS）"
            )
    if errors:
        print("RPC WIRE CHECK FAILED")
        for item in errors:
            print("-", item)
        return 1
    print(
        f"RPC WIRE CHECK PASSED（api.py 注册 {len(signatures)} 个方法；"
        f"main.js 字面量调用校验 {checked} 处、宿主方法 {host_only} 处、"
        f"动态方法/参数跳过 {len(skipped_dynamic)} 处：行 {skipped_dynamic}）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
