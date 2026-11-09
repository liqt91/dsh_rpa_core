"""版本事实源：release.json ↔ 各产物文件。

只做两件事：

  · version_drift()  —— 列出「事实源与文件里写的版本不一致」的项；
  · apply_versions() —— 打包前把事实源写回文件（幂等，只在有差异时写盘）。

为什么用文本级替换而不是 json.dump / toml 库：改写要保留原文件的缩进、换行
与键顺序；整文件重写会把 LF 变成 CRLF，还会把别人的手工格式冲掉。
锚点必须唯一，否则宁可报错也不猜哪一处是版本（同一个文件里出现两个
`"version"` 时，改错一处比不改更坏）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
REPO = SUITE.parent
RELEASE = SUITE / "release.json"

PATTERNS = {
    "json": re.compile(r'^(\s*"version"\s*:\s*")([^"]*)(".*)$', re.MULTILINE),
    "toml": re.compile(r'^(version\s*=\s*")([^"]*)(".*)$', re.MULTILINE),
    "frontmatter": re.compile(r"^(version:\s*)(\S+)(.*)$", re.MULTILINE),
}


def load_release() -> dict:
    data = json.loads(RELEASE.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1:
        raise ValueError(f"release.json 的 schemaVersion 不是 1：{data.get('schemaVersion')!r}")
    versions = data.get("versions") or {}
    files = data.get("files") or {}
    if not versions or set(versions) != set(files):
        raise ValueError("release.json 的 versions 与 files 必须一一对应且非空")
    for name, value in versions.items():
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(value)):
            raise ValueError(f"{name} 的版本不是语义化版本：{value!r}")
    for name, spec in files.items():
        if spec.get("format") not in PATTERNS:
            raise ValueError(f"{name} 的 format 不认识：{spec.get('format')!r}")
    return data


def _rewrite(raw: str, pattern: re.Pattern, value: str) -> tuple[str, str]:
    """返回 (改写后的全文, 锚点里读到的当前版本)。锚点不唯一直接抛错。"""
    matches = list(pattern.finditer(raw))
    if len(matches) != 1:
        raise ValueError(f"版本锚点找到 {len(matches)} 处（应为 1），拒绝猜测")
    match = matches[0]
    return raw[: match.start(2)] + value + raw[match.end(2) :], match.group(2)


def _entries() -> list[tuple[str, Path, re.Pattern, str]]:
    data = load_release()
    entries = []
    for name, spec in data["files"].items():
        entries.append(
            (name, REPO / spec["path"], PATTERNS[spec["format"]], data["versions"][name])
        )
    return entries


def version_drift() -> list[str]:
    """事实源与各文件里实际写的版本之间，有哪些不一致。空列表 = 对齐。"""
    drift: list[str] = []
    for name, path, pattern, expected in _entries():
        if not path.is_file():
            drift.append(f"{name}: 缺文件 {path.relative_to(REPO).as_posix()}")
            continue
        try:
            _, current = _rewrite(path.read_bytes().decode("utf-8"), pattern, expected)
        except (OSError, UnicodeError, ValueError) as exc:
            drift.append(f"{name}: {exc}")
            continue
        if current != expected:
            drift.append(f"{name}: 文件写 {current}，事实源是 {expected}")
    return drift


def apply_versions() -> list[str]:
    """打包前把事实源写回各文件；返回真正改动了哪些（幂等：一致时不写盘）。"""
    changed: list[str] = []
    for name, path, pattern, expected in _entries():
        if not path.is_file():
            raise FileNotFoundError(f"{name} 指向的文件不存在：{path}")
        raw = path.read_bytes().decode("utf-8")
        rewritten, current = _rewrite(raw, pattern, expected)
        if current == expected:
            continue
        # write_bytes 而不是 write_text：不碰换行，也不改其余任何字节。
        path.write_bytes(rewritten.encode("utf-8"))
        changed.append(f"{name}: {current} → {expected}")
    return changed


if __name__ == "__main__":
    import sys

    if "--apply" in sys.argv:
        for line in apply_versions():
            print(f"[SYNC] {line}")
    drift = version_drift()
    for line in drift:
        print(f"[FAIL] {line}")
    print("版本口径一致" if not drift else f"{len(drift)} 处漂移")
    raise SystemExit(1 if drift else 0)
