"""把三件套打成可上传的 zip —— 一条命令产出上架需要的全部文件。

产物：

    dist/workflow-studio-skill.zip        技能包（open.workbuddy.cn/docs/skill）
    dist/rpa-core-connector.zip           连接器（CLI + Skill，docs/connector）
    dist/workbuddy-studio-extension.zip   浏览器扩展（Chrome / Edge，MV3）

技能包布局按**官方文档**：`{skill-name}/SKILL.md`（zip 根下套一层技能目录）。
早先这里是 flat（根下直接 SKILL.md，来自一篇社区过审攻略），但官方「技能基础结构」
写的就是套一层 —— 平台解析器按那个找入口，套错了解析失败。所以默认 folder，
`--layout flat` 只留给「控制台亲测要 flat」的情况。

打包时会顺手做结构断言（不是「文件生成了就算」）：

- 技能包入口必须是 `workflow-studio/SKILL.md`，且 references/scripts 不许有死链；
- 扩展包根必须有 `manifest.json`，且 manifest 引用的文件都在包里；
- 连接器先跑自己的校验器（字段/权限/能力文档 digest），再复制进本目录 dist。

跑法：python tools/pack.py [--layout folder|flat]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
SKILL = SUITE / "skills" / "workflow-studio"
EXT = SUITE / "extension"
CONNECTOR = SUITE / "connector"
DIST = SUITE / "dist"
LIMIT = 3 * 1024 * 1024

SKIP_PARTS = {"__pycache__", ".pytest_cache", ".DS_Store"}
SKIP_SUFFIX = {".pyc", ".pyo"}

problems: list[str] = []


def _rel(path: Path) -> str:
    """打印用路径：DIST 被指到仓库外时（负向验证会这么干）也要能打印，别在格式化上崩。"""
    try:
        return path.relative_to(SUITE).as_posix()
    except ValueError:
        return str(path)


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIX:
            continue
        yield path


def _write(zip_path: Path, root: Path, arc_prefix: str) -> int:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in _iter_files(root):
            arc = f"{arc_prefix}{path.relative_to(root).as_posix()}"
            bundle.write(path, arc)
    return zip_path.stat().st_size


def pack_skill(layout: str) -> Path:
    if not (SKILL / "SKILL.md").is_file():
        problems.append("缺少 skills/workflow-studio/SKILL.md")
        return DIST / "workflow-studio-skill.zip"
    prefix = "" if layout == "flat" else f"{SKILL.name}/"
    target = DIST / "workflow-studio-skill.zip"
    size = _write(target, SKILL, prefix)
    if size > LIMIT:
        problems.append(f"技能包 {size} 字节超过平台 3MB 限制")
    entry = f"{prefix}SKILL.md"
    with zipfile.ZipFile(target) as bundle:
        names = bundle.namelist()
    if entry not in names:
        problems.append(f"技能包根缺少入口 {entry}（当前布局 {layout}）")
    # 正文引用的 references / scripts 必须真的在包里
    body = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for relative in sorted(set(re.findall(r"(?:references|scripts)/[\w.-]+", body))):
        if f"{prefix}{relative}" not in names:
            problems.append(f"SKILL.md 引用了包内不存在的 {relative}")
    print(f"{_rel(target)}  {size / 1024:.1f} KB  layout={layout}")
    return target


def pack_extension() -> Path:
    target = DIST / "workbuddy-studio-extension.zip"
    size = _write(target, EXT, "")
    with zipfile.ZipFile(target) as bundle:
        names = set(bundle.namelist())
        manifest = json.loads(bundle.read("manifest.json")) if "manifest.json" in names else {}
    if "manifest.json" not in names:
        problems.append("扩展包根缺少 manifest.json")
    referenced = [
        manifest.get("background", {}).get("service_worker"),
        manifest.get("action", {}).get("default_popup"),
        *manifest.get("icons", {}).values(),
        *manifest.get("action", {}).get("default_icon", {}).values(),
    ]
    missing = [item for item in referenced if item and item not in names]
    if missing:
        problems.append(f"扩展包内缺文件：{missing}")
    print(f"{_rel(target)}  {size / 1024:.1f} KB")
    return target


def pack_connector() -> Path | None:
    packer = CONNECTOR / "tools" / "pack_connector.py"
    if not packer.is_file():
        problems.append("缺少 connector/tools/pack_connector.py")
        return None
    result = subprocess.run([sys.executable, str(packer)], capture_output=True, text=True)
    if result.returncode != 0:
        problems.append("连接器校验未通过：\n" + (result.stdout or result.stderr).strip())
        return None
    source = CONNECTOR / "dist" / "rpa-core-connector.zip"
    target = DIST / "rpa-core-connector.zip"
    if source.is_file():
        shutil.copyfile(source, target)
        print(f"{_rel(target)}  {target.stat().st_size / 1024:.1f} KB")
        return target
    problems.append("连接器打包后没找到 zip")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 WorkBuddy Studio 三件套")
    parser.add_argument("--layout", choices=("folder", "flat"), default="folder")
    args = parser.parse_args()
    DIST.mkdir(exist_ok=True)

    pack_skill(args.layout)
    pack_extension()
    pack_connector()

    print("\ndist 内容：")
    for path in sorted(DIST.glob("*.zip")):
        print(f"  {path.name}  {path.stat().st_size / 1024:.1f} KB")

    if problems:
        print(f"\n{len(problems)} 个问题：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\n三件套打包完成（结构断言全过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
