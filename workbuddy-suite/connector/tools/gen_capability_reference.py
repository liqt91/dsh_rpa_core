"""把 `commands/**/*.json` 生成成 WorkBuddy 能读懂的原子能力清单。

为什么要有这个脚本，而不是手写一份文档：

- 命令目录已经有 89 条，手写必然漂移（上一份手写参考停在 26 条，页面还写着「26 条」）；
- 「AI 该在什么时候调哪条」的前提是它先**看见全部命令和参数**，而不是看见一份过期的摘要；
- 漂移必须能被门禁抓住，所以本脚本带 `--check`：磁盘文档与当前 manifest 不一致就退出码 1。

产物（写进唯一的技能源 `workbuddy-suite/skills/workflow-studio/references/`；
连接器包在打包时从这里复制，不再维护第二份）：

  commands-index.md      一行一条，给 Agent 做「先扫一眼」的目录
  commands-reference.md  逐条：参数 / 默认值 / 输出 / 失败码 / 重放语义
  catalog.json           机器可读快照（id 列表 + digest + 计数），给门禁与自检用

中文名与字段标签不在这里发明：一律取 `rpa_core.gui.i18n`（`devserver/static/i18n.js`
是全仓唯一中文化事实源）。缺标签时如实回退英文 id，不编。

跑法：
    python workbuddy-connector/tools/gen_capability_reference.py
    python workbuddy-connector/tools/gen_capability_reference.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CONNECTOR = Path(__file__).resolve().parents[1]
REPO = CONNECTOR.parents[1]  # rpa_core 根（connector 现在挂在 workbuddy-suite/ 下）
COMMANDS = REPO / "commands"
OUT = CONNECTOR.parent / "skills" / "workflow-studio" / "references"

sys.path.insert(0, str(REPO / "src"))

from rpa_core.catalog import load_catalog  # noqa: E402
from rpa_core.gui.i18n import field_label, load_command_display_names  # noqa: E402

GROUPS: list[tuple[str, str]] = [
    ("browser.", "浏览器"),
    ("desktop.win32.", "桌面（Win32）"),
    ("desktop.", "桌面（UIA）"),
    ("data.", "数据 / 文件"),
    ("ocr.", "OCR / 验证码"),
    ("workflow.", "工作流控制"),
]

GROUP_HINT = {
    "浏览器": "网页上的点击、输入、取值、截图、Cookie、下载、多标签。每条命令都要带 `sessionId`。",
    "桌面（Win32）": "按窗口/控件句柄直接操作，适合标准 Win32 控件与被 UIA 挡住的场景。",
    "桌面（UIA）": "按 UI Automation 语义操作，适合现代桌面应用（记事本、Office 等）。",
    "数据 / 文件": "不碰界面的纯数据步骤：读写文件、格式化、时间、路径、JSON/CSV。",
    "OCR / 验证码": "截图识别、滑块/点选类验证码。慢且依赖外部引擎，能不用就不用。",
    "工作流控制": "子流程、重试、等待、断言这类编排原语。",
}

# 参数说明里出现这些词，就该在调用前跟用户确认（不可逆 / 花钱 / 动别人数据）
EFFECT_LABEL = {
    "read": "只读",
    "pure": "纯函数",
    "idempotent-write": "可重复写",
    "unsafe-write": "不可重复写",
    "session": "会话",
}

# 参数说明里出现这些词，就该在调用前跟用户确认（不可逆 / 花钱 / 动别人数据）
RISKY_HINTS = ("删除", "覆盖", "上传", "下载", "提交", "支付", "发送")


def group_of(command_id: str) -> str:
    for prefix, name in GROUPS:
        if command_id.startswith(prefix):
            return name
    return "其他"


def _type_of(prop: dict[str, Any]) -> str:
    kind = prop.get("type")
    if kind == "array":
        inner = prop.get("items", {})
        return f"array<{inner.get('type', 'any')}>"
    if kind is None:
        return "any"
    if prop.get("enum"):
        return f"{kind} enum"
    return kind


def _enum_of(prop: dict[str, Any]) -> str:
    values = prop.get("enum")
    if not values:
        return ""
    labels = prop.get("x-enum-labels") or {}
    return " / ".join(
        f"`{value}`" + (f"（{labels[value]}）" if value in labels else "") for value in values
    )


def _default_of(prop: dict[str, Any]) -> str:
    if "default" not in prop:
        return "—"
    value = prop["default"]
    if isinstance(value, bool):
        return "true" if value else "false"
    if value == []:
        return "`[]`"
    return f"`{value}`" if isinstance(value, str) else str(value)


def _one_line(text: str, limit: int = 220) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def render(catalog: Any) -> tuple[str, str, dict[str, Any]]:
    names = load_command_display_names()
    index_rows: list[str] = []
    detail: list[str] = []
    ids = sorted(catalog)

    by_group: dict[str, list[str]] = {}
    for command_id in ids:
        by_group.setdefault(group_of(command_id), []).append(command_id)

    index_rows.append(f"# 原子能力目录（{len(ids)} 条 = 当前 catalog 全量）\n")
    index_rows.append(
        f"> 由 `commands/**/*.json` 生成，catalog digest `{catalog.digest[:16]}…`。\n"
        "> **先跑 `rpa-core catalog` 对齐版本**：digest 不一致就说明本机 CLI 与本表不同代，\n"
        "> 以本机输出为准，不要照表硬调。\n"
    )
    for _prefix, group in GROUPS:
        members = by_group.get(group)
        if not members:
            continue
        index_rows.append(f"\n## {group}（{len(members)} 条）\n")
        index_rows.append(f"{GROUP_HINT[group]}\n")
        index_rows.append("| 命令 | 中文名 | 副作用 | 必填参数 |")
        index_rows.append("|---|---|---|---|")
        for command_id in members:
            manifest = catalog[command_id]
            required = manifest.input_schema.get("required") or []
            required_text = "、".join(f"`{key}`" for key in required) or "—"
            effect = EFFECT_LABEL.get(manifest.effect.kind, manifest.effect.kind)
            index_rows.append(
                f"| `{command_id}` | {names.get(command_id, command_id)} "
                f"| {effect} | {required_text} |"
            )

    detail.append(f"# 命令参考（{len(ids)} 条，完整契约）\n")
    detail.append(
        f"catalog digest `{catalog.digest}`。本文件由 `tools/gen_capability_reference.py` 生成，\n"
        "**不要手改**（改了会被 `--check` 与门禁抓住）。语义级 schema 以\n"
        "`rpa-core catalog` 输出的 JSON Schema 为准；本表负责「有哪条、要传什么、\n"
        "会返回什么、会怎么失败」。\n"
    )

    for _prefix, group in GROUPS:
        members = by_group.get(group)
        if not members:
            continue
        detail.append(f"\n## {group}\n")
        for command_id in members:
            manifest = catalog[command_id]
            props = manifest.input_schema.get("properties") or {}
            required = set(manifest.input_schema.get("required") or [])
            detail.append(f"### `{command_id}` · {names.get(command_id, command_id)}\n")
            effect = manifest.effect
            detail.append(
                f"- 执行器 `{manifest.executor}` · {manifest.kind.value}"
                f" · 风险 `{manifest.risk.value}`\n"
                f"- 重放 `{effect.replay}` · 幂等 `{effect.idempotency}`"
                f" · 超时 {manifest.default_timeout_seconds:g}s"
                f" · {'可重试' if manifest.retryable else '不可重试'}\n"
            )
            if not props:
                detail.append("\n无入参。\n")
            else:
                detail.append("\n| 参数 | 类型 | 必填 | 默认 | 说明 |")
                detail.append("|---|---|---|---|---|")
                for key, prop in props.items():
                    label = field_label(key, command_id)
                    note = _one_line(prop.get("description", ""))
                    extra = _enum_of(prop)
                    if extra:
                        note = f"{note} 取值：{extra}".strip()
                    detail.append(
                        f"| `{key}`（{label}） | {_type_of(prop)} "
                        f"| {'是' if key in required else '否'} "
                        f"| {_default_of(prop)} | {note or '—'} |"
                    )
                detail.append("")
            outputs = manifest.output_schema.get("properties") or {}
            if outputs:
                out_labels = manifest.x_outputs or {}
                rendered = []
                for key in outputs:
                    label = ""
                    if key in out_labels:
                        label = getattr(out_labels[key], "label", "") or ""
                    rendered.append(f"`{key}`" + (f"（{label}）" if label else ""))
                detail.append(f"**输出**：{'、'.join(rendered)}\n")
            if manifest.errors:
                detail.append(f"**失败码**：{', '.join(f'`{code}`' for code in manifest.errors)}\n")
            risky = [
                field_label(key, command_id)
                for key, prop in props.items()
                if any(hint in str(prop.get("description", "")) for hint in RISKY_HINTS)
            ]
            if manifest.risk.value in {"unsafe-write", "external-side-effect"} or risky:
                detail.append(
                    "**调用前**：这条会改外部世界（"
                    + ("、".join(risky) if risky else manifest.risk.value)
                    + "），先把目标与后果说清楚，拿到用户明确同意再执行。\n"
                )

    snapshot = {
        "generated_by": "workbuddy-connector/tools/gen_capability_reference.py",
        "digest": catalog.digest,
        "count": len(ids),
        "groups": {group: len(members) for group, members in by_group.items()},
        "commands": [
            {
                "id": command_id,
                "label": names.get(command_id, command_id),
                "group": group_of(command_id),
                "required": list(catalog[command_id].input_schema.get("required") or []),
                "risk": catalog[command_id].risk.value,
            }
            for command_id in ids
        ],
    }
    return "\n".join(index_rows) + "\n", "\n".join(detail) + "\n", snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 WorkBuddy 原子能力参考")
    parser.add_argument("--check", action="store_true", help="只校验磁盘文档是否与 manifest 一致")
    args = parser.parse_args()

    catalog = load_catalog(COMMANDS)
    index_md, detail_md, snapshot = render(catalog)
    files = {
        OUT / "commands-index.md": index_md,
        OUT / "commands-reference.md": detail_md,
        OUT / "catalog.json": json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
    }

    if args.check:
        stale = []
        for path, expected in files.items():
            actual = path.read_text(encoding="utf-8") if path.is_file() else ""
            if actual.replace("\r\n", "\n") != expected:
                stale.append(path.relative_to(CONNECTOR).as_posix())
        if stale:
            print("文档与 manifest 不一致（跑一次不带 --check 的生成）：")
            for name in stale:
                print(f"  - {name}")
            return 1
        print(
            f"reference 与 catalog 一致：{len(snapshot['commands'])} 条，"
            f"digest {snapshot['digest'][:16]}…"
        )
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        print(
            f"wrote {path.relative_to(CONNECTOR).as_posix()}  {len(content.encode('utf-8'))} bytes"
        )
    print(f"{len(snapshot['commands'])} 条命令 · 分组 {snapshot['groups']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
