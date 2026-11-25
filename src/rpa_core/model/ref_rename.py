"""变量改名：把流程里对某个变量的**引用**整体换成新名字。

存在意义：输出别名（``output_aliases``）是跨节点传值的唯一稳定入口，可它一旦改名，
全流程里所有 ``${旧名}`` / ``[旧名]`` 就全成了悬空引用——编译器会报
``Unknown reference``，而用户看到的是「我只是改了个变量名，流程图就红了」。
改名联动就是让「改名字」这个动作自带引用迁移。

引用的语法事实源只有两处，本模块也不另立一套：

* ``${name}`` / ``${name.sub}``——见 ``runtime/resolver.py`` 的 ``_REFERENCE``；
* ``[name]`` / ``[name.sub]``——见 ``model/expr_tags.py`` 的 ``TAG_PATTERN``。

两类**不是**等价的两种写法，而是两种模式：

* 普通模式下 ``${...}`` 只在**整串**匹配时被替换；``[name]`` 就是字面文本，
  改了等于篡改用户数据，所以 ``tags=False`` 时一个方括号都不碰。
* fx 模式下 ``[name]`` 可混排拼接；而整串 ``${...}`` 也被 ``resolve_tags`` 特判处理
  （整串时保留原类型），所以 fx 字段两种语法都要改。

Python 模式字段（``_exprModes[字段] == "python"``）里的引用是**裸标识符**：运行期把
``scopes["variables"]`` 摊成同名字符串喂给 worker 子进程，所以 ``total = sum(rows)`` 里
的 ``rows`` 也得跟着改。这里按 ``tokenize`` 的 NAME token 改，字符串与注释不碰——
用正则改 ``rows`` 会把 ``"rows"`` 这种字面量也改掉。
"""

from __future__ import annotations

import io
import re
import tokenize
from typing import Any

from rpa_core.model.expr_tags import TAG_PATTERN

# 与 resolver._REFERENCE 同形，但**不去锚定整串**：这里要找的是文本里出现的引用，
# 不是判断「这整串是不是一个引用」。两处口径必须一致，改一处就要改另一处。
REF_PATTERN = r"\$\{([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\}"

_TAG = re.compile(TAG_PATTERN)
_REF = re.compile(REF_PATTERN)


def _swap_head(path: str, renames: dict[str, str]) -> str:
    """把引用路径的**根名**换成新名，子路径原样保留（``old.k`` → ``new.k``）。"""
    head, dot, tail = path.partition(".")
    new = renames.get(head)
    if new is None:
        return path
    return f"{new}{dot}{tail}"


def rename_text(value: str, renames: dict[str, str], *, tags: bool) -> tuple[str, int]:
    """改一段文本里的引用，返回 ``(新文本, 命中数)``。空表/无命中时原样返回。"""
    if not value or not renames:
        return value, 0
    hits = 0

    def _ref(match: re.Match[str]) -> str:
        nonlocal hits
        path = match.group(1)
        new = _swap_head(path, renames)
        if new == path:
            return match.group(0)
        hits += 1
        return "${" + new + "}"

    out = _REF.sub(_ref, value)
    if tags:

        def _tag(match: re.Match[str]) -> str:
            nonlocal hits
            path = match.group(1)
            new = _swap_head(path, renames)
            if new == path:
                return match.group(0)
            hits += 1
            return "[" + new + "]"

        out = _TAG.sub(_tag, out)
    return out, hits


def rename_value(value: Any, renames: dict[str, str], *, tags: bool) -> tuple[Any, int]:
    """递归改 list/dict 里的每一段文本（``with`` 的值可以是任意 JSON）。"""
    if isinstance(value, str):
        return rename_text(value, renames, tags=tags)
    if isinstance(value, list):
        hits = 0
        out: list[Any] = []
        for item in value:
            new, count = rename_value(item, renames, tags=tags)
            hits += count
            out.append(new)
        return out, hits
    if isinstance(value, dict):
        hits = 0
        out_map: dict[Any, Any] = {}
        for key, item in value.items():
            new, count = rename_value(item, renames, tags=tags)
            hits += count
            out_map[key] = new
        return out_map, hits
    return value, 0


def rename_python(source: str, renames: dict[str, str]) -> tuple[str, int] | None:
    """按 NAME token 改 Python 源码里的裸标识符，返回 ``(新源码, 命中数)``。

    源码读不出 token（语法错 / 未闭合）时返回 ``None``——**不猜**：这种情况下改与不改
    都可能把用户本来能跑的一格写坏，交给调用方如实上报「这一格没改，请手动处理」。
    """
    if not source.strip() or not renames:
        return source, 0
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None

    # 按行收集 (起列, 止列, 新名)，同行从右往左替换，避免列号偏移。
    edits: dict[int, list[tuple[int, int, str]]] = {}
    for token in tokens:
        if token.type != tokenize.NAME:
            continue
        new = renames.get(token.string)
        if new is None:
            continue
        row, col = token.start
        end_row, end_col = token.end
        if row != end_row:  # NAME 不可能跨行，出现就说明口径变了，不碰
            continue
        edits.setdefault(row, []).append((col, end_col, new))
    if not edits:
        return source, 0

    lines = source.split("\n")
    hits = 0
    for row, items in edits.items():
        line = lines[row - 1]
        for col, end_col, new in sorted(items, reverse=True):
            line = line[:col] + new + line[end_col:]
            hits += 1
        lines[row - 1] = line
    return "\n".join(lines), hits
