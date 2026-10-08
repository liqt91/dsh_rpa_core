"""M48/M50 负向验证探针（标准流程：对照绿 → 注入精确红 → 逐字节还原核 md5）。

十二处注入（D1 四处 + D2 四处 + D3 四处）：
  I1 模型层：path 空列表不再拦（静默通过）→ 模型判据红
  I2 agent 层：祖先链把**目标自身**也带进 path → path_excludes_target 红
  I3 uia 执行器：_find 忽略 path（收窄旁路）→ path 收窄判据红
  I4 uia 执行器：_step_matches 把 AND 改成 OR（恒匹配）→ step_matches 判据红
  I5 模型层 D2：effective_match_mode 恒返回 exact（吃掉 contains/regex）→ 模糊匹配判据红
  I6 模型层 D2：matches_text 的 contains 分支退化成等值 → contains 判据红
  I7 uia 执行器 D2：_find_in 忽略 matchMode（恒走 criteria 等值）→ 控件级模糊匹配判据红
  I8 win32 执行器 D2：_find_in 忽略 matchMode（title 恒等值）→ win32 侧判据红
  I9 uia 执行器 D3：_find 不解析 anchor → 锚点缺失判据红（不抛异常）
  I10 win32 执行器 D3：_find 不解析 anchor → win32 锚点判据红
  I11 模型层 D3：anchor 嵌套不再拦 → 嵌套拒绝判据红
  I12 uia 执行器 D3：AnchorNotResolved 的 details 丢掉锚点上下文 → details 判据红

真机回归钉子（D1 选项 A）：
  I20 agent：_path_steps_from 不再剥根窗口级 → 真机 path 首级残留判据红
  I21 uia：_narrow_by_path 放宽成「自身或后代」→ 收窄判据红

四契约同步（GUI 字段表 / 结构字段透传 / i18n）：
  I13–I16 GUI 字段表与结构字段；I17–I19 i18n 标签

M50 改写入口（祖先链增删改 + 锚点编辑）：
  I22 共享纯函数：全空级不再丢弃 → 编辑器与捕获侧**同时**红（两端同规则的证据）
  I23 GUI：_commit_path_steps 绕开共享纯函数 → 空级不再被丢（编辑器另立权威）
  I24 GUI：祖先链表退回只读 → 改动不落进结果文档
  I25 GUI：取消勾选不再移除 anchor → 残留空壳

每处：先跑对照（原始文件必须绿）→ 注入 → 精确失败 → 还原 → md5 一致 → 复绿。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
TEST = "tests/contract/test_desktop_locator_path.py"
BAK = pathlib.Path(tempfile.gettempdir()) / "m48_neg_backup"

SENTINEL = "# [M48-NEGATIVE-INJECTED]"

PATHS = {
    "model": "src/rpa_core/model/desktop.py",
    "agent": "src/rpa_core/capture/desktop_agent.py",
    "uia": "src/rpa_core/executors/desktop.py",
    "win32": "src/rpa_core/executors/desktop_win32.py",
    # 四契约同步（任务 #8）：GUI 字段表 / 结构字段透传
    "editor": "src/rpa_core/gui/element_editor.py",
    "i18n": "src/rpa_core/devserver/static/i18n.js",
}

#: 每处注入对应的测试文件（默认 = TEST）。
GUI_TEST = "tests/contract/test_gui_element_editor.py"
I18N_TEST = "tests/contract/test_editor_i18n.py"


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def run_tests(test: str = TEST) -> tuple[int, str, int, int]:
    """跑目标测试，返回 (rc, 输出, passed 数, failed 数)。

    **不解析末行文本**：本机删除守卫的 atexit 回调会在输出末尾打印
    `SystemExit: 1`（用户记忆既有事实）——它不改退出码，但会让 `tail -1`
    读到假结果。

    **也不用 stdout 摘要行**：`-q` 下全绿时 pytest 只打点、不打印
    `N passed`（实测 passed=0 failed=0 的假零）。计数改从 **junitxml** 取，
    那是 pytest 自己写的事实源。

    .. warning:: **不要 `unlink()` 这个 xml**（2026-10-08 真事故）。
       本机沙箱的删除守卫按 **turn** 累计删除数、>50 即 fail-closed
       `SystemExit(1)`。旧版每跑一次就 `xml.unlink()` 一次；M48 的 12 处注入
       尚且勉强（<50），M50 加到 I22–I25 后（25 处注入 × 1 跑 + 对照 + 复绿），
       再叠上 pytest 自己清理 `garbage-*` 临时目录的删除，**正好越过阈值**——
       探针在 I8 处被 `SystemExit` 打断，`finally` 里的还原虽然执行了，
       但整轮结果作废（表现为「跑到一半没有任何 ✗ 却 exit 1」）。
       `--junitxml` 本身**会覆盖**同名文件，不需要先删。故这里只读、不删：
       ``if xml.exists()`` 用来判断「本轮是否真的产出了报告」。
    """
    xml = ROOT / ".harness" / "spike" / "_m48_last.xml"
    # 先记 mtime：pytest 覆盖写会刷新它；若本轮因收集错而没产出新报告，
    # 我们至少能凭「mtime 没变」察觉自己在读上一轮的陈旧结果。
    before = xml.stat().st_mtime_ns if xml.exists() else None
    proc = subprocess.run(
        [
            str(PY), "-m", "pytest", test, "-q", "--no-header", "--tb=no",
            f"--junitxml={xml}",
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
    )
    out = proc.stdout + proc.stderr
    passed = failed = 0
    fresh = xml.exists() and (before is None or xml.stat().st_mtime_ns != before)
    if fresh:
        import xml.etree.ElementTree as ET

        root = ET.parse(xml).getroot()
        for suite in root.iter("testsuite"):
            passed += int(suite.get("tests", 0))
            failed += int(suite.get("failures", 0)) + int(suite.get("errors", 0))
        # tests 含 skip/xfail，故 passed = tests - failures - skipped - xfailed
        skipped = sum(int(s.get("skipped", 0)) for s in root.iter("testsuite"))
        passed = passed - failed - skipped
    return proc.returncode, out, passed, failed


def refresh_backups() -> None:
    """把当前（干净的）源文件整体拷进备份目录。

    **不能沿用旧备份**：``PATHS`` 新增 `editor` / `i18n` 后，旧备份目录里没有这两份，
    ``restore_all()`` 会 `FileNotFoundError`——而且是在**已经注入完第一条**之后才炸，
    等于把工作区留在脏状态。故备份必须由本脚本自己刷新，且刷新前先确认干净。
    """
    assert_no_sentinel()
    BAK.mkdir(parents=True, exist_ok=True)
    for key, rel in PATHS.items():
        (BAK / f"{key}.bak").write_bytes((ROOT / rel).read_bytes())


def restore_all() -> None:
    for key, rel in PATHS.items():
        src = BAK / f"{key}.bak"
        dst = ROOT / rel
        if not src.exists():
            raise SystemExit(f"备份缺失，拒绝还原（先跑 refresh_backups）：{src}")
        data = src.read_bytes()
        if SENTINEL.encode() in data:
            raise SystemExit(f"备份里含哨兵，拒绝还原：{src}")
        dst.write_bytes(data)


def assert_no_sentinel() -> None:
    """开跑前自检：工作区任何源文件含哨兵 ⇒ 拒绝运行。

    这是**真事故**的产物（2026-10-08）：上一次跑探针时 stdout 接了管道，输出量大时
    进程在 `print` 上被打断，`restore_all()` 没执行，I8 的注入**留在了源文件里**；
    后续改动是在「带着哨兵的工作区」上做的，直到下一次对照跑才暴露——而那时人已经
    在错误的基线上改了好几步。故：**带着哨兵开跑必须硬失败**，不能靠人记性。
    """
    dirty = [rel for rel in PATHS.values() if SENTINEL.encode() in (ROOT / rel).read_bytes()]
    if dirty:
        raise SystemExit(
            "工作区含未还原的注入哨兵，拒绝运行（先手动还原再跑）：\n  "
            + "\n  ".join(dirty)
        )


def inject(rel: str, old: str, new: str) -> None:
    """整行锚点注入（old 必须恰好出现一次；哨兵插在 new 后）。

    必须 write_bytes 保 LF：``write_text`` 在 Windows 会把 `\\n` 转成 `\\r\\n`，
    把整个文件的行尾污染掉（还原时才发现，diff 一片红）。
    """
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"锚点出现 {count} 次（要求 1）：{rel}\n{old!r}")
    path.write_bytes(text.replace(old, new + "  " + SENTINEL).encode("utf-8"))


def main() -> int:
    refresh_backups()
    originals = {key: md5(ROOT / rel) for key, rel in PATHS.items()}

    print("=== 对照跑（注入前必须全绿）===")
    rc, out, passed, failed = run_tests()
    if rc != 0 or failed != 0 or passed == 0:
        print(out[-3000:])
        print(f"对照不绿：rc={rc} passed={passed} failed={failed}")
        return 1
    print(f"对照绿：{passed} passed / {failed} failed / rc={rc}")

    injections = [
        (
            "I1 模型层：空 path 不再拦",
            "src/rpa_core/model/desktop.py",
            "        if self.path is not None and not self.path:",
            "        if False:",
        ),
        (
            "I2 agent：祖先链带上目标自身",
            "src/rpa_core/capture/desktop_agent.py",
            "    # 最后一跳 candidate 就是目标本身，不入祖先链\n"
            "    if path_out is not None and path_out:\n"
            "        path_out.pop()",
            "    pass",
        ),
        (
            "I3 uia：_find 旁路 path 收窄",
            "src/rpa_core/executors/desktop.py",
            '        if locator.path is None:\n'
            '            return cls._find_in(window, locator)\n'
            '        matches: list[Any] = []\n'
            '        for scope in cls._narrow_by_path(window, locator.path):\n'
            '            matches.extend(cls._find_in(scope, locator))\n'
            '        return matches',
            "        return cls._find_in(window, locator)",
        ),
        (
            "I4 uia：_step_matches 改 OR（恒匹配）",
            "src/rpa_core/executors/desktop.py",
            "            if getattr(info, \"automation_id\", None) != automation_id:\n"
            "                return False\n"
            "        name = getattr(step, \"name\", None)",
            "            pass\n"
            "        name = getattr(step, \"name\", None)",
        ),
        (
            "I5 模型层 D2：effective_match_mode 恒 exact",
            "src/rpa_core/model/desktop.py",
            "    return mode if mode in VALID_MATCH_MODES else \"exact\"",
            "    return \"exact\"",
        ),
        (
            "I6 模型层 D2：matches_text 的 contains 退化成等值",
            "src/rpa_core/model/desktop.py",
            "        return expected in actual",
            "        return actual == expected",
        ),
        (
            "I7 uia D2：_find_in 忽略 matchMode（恒 exact）",
            "src/rpa_core/executors/desktop.py",
            "        mode = effective_match_mode(locator.match_mode)",
            "        mode = \"exact\"",
        ),
        (
            "I8 win32 D2：_find_in 忽略 matchMode（恒 exact）",
            "src/rpa_core/executors/desktop_win32.py",
            "        mode = effective_match_mode(locator.match_mode)",
            "        mode = \"exact\"",
        ),
        (
            "I9 uia D3：_find 不解析 anchor（跳过前置检查）",
            "src/rpa_core/executors/desktop.py",
            "        if locator.anchor is not None:\n"
            "            anchor_locator = locator.anchor.locator\n"
            "            if not cls._find(window, anchor_locator):\n"
            "                raise AnchorNotResolved(locator)",
            "        pass",
        ),
        (
            "I10 win32 D3：_find 不解析 anchor",
            "src/rpa_core/executors/desktop_win32.py",
            "        if locator.anchor is not None:\n"
            "            if not cls._find(window, locator.anchor.locator):\n"
            "                raise AnchorNotResolved(locator)",
            "        pass",
        ),
        (
            "I11 模型层 D3：anchor 嵌套不再拦",
            "src/rpa_core/model/desktop.py",
            "        if self.anchor is not None and self.anchor.locator.anchor is not None:",
            "        if False:",
        ),
        (
            "I12 uia D3：异常 details 丢锚点上下文",
            "src/rpa_core/executors/desktop.py",
            '        self.details: dict[str, Any] = {\n'
            '            "anchor": locator.anchor.locator.model_dump(by_alias=True)\n'
            '            if locator.anchor is not None\n'
            '            else None,\n'
            '        }',
            '        self.details: dict[str, Any] = {}',
        ),
        # ---- D1 真机回归钉子（2026-10-08 真机复验抓到；修法是「产侧剥根级」= 选项 A）----
        (
            "I20 agent：_path_steps_from 不再剥根窗口级（真机 path 首级残留 ⇒ 收窄恒空）",
            "src/rpa_core/capture/desktop_agent.py",
            "    root = int(root_hwnd or 0)\n"
            "    if root:\n"
            "        chain = [\n"
            "            item\n"
            "            for item in chain\n"
            "            if int(getattr(item, \"handle\", 0) or 0) != root\n"
            "        ]\n",
            "",
        ),
        (
            "I21 uia：_narrow_by_path 放宽成「自身或后代」（把 D1 的 A 回滚成 B）",
            "src/rpa_core/executors/desktop.py",
            "            for container in current:\n"
            "                try:\n"
            "                    descendants = container.descendants()\n"
            "                except Exception:\n"
            "                    continue\n"
            "                for item in descendants:\n"
            "                    if cls._step_matches(step, item):\n"
            "                        next_level.append(item)\n",
            "            for container in current:\n"
            "                if cls._step_matches(step, container):\n"
            "                    next_level.append(container)\n"
            "                    continue\n"
            "                try:\n"
            "                    descendants = container.descendants()\n"
            "                except Exception:\n"
            "                    continue\n"
            "                for item in descendants:\n"
            "                    if cls._step_matches(step, item):\n"
            "                        next_level.append(item)\n",
        ),
        # ---- 四契约同步（任务 #8）：GUI 字段表 / 结构字段透传 ----
        (
            "I13 GUI：uia 字段表删掉 matchMode（界面不提供该字段）",
            "src/rpa_core/gui/element_editor.py",
            '        ("matchMode", "text", "匹配方式：exact（默认）/ contains / regex"),\n'
            "    ),\n"
            "    \"win32\": (",
            "    ),\n"
            '    "win32": (',
            GUI_TEST,
        ),
        (
            "I14 GUI：STRUCTURED_LOCATOR_KEYS 少登记 path（契约覆盖漏一个字段）",
            "src/rpa_core/gui/element_editor.py",
            'STRUCTURED_LOCATOR_KEYS: tuple[str, ...] = ("path", "anchor")',
            'STRUCTURED_LOCATOR_KEYS: tuple[str, ...] = ("anchor",)',
            GUI_TEST,
        ),
        (
            "I15 GUI：compose_locator 丢掉 structured（编辑一次即静默丢祖先链）",
            "src/rpa_core/gui/element_editor.py",
            "    for key, value in (structured or {}).items():\n"
            "        if key in STRUCTURED_LOCATOR_KEYS and value is not None:\n"
            "            locator[key] = value\n"
            "    return locator\n",
            "    return locator\n",
            GUI_TEST,
        ),
        (
            "I16 GUI：inert_locator_keys 不再排除结构字段（把真字段报成死字段）",
            "src/rpa_core/gui/element_editor.py",
            "    known |= set(STRUCTURED_LOCATOR_KEYS)\n",
            "",
            GUI_TEST,
        ),
        (
            "I17 i18n：anchor 标签被删（新字段退回英文键名）",
            "src/rpa_core/devserver/static/i18n.js",
            '    anchor: "锚点",\n',
            "",
            I18N_TEST,
        ),
        (
            "I18 i18n：path 标签被删（结构字段同样需要中文标签）",
            "src/rpa_core/devserver/static/i18n.js",
            '    path: "文件路径",\n',
            "",
            I18N_TEST,
        ),
        (
            "I19 i18n：matchMode 标签被删（标量字段的标签也在模型侧判据里）",
            "src/rpa_core/devserver/static/i18n.js",
            '    matchMode: "匹配方式",\n',
            "",
            I18N_TEST,
        ),
        # ---- M50：GUI 改写入口（祖先链增删改 + 锚点编辑）----------------------
        # 判据在 GUI_TEST（test_gui_element_editor.py）。「两端同规则」那一条
        # （test_editor_path_pruning_is_shared_with_capture）两边都碰：I22 打共享纯函数，
        # 编辑器侧与捕获侧同时红——这正是「同源」的证据。
        (
            "I22 共享纯函数：全空级不再丢弃（编辑器与捕获侧同时漏口径）",
            "src/rpa_core/model/desktop.py",
            "        if step:  # 一个键都没有 ⇒ 该级无法描述，丢弃（与捕获侧同口径）\n"
            "            pruned.append(step)",
            "        pruned.append(step)",
            GUI_TEST,
        ),
        (
            "I23 GUI：_commit_path_steps 绕开共享纯函数（编辑器另立一套口径）",
            "src/rpa_core/gui/element_editor.py",
            "        raw = self._path_table_steps()\n"
            "        pruned = prune_locator_steps(raw)",
            "        raw = self._path_table_steps()\n"
            "        pruned = raw",
            GUI_TEST,
        ),
        (
            "I24 GUI：祖先链表退回只读（改了不落）",
            "src/rpa_core/gui/element_editor.py",
            "        table.itemChanged.connect(self._on_path_table_changed)\n"
            "        layout.addWidget(table)\n"
            "        self.path_table = table",
            "        layout.addWidget(table)\n"
            "        self.path_table = table",
            GUI_TEST,
        ),
        (
            "I25 GUI：取消勾选不再移除 anchor（残留空壳）",
            "src/rpa_core/gui/element_editor.py",
            "        if not self.anchor_box.isChecked():\n"
            "            self._structured.pop(\"anchor\", None)",
            "        if False:\n"
            "            self._structured.pop(\"anchor\", None)",
            GUI_TEST,
        ),
    ]

    failures = []
    try:
        for entry in injections:
            label, rel, old, new = entry[0], entry[1], entry[2], entry[3]
            test = entry[4] if len(entry) > 4 else TEST
            print(f"\n=== {label} ===")
            try:
                inject(rel, old, new)
            except SystemExit as exc:
                print("注入失败：", exc)
                failures.append(label)
                continue
            code, out, passed, failed = run_tests(test)
            detail = f"passed={passed} failed={failed} rc={code}"
            if code == 0 and failed == 0:
                print(f"  ✗ 假绿灯：注入后仍绿 —— {detail}")
                failures.append(label)
            elif failed == 0:
                # 非零退出码但没有 failed 计数 ⇒ 收集/语法错误（不是干净断言失败）
                print(f"  ✗ 非断言失败（疑似 error/收集错）—— {detail}")
                failures.append(label)
            else:
                print(f"  ✓ 精确红：{detail}")
            restore_all()
            key = next(k for k, v in PATHS.items() if v == rel)
            restored = md5(ROOT / rel)
            expect = originals[key]
            if restored != expect:
                print(f"  ✗ 还原后 md5 不一致：{restored} != {expect}")
                failures.append(label + "(未还原)")
            else:
                print(f"  ✓ 逐字节还原 md5 一致：{restored[:12]}…")
    finally:
        # 无论正常结束、断言失败还是**外部异常**（含 stdout 被打断的 BrokenPipe），
        # 都必须把注入还原——本文件的存在意义就是留下干净的树。
        restore_all()

    print("\n=== 还原后复绿 ===")
    code, out, passed, failed = run_tests()
    print(f"  {passed} passed / {failed} failed / rc={code}")
    if code != 0 or failed != 0 or passed == 0:
        failures.append("还原后复绿失败")

    print("\n=== 结果 ===")
    if failures:
        print("失败项：", failures)
        return 1
    print("全部注入命中、全部逐字节还原、还原后复绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
