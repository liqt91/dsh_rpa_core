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

打包前还会从 `release.json` 把版本写回各产物文件；包内路径 → 源文件的映射统一
走 `dist_sources.py`（`verify_suite.py` 逐文件比 sha256 时复用它），所以「源改了
没重打包」会被判据抓住，旧 zip 不再靠入口层级一直绿。

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

from dist_sources import (
    CONNECTOR,
    DIST,
    SKILL,
    SUITE,
    extension_sources,
    skill_sources,
)
from release import apply_versions

LIMIT = 3 * 1024 * 1024

problems: list[str] = []


def _rel(path: Path) -> str:
    """打印用路径：DIST 被指到仓库外时（负向验证会这么干）也要能打印，别在格式化上崩。"""
    try:
        return path.relative_to(SUITE).as_posix()
    except ValueError:
        return str(path)


def _write(zip_path: Path, sources: dict[str, Path]) -> int:
    """按 dist_sources 的映射写 zip；映射与校验共用，别在这里另造一份路径规则。"""
    missing = [arc for arc, path in sources.items() if not path.is_file()]
    if missing:
        problems.append(f"{zip_path.name} 的映射指向了不存在的源文件：{missing[:5]}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for arc, path in sorted(sources.items()):
            if path.is_file():
                bundle.write(path, arc)
    return zip_path.stat().st_size


def pack_skill(layout: str) -> Path:
    if not (SKILL / "SKILL.md").is_file():
        problems.append("缺少 skills/workflow-studio/SKILL.md")
        return DIST / "workflow-studio-skill.zip"
    prefix = "" if layout == "flat" else f"{SKILL.name}/"
    target = DIST / "workflow-studio-skill.zip"
    size = _write(target, skill_sources(layout))
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
    size = _write(target, extension_sources())
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

    # 版本只有一个事实源（release.json）。打包前写回各产物文件，避免
    # 「改了 SKILL.md 的版本忘了改 connector-meta / manifest」这类漂移进包。
    for line in apply_versions():
        print(f"[版本同步] {line}")

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
