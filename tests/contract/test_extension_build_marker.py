"""扩展构建号三方一致判据（M105 补回）。

历史 ``test_capture_extension.py`` 已删，EXT_BUILD 三方一致曾因此**没有活跃门禁**——
改 content.js 忘了同步 manifest.json / background.js 时，用户重载后看到的还是旧脚本，
而所有测试照绿。本文件把这条面重新钉住，不做内容校验，只钉三方相等与可解析。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _build_of(rel: str) -> str:
    text = (ROOT / rel).read_text(encoding="utf-8")
    match = re.search(r'EXT_BUILD\s*=\s*"([^"]+)"', text)
    assert match, f"{rel} 里找不到 EXT_BUILD"
    return match.group(1)


def test_ext_build_marker_matches_manifest_version() -> None:
    content = _build_of("extension/content.js")
    background = _build_of("extension/background.js")
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    assert content == background == version, (
        f"EXT_BUILD 三方不一致：content={content} background={background} manifest={version}"
    )
