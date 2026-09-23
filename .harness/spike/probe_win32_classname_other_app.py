"""BACKLOG 尾巴 #2 取证之三：哈希是「应用级」还是「机器+运行时级」常量。

前两步证明：同一程序跨重编译/跨路径/跨源码变更，哈希恒定。
本步换一个**完全不同的 WinForms 程序**（不同控件组合、不同类名），
看哈希是否仍是 `34f5582`——若是，则它是机器+运行时常量，
`WindowsForms10.<TYPE>.<...>` 里真正有区分力的是中间的 `<TYPE>` 段。

跑法（会开窗抢前台）：

    .venv/Scripts/python.exe .harness/spike/probe_win32_classname_other_app.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

CSC = "C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"

OTHER_APP = r"""
using System;
using System.Drawing;
using System.Windows.Forms;

class OtherApp
{
    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        var f = new Form();
        f.Text = "RPA Probe Other App";
        f.ClientSize = new Size(480, 320);
        var gb = new GroupBox();
        gb.Text = "Group";
        gb.SetBounds(10, 10, 300, 200);
        var tb = new TextBox();
        tb.SetBounds(10, 25, 150, 24);
        var lb = new ListBox();
        lb.SetBounds(10, 60, 150, 90);
        lb.Items.AddRange(new object[] { "x", "y" });
        var cb = new ComboBox();
        cb.SetBounds(10, 155, 150, 24);
        cb.Items.Add("q");
        gb.Controls.Add(tb);
        gb.Controls.Add(lb);
        gb.Controls.Add(cb);
        f.Controls.Add(gb);
        Application.Run(f);
    }
}
"""


def class_names(exe: Path) -> list[str]:
    import pywinauto

    proc = subprocess.Popen([str(exe)])
    try:
        app = pywinauto.Application(backend="win32")
        deadline = time.time() + 15
        while True:
            try:
                app.connect(title="RPA Probe Other App", timeout=1)
                break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(0.3)
        win = app.window(title="RPA Probe Other App")
        return sorted({c.class_name() for c in win.descendants()})
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(0.5)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rpa-cn-other-"))
    src = tmp / "OtherApp.cs"
    src.write_text(OTHER_APP, encoding="utf-8")
    exe = tmp / "OtherApp.exe"
    subprocess.run(
        [
            CSC, "/nologo", "/target:winexe", f"/out:{exe}",
            "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", str(src),
        ],
        check=True,
        capture_output=True,
    )
    names = class_names(exe)
    print(json.dumps({"other_app_classes": names}, ensure_ascii=False, indent=2))
    hashes = {n.split(".")[-1] for n in names}
    print(f"# 本程序的哈希段: {hashes}", file=sys.stderr)
    print("# 演示程序已知哈希段: {'34f5582_r8_ad1'}", file=sys.stderr)


if __name__ == "__main__":
    main()
