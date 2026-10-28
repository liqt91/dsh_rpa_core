"""把三件套打成可上传的 zip。

两个产物：
  dist/workflow-studio-skill.zip      技能包（≤3MB，平台硬限制）
  dist/workbuddy-studio-extension.zip  浏览器扩展（解压后「加载已解压的扩展」）

关于技能包布局：官方文档写的是 `{skill-name}/SKILL.md`，而实测过审的攻略说
「zip 根目录直接放 SKILL.md，不要套外层文件夹」。两种说法目前并存，所以这里
默认 **flat**（根目录直接 SKILL.md），一个 `--layout folder` 就能切成官方文档那种。

跑法：python tools/pack.py [--layout flat|folder]
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
SKILL = SUITE / "skills" / "workflow-studio"
EXT = SUITE / "extension"
DIST = SUITE / "dist"
LIMIT = 3 * 1024 * 1024

SKIP_PARTS = {"__pycache__", ".pytest_cache", ".DS_Store"}
SKIP_SUFFIX = {".pyc", ".pyo"}


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


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 WorkBuddy Studio 三件套")
    parser.add_argument("--layout", choices=("flat", "folder"), default="flat")
    args = parser.parse_args()

    if not (SKILL / "SKILL.md").is_file():
        print("缺少 skills/workflow-studio/SKILL.md")
        return 2
    DIST.mkdir(exist_ok=True)

    prefix = "" if args.layout == "flat" else f"{SKILL.name}/"
    skill_zip = DIST / "workflow-studio-skill.zip"
    skill_size = _write(skill_zip, SKILL, prefix)
    print(f"{skill_zip.relative_to(SUITE)}  {skill_size / 1024:.1f} KB  layout={args.layout}")
    if skill_size > LIMIT:
        print(f"超过平台 3MB 限制：{skill_size} 字节")
        return 1

    ext_zip = DIST / "workbuddy-studio-extension.zip"
    ext_size = _write(ext_zip, EXT, "")
    print(f"{ext_zip.relative_to(SUITE)}  {ext_size / 1024:.1f} KB")

    with zipfile.ZipFile(skill_zip) as bundle:
        names = bundle.namelist()
    # 把入口文件排到最前面：看包内容的人第一眼该看到 SKILL.md
    root_entry = f"{prefix}SKILL.md"
    names.sort(key=lambda name: (name != root_entry, name))
    print("技能包内容：")
    for name in names[:12]:
        print(f"  {name}")
    if len(names) > 12:
        print(f"  …共 {len(names)} 个文件")
    if root_entry not in names:
        print(f"警告：包根目录缺少 SKILL.md（期望 {root_entry}）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
