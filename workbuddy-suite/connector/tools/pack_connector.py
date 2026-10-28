"""校验并按开放平台规范打包 CLI + Skill 连接器。

规范来源（2026-10 拉取 `open.workbuddy.cn/docs/connector`）：

    your-cli-connector/
    ├── connector-meta.json   # 必须，type 为 cli
    ├── cli.json              # 必须，init / auth / unAuth / status
    ├── icon.svg              # 必须
    └── skills/{name}/SKILL.md

这份脚本不是「打 zip 就完事」：它把审核会看的点全变成断言 ——
少一个平台的 init、statusMatch 写成空、示例只有 1 条、图标不是 64 见方、
SKILL.md 缺 display_name、能力文档与当前 catalog 不同代，统统当场退出码 1。

跑法：
    python workbuddy-connector/tools/pack_connector.py            # 校验 + 打包
    python workbuddy-connector/tools/pack_connector.py --check    # 只校验
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

CONNECTOR = Path(__file__).resolve().parents[1]
REPO = CONNECTOR.parents[1]  # rpa_core 根（connector 现在挂在 workbuddy-suite/ 下）
SKILLS = CONNECTOR / "skills"
DIST = CONNECTOR / "dist"
PLATFORMS = ("darwin", "linux", "win32")
SKILL_REQUIRED = (
    "name",
    "display_name",
    "display_name_en",
    "description",
    "description_zh",
    "description_en",
    "version",
    "author",
)
META_REQUIRED = (
    "name",
    "name_en",
    "description",
    "description_zh",
    "description_en",
    "source",
    "version",
)
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SECRET = re.compile(r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})")

problems: list[str] = []
notes: list[str] = []


def fail(message: str) -> None:
    problems.append(message)


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        fail("SKILL.md 没有 YAML frontmatter（--- 开头）")
        return {}
    head = text.split("---", 2)[1]
    data: dict[str, str] = {}
    for line in head.splitlines():
        if not line.strip() or line.lstrip().startswith("-") or line.startswith(" "):
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip().strip('"')
    return data


def check_meta() -> dict:
    path = CONNECTOR / "connector-meta.json"
    if not path.is_file():
        fail("缺少 connector-meta.json")
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        fail(f"connector-meta.json 不是合法 JSON：{error}")
        return {}
    for key in META_REQUIRED:
        if not str(meta.get(key, "")).strip():
            fail(f"connector-meta.json 缺字段 {key}")
    if meta.get("type") != "cli":
        fail('connector-meta.json 的 type 必须是 "cli"（CLI 方案硬要求）')
    if not KEBAB.match(str(meta.get("source", ""))):
        fail(f"source 必须是 kebab-case 全局唯一标识，当前 {meta.get('source')!r}")
    if not SEMVER.match(str(meta.get("version", ""))):
        fail(f"version 必须是语义化版本，当前 {meta.get('version')!r}")
    for key in ("examples_zh", "examples_en"):
        examples = meta.get(key) or []
        if not (2 <= len(examples) <= 5):
            fail(f"{key} 需要 2~5 条示例，当前 {len(examples)} 条")
    for key in ("description", "description_zh", "description_en"):
        length = len(str(meta.get(key, "")))
        if not 15 <= length <= 160:
            fail(f"{key} 长度 {length} 不在 15~160 之间（市场详情页会被截断）")
    if "minWorkbuddyVersion" not in meta:
        fail("声明了 runtime 等新字段，必须同时声明 minWorkbuddyVersion")
    return meta


def check_cli() -> dict:
    path = CONNECTOR / "cli.json"
    if not path.is_file():
        fail("缺少 cli.json")
        return {}
    try:
        cli = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        fail(f"cli.json 不是合法 JSON：{error}")
        return {}
    runtime = cli.get("runtime") or {}
    if runtime.get("type") not in {"python", "node"}:
        fail("cli.json 的 runtime.type 只能是 python 或 node")
    for section in ("init", "auth", "unAuth", "status"):
        block = cli.get(section) or {}
        for platform in PLATFORMS:
            if not str(block.get(platform, "")).strip():
                fail(f"cli.json 的 {section}.{platform} 为空（至少要 macOS/Linux，建议含 Windows）")
    match = str(cli.get("statusMatch", "")).strip()
    if not match:
        fail("cli.json 缺 statusMatch / statusMatchJson")
    else:
        try:
            re.compile(match)
        except re.error as error:
            fail(f"statusMatch 不是合法正则：{error}")
    joined = json.dumps(cli, ensure_ascii=False)
    if SECRET.search(joined):
        fail("cli.json 里出现了疑似真实密钥")
    for platform in PLATFORMS:
        command = str((cli.get("init") or {}).get(platform, ""))
        if "pip install" in command and runtime.get("type") != "python":
            fail(f"{platform} 用 pip 安装但 runtime.type 不是 python")
        if "npm install" in command and runtime.get("type") != "node":
            fail(f"{platform} 用 npm 安装但 runtime.type 不是 node")
    return cli


def check_icon() -> None:
    path = CONNECTOR / "icon.svg"
    if not path.is_file():
        fail("缺少 icon.svg（市场图标必须）")
        return
    text = path.read_text(encoding="utf-8")
    width = re.search(r'width="(\d+)"', text)
    height = re.search(r'height="(\d+)"', text)
    if not width or not height:
        fail("icon.svg 必须有显式 width/height（否则市场里尺寸不确定）")
    elif width.group(1) != height.group(1):
        fail(f"icon.svg 不是正方形：{width.group(1)}×{height.group(1)}")


def check_skill() -> None:
    skill_dirs = [item for item in SKILLS.iterdir() if item.is_dir()] if SKILLS.is_dir() else []
    if not skill_dirs:
        fail("skills/ 下没有任何技能目录")
        return
    for directory in skill_dirs:
        skill_file = directory / "SKILL.md"
        if not skill_file.is_file():
            fail(f"{directory.name}/ 缺 SKILL.md")
            continue
        text = skill_file.read_text(encoding="utf-8")
        meta = frontmatter(text)
        for key in SKILL_REQUIRED:
            if not meta.get(key):
                fail(f"{directory.name}/SKILL.md 缺 frontmatter 字段 {key}")
        if meta.get("name") and meta["name"] != directory.name:
            fail(f"{directory.name}/SKILL.md 的 name={meta['name']} 与目录名不一致")
        triggers = meta.get("description", "")
        if "触发" not in triggers:
            fail(f"{directory.name}/SKILL.md 的 description 没写触发词")
        for relative in re.findall(r"`references/([\w.-]+)`", text):
            if not (directory / "references" / relative).is_file():
                fail(f"{directory.name}/SKILL.md 引用了不存在的 references/{relative}")
        if SECRET.search(text):
            fail(f"{directory.name}/SKILL.md 里出现了疑似真实密钥")


def check_catalog_alignment(meta: dict) -> None:
    """能力文档必须与当前 manifest 同代，否则 AI 会照着不存在的能力写流程。"""
    sys.path.insert(0, str(REPO / "src"))
    from rpa_core.catalog import load_catalog

    snapshot_path = SKILLS / "rpa-automation" / "references" / "catalog.json"
    if not snapshot_path.is_file():
        fail("缺 references/catalog.json（先跑一次 gen_capability_reference.py）")
        return
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    catalog = load_catalog(REPO / "commands")
    if snapshot.get("digest") != catalog.digest:
        fail(
            "能力文档与当前 catalog 不同代："
            f"文档 {str(snapshot.get('digest'))[:12]}… / 代码 {catalog.digest[:12]}… "
            "（重跑 gen_capability_reference.py）"
        )
        return
    if snapshot.get("count") != len(catalog):
        fail(f"catalog.json 计数 {snapshot.get('count')} ≠ 实际 {len(catalog)}")
        return
    documented = {item["id"] for item in snapshot.get("commands", [])}
    missing = sorted(set(catalog) - documented)
    if missing:
        fail(f"有 {len(missing)} 条命令没进文档：{missing[:5]}")
    notes.append(f"能力文档覆盖 {len(catalog)} 条命令，digest 一致")

    # 技能正文里提到的命令 id 必须真实存在——手写示例最容易在这里说漏嘴
    skill_text = (SKILLS / "rpa-automation" / "SKILL.md").read_text(encoding="utf-8")
    mentioned = set(
        re.findall(
            r"`((?:browser|desktop|desktop\.win32|data|ocr|workflow)\.[A-Za-z]\w*)`", skill_text
        )
    )
    # `workflow.json` 这类文件名也会被上面的正则捞进来，按扩展名排除，别把它当命令报错
    mentioned = {item for item in mentioned if not item.endswith((".json", ".md", ".py", ".txt"))}
    unknown = sorted(mentioned - set(catalog))
    if unknown:
        fail(f"SKILL.md 提到了不存在的命令：{unknown}")
    notes.append(f"SKILL.md 提到的 {len(mentioned)} 条命令全部存在")

    if meta:
        declared = str(meta.get("description_zh", ""))
        count = len(catalog)
        if str(count) not in declared and str(count) not in str(meta.get("description_en", "")):
            notes.append(f"提醒：connector-meta 描述里没写命令数（当前 {count} 条）")


def build_zip() -> Path:
    DIST.mkdir(exist_ok=True)
    target = DIST / "rpa-core-connector.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in ("connector-meta.json", "cli.json", "icon.svg"):
            bundle.write(CONNECTOR / name, name)
        for path in sorted(SKILLS.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                bundle.write(path, f"skills/{path.relative_to(SKILLS).as_posix()}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="校验并打包 WorkBuddy CLI 连接器")
    parser.add_argument("--check", action="store_true", help="只校验，不写 zip")
    args = parser.parse_args()

    meta = check_meta()
    check_cli()
    check_icon()
    check_skill()
    check_catalog_alignment(meta)

    if problems:
        print(f"{len(problems)} 项不合规：")
        for item in problems:
            print(f"  - {item}")
        return 1

    for note in notes:
        print(f"[OK] {note}")
    if args.check:
        print("连接器结构符合开放平台规范（未打包）")
        return 0
    target = build_zip()
    size = target.stat().st_size
    print(f"{target.relative_to(CONNECTOR).as_posix()}  {size / 1024:.1f} KB")
    with zipfile.ZipFile(target) as bundle:
        for name in bundle.namelist()[:8]:
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
