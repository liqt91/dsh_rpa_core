"""影刀式层级编辑器的纯函数层：层级模型 + 勾选重组 + 转义。

事实源是 ``extension/content.js::pathEntryFor``——捕获时主 css 与 ``selector.path``
出自同一个片段生成处（每级 ``{tag, id, classes, nthOfType, fragment}``，主 css 就是
fragment 以 ``" > "`` 连接）。本模块是**重组侧**的唯一权威：用户在编辑器里勾掉
层级、改属性、换匹配方式后，新的 css 只能从这里产出，编辑器控件不自己拼字符串。

重组规则：
- 每级按勾选产出片段：id 勾选 ⇒ ``#id``（截断本层其他属性，与捕获侧同口径）；
  否则 ``tag``（或 ``*``）+ 勾选的 class（精确 ``.cls`` / 包含 ``[class*="v"]``）
  + 勾选的位置序号 ``:nth-of-type(n)``；
- 级间连接：原链上**相邻**的两个勾选级用 ``" > "``，中间隔着被勾掉的级用 ``" "``
  （后代）——全部用 ``" > "`` 连接非相邻级会得到匹配不到的死选择器；
- 目标级（链尾）永远参与，不允许勾掉。
"""

from __future__ import annotations

from typing import Any

_SAFE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"  #
)


def css_escape(value: str) -> str:
    """id/class 名进 css 的转义（非安全字符一律反斜杠）。"""
    return "".join(ch if ch in _SAFE else "\\" + ch for ch in str(value))


def attr_escape(value: str) -> str:
    """属性选择器引号内的转义（``\\`` 与 ``"``，与 content.js ``attrValue`` 同口径）。"""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def levels_from_path(path: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """捕获的 ``selector.path`` → 编辑器层级模型（初始勾选与主 css 一致）。

    ``path`` 是**目标 → 页面根**的完整祖先链（M69 起），每层带 ``inCss`` 标记
    它是否参与主 css——初始勾选只开 ``inCss`` 的层（id 祖先往下的短链），
    上面的层展示但默认不参与。旧资产没有 ``inCss`` 时按「全参与」兼容。
    """
    levels: list[dict[str, Any]] = []
    for entry in path:
        ident = entry.get("id")
        classes = list(entry.get("classes") or [])
        nth = entry.get("nthOfType")
        levels.append(
            {
                "tag": str(entry.get("tag") or "*"),
                "included": bool(entry.get("inCss", True)),
                "id": {"on": bool(ident), "value": str(ident or "")},
                "classes": [
                    # 捕获侧片段只取首类：初始勾选与它一致，其余类默认不勾
                    {"on": index == 0, "value": cls, "mode": "精确"}
                    for index, cls in enumerate(classes)
                ],
                "nth": {"on": nth is not None, "value": str(nth or "")},
            }
        )
    return levels


def _fragment_for(level: dict[str, Any]) -> str:
    ident = level["id"]
    if ident["on"] and ident["value"]:
        return "#" + css_escape(ident["value"])
    fragment = level["tag"] or "*"
    for cls in level["classes"]:
        if not cls["on"] or not cls["value"]:
            continue
        if cls["mode"] == "包含":
            fragment += f'[class*="{attr_escape(cls["value"])}"]'
        else:
            fragment += "." + css_escape(cls["value"])
    nth = level["nth"]
    if nth["on"] and nth["value"]:
        fragment += f":nth-of-type({nth['value']})"
    return fragment


def compose(levels: list[dict[str, Any]]) -> str:
    """勾选状态 → css。目标级（链尾）始终参与；空链或全不勾回退目标级片段。"""
    if not levels:
        return ""
    picked = [i for i, level in enumerate(levels) if level["included"]]
    last = len(levels) - 1
    if not picked or picked[-1] != last:
        picked.append(last)
    parts: list[str] = []
    previous: int | None = None
    for index in picked:
        if previous is not None:
            parts.append(" > " if index == previous + 1 else " ")
        parts.append(_fragment_for(levels[index]))
        previous = index
    return "".join(parts)


def summary(level: dict[str, Any]) -> str:
    """层级在树里的展示文案：<tag id="x" class="a b" index="2">。"""
    bits = [level["tag"]]
    if level["id"]["value"]:
        bits.append(f'id="{level["id"]["value"]}"')
    classes = " ".join(cls["value"] for cls in level["classes"] if cls["value"])
    if classes:
        bits.append(f'class="{classes}"')
    if level["nth"]["value"]:
        bits.append(f'index="{level["nth"]["value"]}"')
    return "<" + " ".join(bits) + ">"
