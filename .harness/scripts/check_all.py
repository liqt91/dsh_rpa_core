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
    # manifest 声明的输入参数必须被实现消费（M29：把「漂移清单」变成机器门禁）
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_param_consumption.py")],
    # manifest 声明的 errors 必须覆盖实现会返回的错误码（M30 S3：同一种病的另一根轴）
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_error_contract.py")],
    # 指令测试用例表与 catalog 是否对齐（M38）：**静态层**——只读用例表 JSON 与 manifest、
    # 不执行任何命令，毫秒级。全量参数矩阵本身按需跑（RPA_COMMAND_MATRIX=1），
    # 而「新增命令没补用例 / 参数改了没人改表」这类腐烂必须每次提交就拦住。
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_command_matrix.py")],
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
    # 捕获高亮框几何（content.js）：框线带必须整条落在元素**之外**、且碰不到指针
    # ±2px 热区（M41「框要躲着鼠标」）。同样是「只在浏览器里跑」的几何——Python
    # 侧读源码只能证明写了 `left - OVERLAY_OUTSET`，证明不了算出来的带落在哪。
    "check_capture_overlay_geometry.mjs",
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
