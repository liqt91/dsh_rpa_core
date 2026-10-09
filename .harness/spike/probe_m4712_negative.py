"""M47.12 负向验证：新截图链路与新界面的判据是否真拦得住。

**探针自己要能被审**（M48 教训，三条硬规则都在下面）：
 ① 注入锚点必须**整块取到邻接语句之后**——``old`` 截断造成语法错误的注入**永远
    不算命中**（那不是「门禁抓到了」，是「模块被���倒」，红的方向不对）。
 ② **不删任何文件**（``unlink()`` 是删除守卫的触发面：守卫按turn 累计删除数 >50
    即fail-closed ``SystemExit(1)``，探针会在中途静默中断——表现为「跑到一半没有
    ✗ 却 exit 1」，既不是干净红也不是绿）。改用 **mtime 变化**判断本轮是否真产出。
 ③ 起手 ``assert_no_sentinel()``：任何受管文件里还留着上一轮的注入哨兵 ⇒ 硬失败。
    不靠人记性。

注入清单（12 条，分四组）：
  **A 组截图换算（screen_shot.py，6条）**——攻纯函数与服务层
  **B 组扩展回传（background.js / content.js，走 node 门禁，3 条）**
  **C 组界面口径（element_editor.py / element_panel.py / app.py，3 条）**
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOT = ROOT / "src" / "rpa_core" / "capture" / "screen_shot.py"
BG = ROOT / "extension" / "background.js"
CONTENT = ROOT / "extension" / "content.js"
EDITOR = ROOT / "src" / "rpa_core" / "gui" / "element_editor.py"
PANEL = ROOT / "src" / "rpa_core" / "gui" / "element_panel.py"
GUIAPP = ROOT / "src" / "rpa_core" / "gui" / "app.py"

PY_TESTS = [
    ROOT / "tests" / "contract" / "test_screen_shot.py",
    ROOT / "tests" / "contract" / "test_verify_element.py",
    ROOT / "tests" / "contract" / "test_gui_element_editor.py",
    ROOT / "tests" / "contract" / "test_gui_panels.py",
]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
NODE = "C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"

SENTINEL = "@@M4712_NEGATIVE_SENTINEL@@"
MANAGED = [SHOT, BG, CONTENT, EDITOR, PANEL, GUIAPP]


# ---- 跑测与计数 --------------------------------------------------------------
def run_pytest() -> tuple[int, str, float]:
    """跑受影响的 Python 判据。返回 (退出码, 输出, 报告文件 mtime)。

    **报告文件 mtime 用来判断「本轮是否真跑完了」**（替代删除判断，见模块 docstring ②）。
    """
    xml = ROOT / ".harness" / "spike" / "_m4712_neg.xml"
    before = xml.stat().st_mtime if xml.exists() else 0.0
    proc = subprocess.run(
        [PY, "-m", "pytest", "-p", "no:cacheprovider", "--tb=line",
         "--junitxml=" + str(xml), *[str(p) for p in PY_TESTS]],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    after = xml.stat().st_mtime if xml.exists() else 0.0
    return proc.returncode, proc.stdout + proc.stderr, after - before


def run_node(script: Path) -> tuple[int, str]:
    proc = subprocess.run([NODE, str(script)], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout + proc.stderr


def failed_lines(output: str) -> list[str]:
    """``FAILED <nodeid>`` / ``ERROR <nodeid>`` 行。

    **不读 ``N failed`` 摘要行**：本机 pytest teardown 的批量删除守卫会让摘要行
    永远打不出来（连全绿那次也一样）；``FAILED`` 行是跑的过程中逐条打的，更强。
    """
    return [line.strip() for line in output.splitlines()
            if line.strip().startswith(("FAILED ", "ERROR "))]


def counts(output: str) -> tuple[int, int]:
    lines = failed_lines(output)
    return (sum(1 for x in lines if x.startswith("FAILED ")),
            sum(1 for x in lines if x.startswith("ERROR ")))


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def assert_no_sentinel() -> None:
    """起手硬失败：受管文件里还留着上一轮注入 ⇒ 先查工作区，别在脏基线上谈验证。"""
    dirty = [str(p) for p in MANAGED if SENTINEL in p.read_text(encoding="utf-8")]
    if dirty:
        print(f"ABORT：以下文件里还有注入哨兵，工作区是脏的：{dirty}")
        raise SystemExit(3)


def inject_payload(text: str, tag: str, target: Path, old: str, new: str) -> str:
    """把 ``old`` 换成 ``new`` **加一行独占的哨兵注释**，返回注入后的全文。

    哨兵**必须真的写进注入文本**（M47.12 修）：先前只在起手检查、从不写入，
    于是「上一轮 run 被中断在 ``finally`` 之前」留下的注入根本检不出来——
    ``assert_no_sentinel()`` 恒绿，工作区带着 A6 的注入态开跑，BASELINE 直接
    ``ZeroDivisionError`` 变红，看起来像「实现坏了」。

    拼法（这题前后栽了三次，逐条记下来别再简化）：
      ① ``old`` **不带**尾换行（13 条里 11 条）⇒ 替换文本必须自己以 ``\\n`` 收尾，
         否则哨兵与原文下一行**同行**，那行代码被注释掉，人工还原极易连带丢掉
         （C1b 就这么坏过一次）。
      ② ``old`` **带**尾换行（C1a/C1b）⇒ 用 new **原文**（含它自己的尾换行）当
         head；此处若先 rstrip 就少一个换行、哨兵又会粘行。
         ⚠️ 分支依据是 **old**（原文被吃掉多少换行），不是 new。
      ③ 缩进按 head 的**末行**取，不是首行。
      ④ 哨兵**独占一行**、带正确缩进；py 用 ``#``、js 用 ``//``（js 里 ``#``
         是语法错误，而受管的扩展文件后缀是 ``.js`` 不是 ``.mjs``）。
    """
    head = new if old.endswith("\n") else new + "\n"
    last_line = head.rstrip("\n").rsplit("\n", 1)[-1]
    indent = " " * (len(last_line) - len(last_line.lstrip(" ")))
    hashc = "# " if target.suffix == ".py" else "// "
    payload = head + indent + hashc + SENTINEL + ":" + tag + "\n"
    return text.replace(old, payload, 1)


# ---- 注入清单----------------------------------------------------------------
# (标签, 目标文件, old, new, 为什么这样算「打在判据方向上」, 期望命中的判据关键词)
INJECTIONS: list[tuple[str, Path, str, str, str, tuple[str, ...]]] = [
    # ---- A 组：截图换算 ----
    # 每条 old 都**整块取到邻接语句之后**（模块 docstring ①）：截断的 old 会
    # 造成语法错误，那不是「判据拦住了」，是「模块被打倒」，红的方向不对。
    ("A1", SHOT,
     "    ox, oy = float(window_origin[0]), float(window_origin[1])\n"
     '    return {"x": left - ox, "y": top - oy, "width": width, "height": height}',
     "    ox, oy = float(window_origin[0]), float(window_origin[1])\n"
     '    return {"x": left, "y": top, "width": width, "height": height}',
     "桌面腿不减窗口原点 ⇒ 红框整体偏移窗口左上角（正是「1:1 同源」那条判据）",
     ("screen_coord_minus_window_origin", "service_layer_returns_shot")),

    ("A2", SHOT,
     "    scale = float(image_size[0]) / vp_w\n    if scale <= 0:\n        return None",
     "    scale = 1.0  # 假装 100% 缩放\n    if scale <= 0:\n        return None",
     "换算基准从「图实际宽」退化成 1.0 ⇒ 分数缩放下框偏（判据里有 dpr≠2 的用例）",
     ("browser_box", "service_layer_scales")),

    ("A3", SHOT,
     "    ox, oy = float(window_origin[0]), float(window_origin[1])\n"
     "    vx = (screen_x - ox) * scale\n    vy = (screen_y - oy) * scale",
     "    ox, oy = float(window_origin[0]), float(window_origin[1])\n"
     "    vx = 0.0\n    vy = 0.0  # 忘了视口原点在窗口里的偏移",
     "漏掉视口原点偏移 ⇒ 框整体上移一个标签栏高度（80~140px 量级）",
     ("browser_box", "service_layer_scales")),

    ("A4", SHOT,
     "    clamped_x = max(0.0, x)\n    clamped_y = max(0.0, y)\n"
     '    w = max(2.0, min(w - (clamped_x - x), float(image_size[0]) - clamped_x))\n'
     '    h = max(2.0, min(h - (clamped_y - y), float(image_size[1]) - clamped_y))',
     "    clamped_x = max(0.0, x)\n    clamped_y = max(0.0, y)\n"
     '    w = max(2.0, w)  # 只钉位置不收窄\n'
     '    h = max(2.0, h)',
     "**钳位只钉位置不收窄宽** ⇒ 框比元素宽一倍，看着像框住了旁边的东西"
     "（这正是本轮修的那个真 bug，判据必须能拦住它）",
     ("browser_box", "clamps")),

    ("A5", SHOT,
     "    if x + w <= 0 or y + h <= 0:\n        return None\n"
     '    if x >= float(image_size[0]) or y >= float(image_size[1]):\n        return None',
     "    if False:  # 左/上完全在外不再拒\n        return None\n"
     '    if False:  # 右/下完全在外也不拒\n        return None',
     "元素完全在窗口外时也硬画一个框（判据钉「完全在外 → None」）。"
     "**必须同时拆掉两道守卫**：只拆第一道会被第二道拦住，注入看起来「没打中」"
     "——那其实是注入不彻底，不是判据漏",
     ("outside", "browser_box", "nothing_to_draw")),

    ("A6", SHOT,
     "    if width <= 0 or height <= 0 or vp_w <= 0:\n        return None",
     "    if width <= 0 or height <= 0:\n        return None",
     "去掉 vp_w<=0 这道守卫 ⇒ viewport.width=0 时**除零**抛出去（判据钉「坏 viewport 不炸」）",
     ("browser_box", "junk")),

    # ---- B 组：扩展回传（node 门禁 check_verify.mjs）----
    ("B1", BG,
     '    return typeof hwnd === "number" && hwnd > 0 ? hwnd : null;',
     '    return hwnd != null ? hwnd : null;',
     "**放宽句柄形态**：字符串 \"123\" / 布尔 true 都会被当句柄回传——host 会去截一个"
     "毫不相干的窗口（W15b 三条正是钉这个）。"
     "注意第一版注入写的是 `?? 0`，那是**恒等注入**：下游 `typeof/> 0` 守卫未动，"
     "行为完全不变 ⇒ 门禁必然全绿，会被误读成「判据漏了」",
     ()),
    ("B2", BG,
     "    if (wantShot && !silent) {\n      shot = await windowHandleFor(tab);\n    }",
     "    if (wantShot && !silent) {\n      shot = null;\n    }",
     "要句柄时也不取 ⇒ 预览永远退化成「未能定位浏览器窗口」",
     ()),
    ("B3", CONTENT,
     "    screenX: window.screenX,\n    screenY: window.screenY,",
     "    screenX: 0,\n    screenY: 0,",
     "不回传视口屏幕原点 ⇒ 框整体偏一个标签栏高度（host 侧推不出来，只能扩展给）",
     ()),

    # ---- C 组：界面口径 ----
    ("C1a", EDITOR,
     "        self.tabs.setCurrentIndex(1)\n",
     "        self.tabs.setCurrentIndex(0)\n",
     "browser 默认停在「预览」页签（M47.12 用户诉求第 1 条）",
     ("has_preview_and_locate_tabs",)),

    ("C1b", EDITOR,
     "        self.desktop_tabs.setCurrentIndex(1)\n",
     "        self.desktop_tabs.setCurrentIndex(0)\n",
     "桌面腿默认也回到「预览」（M47.12 推翻M47.11：桌面腿同样有页签）",
     ("has_preview_and_locate_tabs",)),
    ("C2", EDITOR,
     "        splitter.setStretchFactor(0, 1)\n        splitter.setStretchFactor(1, 1)",
     "        splitter.setStretchFactor(0, 1)\n        splitter.setStretchFactor(1, 2)",
     "splitter 又变回 1:2（节点路径与属性框均分那条判据）。"
     "**这条打的是 stretchFactor 而不是 setSizes**——原先判据只钉 setSizes 时，"
     "本注入**全绿**（setSizes 那半仍均分），正是它暴露了判据少钉了一半",
     ("stretch_factors_keep_columns_equal",)),

    ("C3", PANEL,
     '            is_desktop and meta.get("windowTitle") and f"所在窗口：{meta[\'windowTitle\']}",',
     '            is_desktop and meta.get("controlType") '
     'and f"controlType: {meta[\'controlType\']}",',
     "描述区又摆回「字段表里能勾的」信息（用户诉求第 5 条那条线）",
     ("metadata_only_hints_at_desktop_class", "metadata_hints_win32_class_use",
      "desktop_edits_locator_by_fields")),
]

# node 门禁期望：node 失败必须靠**退出码**判（M47 教训：JS 注入带 `#` 哨兵会让
# 语法坏掉，门禁 `new Function` 抛异常后 exit 1 且**不打印汇总行** ⇒ 只数行会假绿）
NODE_EXPECTED_KEYS = ("W13", "W15", "W15b")

NODE_SCRIPT = ROOT / "scripts" / "check_verify.mjs"
# 纯函数切片门禁：浏览器腿视口偏移也在这条里（check_verify 切的是 background.js）
NODE_ONLY_JS = {"B1", "B2", "B3"}


def main() -> int:
    assert_no_sentinel()
    originals = {p: p.read_bytes() for p in MANAGED}
    before = {p: md5(p) for p in MANAGED}

    # 整段包进 try/finally（M47.12 修）：先前 BASELINE 的两个 `return 2` 在
    # try 之外，中断（Ctrl-C / 超时 / SystemExit）会跳过还原，把注入留在盘上。
    # 下一轮起手 assert_no_sentinel() 又检不出来（那时哨兵根本没写进去）⇒ 脏态
    # 固化，看起来像「实现坏了」。**任何提前 return 都必须经过 finally。**
    verdicts: list[tuple[str, bool, str]] = []
    try:
        code, out, dt = run_pytest()
        if counts(out) != (0, 0) or dt <= 0:
            print("BASELINE 红了（或报告未更新），先修判据/被测代码再谈注入：")
            print(out[-3000:])
            return 2
        print(f"BASELINE 绿（failed=0 error=0；exit={code} 非 0 是删除守卫 SystemExit，不计判据）")

        code, out = run_node(NODE_SCRIPT)
        if code != 0:
            print("BASELINE node 门禁不绿，先修：")
            print(out[-2000:])
            return 2
        print("BASELINE node 门禁绿（exit=0）")

        for tag, target, old, new, why, keys in INJECTIONS:
            text = originals[target].decode("utf-8")
            if text.count(old) != 1:
                print(f"[{tag}] SKIP：锚点命中 {text.count(old)} 次（要求恰好 1）")
                verdicts.append((tag, False, f"锚点命中 {text.count(old)} 次"))
                continue
            # 拼法与哨兵规则见inject_payload()（那里有三次踩坑的记录）
            payload = inject_payload(text, tag, target, old, new)
            target.write_bytes(payload.encode("utf-8"))
            if tag in NODE_ONLY_JS:
                code, out = run_node(NODE_SCRIPT)
                # JS 侧必须看**退出码**（见上）：失败可能不打印任何汇总文本
                ok = code != 0
                hit = any(k in out for k in NODE_EXPECTED_KEYS) or ok
                detail = f"exit={code} 命中={hit}"
            else:
                code, out, dt = run_pytest()
                lines = failed_lines(out)
                failed, error = counts(out)
                hit = [k for k in keys if any(k in line for line in lines)]
                # 「精确红」：红在针对的那几条判据上，而不是整个模块被打倒
                ok = failed > 0 and error == 0 and dt > 0 and bool(hit)
                detail = f"failed={failed} error={error} 报告更新={dt > 0} 命中={hit}"
            print(f"[{tag}] {'红' if ok else '未拦住'}：{detail}  # {why}")
            verdicts.append((tag, ok, detail))
    finally:
        for path, data in originals.items():
            path.write_bytes(data)

    for path in MANAGED:
        now = md5(path)
        if now != before[path]:
            print(f"还原核 md5 不一致!! {path.name}: {before[path]} -> {now}")
    same = all(md5(p) == before[p] for p in MANAGED)
    print(f"还原核 md5: {'全部一致' if same else '有不一致!!'}")

    bad = [t for t, ok, _ in verdicts if not ok]
    if not same:
        bad.append("restore")
    for tag, ok, detail in verdicts:
        print(f"  {tag}: {'PASS' if ok else 'FAIL'} {detail}")
    print("全部注入均被拦住" if not bad else f"未拦住: {bad}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())