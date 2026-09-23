"""BACKLOG 尾巴取证：win32 后端看到的锚点到底哪一段稳定。

跑法（会开窗抢前台）：

    .venv/Scripts/python.exe .harness/spike/probe_win32_stability.py [轮数]

每轮重新拉起一次同一个 exe，收集全部后代控件的 (class_name, control_id, text)，
最后按 class_name 归组，报出「哪些字段跨轮恒定、哪些字段每轮都变」。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def compile_demo(tmp: Path) -> Path:
    exe = tmp / "RpaCoreDesktopDemo.exe"
    subprocess.run(
        [
            "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe", "/nologo",
            "/target:winexe", f"/out:{exe}", "/r:System.Windows.Forms.dll",
            "/r:System.Drawing.dll", str(ROOT / "testapps" / "desktop" / "Program.cs"),
        ],
        check=True,
        capture_output=True,
    )
    return exe


def snapshot(exe: Path) -> list[dict]:
    import pywinauto

    proc = subprocess.Popen([str(exe)])
    try:
        app = pywinauto.Application(backend="win32")
        deadline = time.time() + 15
        while True:
            try:
                app.connect(title="RPA Core Desktop Demo", timeout=1)
                break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(0.3)
        win = app.window(title="RPA Core Desktop Demo")
        out = []
        for c in win.descendants():
            info = c.element_info
            out.append(
                {
                    "class": c.class_name(),
                    "cid": info.control_id,
                    "text": c.window_text(),
                }
            )
        return out
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(0.5)


def norm_class(cls: str) -> str:
    """把 WindowsForms10.<TYPE>.app.0.<hash> 归成 <TYPE>，露出可变段。"""
    parts = cls.split(".")
    if len(parts) >= 4 and parts[0].startswith("WindowsForms"):
        return f"{parts[0]}.{parts[1]}"
    return cls


def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    tmp = Path(tempfile.mkdtemp(prefix="rpa-win32-stab-"))
    exe = compile_demo(tmp)

    snaps = []
    for i in range(rounds):
        snap = snapshot(exe)
        snaps.append(snap)
        print(f"# round {i + 1}: {len(snap)} descendants", file=sys.stderr)

    # 按 (归组类名, 序号) 对齐——同轮内同类多实例用出现顺序区分
    seq: dict[str, int] = defaultdict(int)
    keys = []
    for item in snaps[0]:
        base = norm_class(item["class"])
        idx = seq[base]
        seq[base] += 1
        keys.append((base, idx))

    report = []
    for pos, key in enumerate(keys):
        base, idx = key
        rows = []
        for snap in snaps:
            same = [c for c in snap if norm_class(c["class"]) == base]
            rows.append(same[idx] if idx < len(same) else None)
        classes = {r["class"] for r in rows if r}
        cids = {r["cid"] for r in rows if r}
        texts = {r["text"] for r in rows if r}
        report.append(
            {
                "key": f"{base}#{idx}",
                "class_stable": len(classes) == 1,
                "class": sorted(classes)[0] if classes else None,
                "cid_stable": len(cids) == 1,
                "cids": sorted(cids),
                "text_stable": len(texts) == 1,
                "texts": sorted(texts),
            }
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    unstable_cls = [r["key"] for r in report if not r["class_stable"]]
    unstable_cid = [r["key"] for r in report if not r["cid_stable"]]
    print(f"# class 漂移的控件: {unstable_cls or '无'}", file=sys.stderr)
    print(f"# control_id 漂移的控件: {unstable_cid or '无'}", file=sys.stderr)


if __name__ == "__main__":
    main()
