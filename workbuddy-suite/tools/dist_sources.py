"""上架包「zip 内路径 → 源文件」的唯一映射。

三个消费者共用它，避免「打包逻辑」与「校验逻辑」各写一份映射：

  · tools/pack.py                     按映射写技能包 / 扩展包；
  · connector/tools/pack_connector.py 按映射写连接器包；
  · verify/verify_suite.py            按同一映射逐文件比哈希。

判据不是「zip 存在」也不是「入口层级对」——那些只能证明包能解析，
证明不了包里装的是**当前**源码。旧包一直能过结构检查，正是这种假绿灯。
"""

from __future__ import annotations

from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
REPO = SUITE.parent
SKILL = SUITE / "skills" / "workflow-studio"
EXT = SUITE / "extension"
CONNECTOR = SUITE / "connector"
DIST = SUITE / "dist"
CONNECTOR_DIST = CONNECTOR / "dist"
CONNECTOR_SKILL = "rpa-core-cli"
CONNECTOR_TOP = ("connector-meta.json", "cli.json", "icon.svg")
CAPABILITY_REFS = (
    "catalog.json",
    "commands-index.md",
    "commands-reference.md",
    "error-codes.md",
    "workflow-format.md",
)

SKIP_PARTS = {"__pycache__", ".pytest_cache", ".DS_Store"}
SKIP_SUFFIX = {".pyc", ".pyo"}


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIX:
            continue
        yield path


def skill_sources(layout: str = "folder") -> dict[str, Path]:
    prefix = "" if layout == "flat" else f"{SKILL.name}/"
    return {
        f"{prefix}{path.relative_to(SKILL).as_posix()}": path
        for path in iter_files(SKILL)
    }


def extension_sources() -> dict[str, Path]:
    return {
        path.relative_to(EXT).as_posix(): path for path in iter_files(EXT)
    }


def connector_sources() -> dict[str, Path]:
    sources = {name: CONNECTOR / name for name in CONNECTOR_TOP}
    skills = CONNECTOR / "skills"
    for path in iter_files(skills):
        sources[f"skills/{path.relative_to(skills).as_posix()}"] = path
    # 能力参考只从唯一的技能源复制，连接器包里不留第二份手工副本。
    for name in CAPABILITY_REFS:
        sources[f"skills/{CONNECTOR_SKILL}/references/{name}"] = (
            SKILL / "references" / name
        )
    return sources
