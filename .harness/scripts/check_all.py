import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
commands = [
    [sys.executable, "-m", "pytest"],
    [sys.executable, "-m", "ruff", "check", "."],
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_architecture.py")],
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_tasks.py")],
    # 现役导航文档（AGENTS.md / .harness/README.md / architecture / invariants）里的仓库内
    # 相对路径引用必须真实存在（M62）。**故意不扫历史台账**（PROGRESS / ADR / 已完成任务单）
    # ——那里的引用是历史记录，被指文件后来被删改属正常；扫进来只会让门禁长期恒红。
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_docs.py")],
    # 台账字段的上限与换行（M63 S5）：title ≤ 120 / notes ≤ 2000 / PROGRESS cutoff 之后
    # evidence ≤ 400 / 三个台账文件必须 LF。**没有这条闸门，存量治理当天做完、下周就长回来**。
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_ledger.py")],
    # manifest 声明的输入参数必须被实现消费（M29：把「漂移清单」变成机器门禁）
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_param_consumption.py")],
    # manifest 声明的 errors 必须覆盖实现会返回的错误码（M30 S3：同一种病的另一根轴）
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_error_contract.py")],
    # 指令测试用例表与 catalog 是否对齐（M38）：**静态层**——只读用例表 JSON 与 manifest、
    # 不执行任何命令，毫秒级。全量参数矩阵本身按需跑（RPA_COMMAND_MATRIX=1），
    # 而「新增命令没补用例 / 参数改了没人改表」这类腐烂必须每次提交就拦住。
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_command_matrix.py")],
    # 宿主边界（M65）：GUI 冻结基线 + PPX 原型隔离（ADR 0019）。维护者已拍板
    # 「Qt 代码留着不动、也不改」——冻结必须能被机械证明，否则只是一句会过期的话；
    # 原型则不许偷偷 import Qt/gui UI 模块，否则「WebView 壳不靠 Qt」的结论作废。
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_host_boundary.py")],
    # 元素拾取编排层的**动态**契约（M66）：真跑 resolve_target / 能力闸 / 状态机，
    # 只扫源码证明不了「忽略命令类型时也照样返回 browser」这类假绿灯。
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_element_pick_contract.py")],
    # 编辑窗口 ↔ 会话的路径口径（M67）：真跑三种路径形态、桥等价包装、cmd_gui 的 env
    # 注入与 --wait 的失败回传。只扫源码证明不了「中文会话根真能打开」。
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_editor_workspace_contract.py")],
    # 分支职责边界（2026-11-11 拆分）：两条分支共享同一份契约，越界改动直接红。
    # 事实源 .harness/branch-scope.json；闸门自带接线哨兵，摘掉下面这行它自己会红。
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_branch_scope.py")],
    # 小红书工作台应用层单测（xhs-workbench 独占域）：默认 pytest 只收 testpaths=["tests"]，
    # 扫不到 apps/ 下，故独立跑 + 哨兵证明测试真被收集/跑过（含 run_live→evaluate_run 接线）。
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_xhs_workbench_tests.py")],
]

# 真实桌面 E2E（记事本/WinForms 演示程序/捕获悬浮框，会弹窗抢前台）**默认不进
# 门禁**——跑门禁不该打断维护者手头操作。需要完整覆盖时显式开：
#   uv run python .harness/scripts/check_all.py --with-desktop-e2e
# 或先设环境变量（PowerShell: $env:RPA_DESKTOP_E2E=1）再跑门禁。
# 日常 `uv run pytest` 缺省同样跳过（见 tests/conftest.py）。
gate_env = dict(os.environ)
if "--with-desktop-e2e" in sys.argv:
    sys.argv.remove("--with-desktop-e2e")
    gate_env["RPA_DESKTOP_E2E"] = "1"

# 前端纯函数一致性校验（编辑器渲染逻辑与后端语义共用同一份源码）。
# node 不在时跳过，避免门禁在无 node 的机器上硬失败。
PURE_FUNCTION_SCRIPTS = (
    "check_param_groups.mjs",
    "check_retry_policy.mjs",
    "check_capture_helpers.mjs",
    # 元素拾取 UI 三层判据（M66）：文案禁词 / 样式命中区 / 最小 DOM shim 真跑渲染函数。
    "check_element_pick_ui.mjs",
    # 捕获高亮框几何（content.js）：框线带必须整条落在元素**之外**、且碰不到指针
    # ±2px 热区（M41「框要躲着鼠标」）。同样是「只在浏览器里跑」的几何——Python
    # 侧读源码只能证明写了 `left - OVERLAY_OUTSET`，证明不了算出来的带落在哪。
    "check_capture_overlay_geometry.mjs",
    # 捕获描述符里的元素矩形**键名**（content.js 的 rectOf，2026-10-09 真机事故）：
    # 捕获回传原写 {x, y}，而 host 的 screen_shot.browser_box_in_window 只读 {left, top}
    # ⇒ 截图恒算不出红框，且异常被 `except (KeyError, ...)` 吞掉，只表现为「截图有、框没有」，
    # 看起来像功能没实现而不是键名对不上。必须**真跑一次求值 + 与 host 换算函数交叉对账**
    # ——Python 读源码只能证明「文件里出现过 left」，证明不了键名真对得上。
    "check_capture_descriptor_rect.mjs",
    # 捕获脚本的**实例生命周期**（content.js，M42）：可接管守卫（活实例拦人、僵尸实例
    # 先拆干净再接管）、僵尸在 mousemove 下不再留框、失败路径只收框不留残影。
    # 这几个语义只在浏览器里跑：Python 读源码最多证明「写了 if (previous.alive())」。
    "check_capture_lifecycle.mjs",
    # 捕获通道的**可达性**（background.js，M42）：已打开标签页补注入、撤防不补注入、
    # 注入失败不阻断、统一撤防（落盘 + 广播）、host 断开必须撤防、arm 先落盘再广播。
    "check_capture_broadcast.mjs",
    "check_input_helpers.mjs",
    "check_precheck_helpers.mjs",
    "check_click_helpers.mjs",
    # 「智能识别并补充地址前缀」（background.js，M53）：「获取元素属性」与补前缀两项
    # 对标影刀「获取元素信息」的参数缺口。补前缀是纯字符串语义、五种形态各有归处，
    # 只在浏览器里跑——Python 读源码证明不了「`/next` 会不会被拼成 `https:///next`」。
    "check_url_prefix_helpers.mjs",
    # 浏览器收尾 op（关标签页 / 终止浏览器）：批量关闭的「逐项记账」语义只有
    # 扩展侧能证明——Python 桩测的是接口形状，一个把 Promise.all 当成功的实现
    # 也能过 Python 测试，却会在真机上把「关了 2/3」报成「全关了」。
    "check_close_ops.mjs",
    # 编辑器确认框的「只读区块」纯函数区（app.js）：把已捕获的候选定位与语义特征
    # 如实渲染出来。这一段**只在浏览器里跑**——Python 侧读源码只能证明「写了这行字」，
    # 证明不了「渲染出哪几行」，而它的唯一职责就是渲染对。
    "check_element_display_helpers.mjs",
    # 默认元素名生成器（A3，app.js）：与 Python 侧同一份用例表
    # （tests/contract/data/element_name_cases.json）切片求值——两份实现
    # （零构建双端）输出分叉时至少一侧红。
    "check_element_name.mjs",
    # 活体校验（M47）：content.js verify-helpers 纯求值 + background runVerify
    # 调用矩阵（只发活跃页/补注入重试/结构化报错）+ content 应答接线断言。
    "check_verify.mjs",
    # 变量可见范围（app.js 的 visibleScopeAt，2026-10-14）：与 Python 侧
    # tests/contract/test_variable_scope_visibility.py 跑**同一份用例表**
    # （tests/contract/data/scope_visibility_cases.json）。桌面端菜单读 Python 的
    # compiler.visible_scope，Web 属性面板是另一份 JS 实现——Python 侧读源码只能
    # 证明「写了 visibleScopeAt」，证明不了它对每个节点的判定与编译器一致。
    "check_scope_visibility.mjs",
)
node = shutil.which("node")
if node:
    for script in PURE_FUNCTION_SCRIPTS:
        path = ROOT / "scripts" / script
        if path.exists():
            commands.append([node, str(path)])

for command in commands:
    print("$", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT, env=gate_env)
    if completed.returncode:
        raise SystemExit(completed.returncode)
print("FULL GATE PASSED")
