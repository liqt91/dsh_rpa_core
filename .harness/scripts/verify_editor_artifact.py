"""发布前的产物判据：tag / 包内版本 / exe / sha256 四件事必须互相一致。

CI 在 `editor-v*` tag 上构建完调用它；本地也能跑：

    ./.venv/Scripts/python.exe .harness/scripts/verify_editor_artifact.py
    ./.venv/Scripts/python.exe .harness/scripts/verify_editor_artifact.py --tag editor-v0.1.0

为什么不只在安装器里校验：安装器是**用户侧**的兜底，发布侧要是把版本写错、
漏传 .sha256 或者压了个空包，等到用户装完才发现就晚了。这里把「发布物本身对不对」
在上传前拦住，和探针里「装完对不对」形成两层。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
ZIP = DIST / "RPA-Editor-win64.zip"
SIDECAR = Path(str(ZIP) + ".sha256")
TOML = ROOT / ".harness" / "demo" / "ppx_probe" / "app" / "ppx.toml"


def toml_version() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', TOML.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit(f"[verify] {TOML} 里找不到 version")
    return m.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="校验编辑窗口发布产物")
    parser.add_argument("--tag", help="CI 传 github.ref_name，形如 editor-v0.1.0")
    args = parser.parse_args()

    version = toml_version()
    if args.tag and args.tag != f"editor-v{version}":
        print(f"[verify] tag {args.tag} 与 ppx.toml 的 version {version} 不一致", file=sys.stderr)
        return 1
    for path in (ZIP, SIDECAR):
        if not path.is_file():
            print(f"[verify] 缺文件：{path}", file=sys.stderr)
            return 1

    digest = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    recorded = SIDECAR.read_text(encoding="utf-8").strip().split()[0].lower()
    if recorded != digest:
        print(f"[verify] .sha256 不匹配：{recorded[:12]}… vs {digest[:12]}…", file=sys.stderr)
        return 1

    with zipfile.ZipFile(ZIP) as zf:
        names = zf.namelist()
        markers = [n for n in names if n.endswith("editor-version.json")]
        # 顶层那个 exe；_internal/ 里还有 python.exe 之类的运行时，不算窗口本体。
        exes = [
            n for n in names
            if n.lower().endswith(".exe") and "_internal/" not in n and n.count("/") == 1
        ]
        if len(markers) != 1:
            hint = f"[verify] 包里应有且仅有一个 editor-version.json，实得 {len(markers)}"
            print(hint, file=sys.stderr)
            return 1
        if not exes:
            print("[verify] 包里没有顶层 .exe", file=sys.stderr)
            return 1
        try:
            marker = json.loads(zf.read(markers[0]).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            print(f"[verify] editor-version.json 不是合法 JSON：{exc}", file=sys.stderr)
            return 1
        if marker.get("version") != version:
            print(
                f"[verify] 包内版本 {marker.get('version')} != ppx.toml 的 {version}",
                file=sys.stderr,
            )
            return 1

    print(
        json.dumps(
            {
                "ok": True,
                "zip": str(ZIP),
                "tag": args.tag,
                "version": version,
                "entries": len(names),
                "exe": exes[0],
                "sha256": digest,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
