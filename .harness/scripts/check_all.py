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
    "check_input_helpers.mjs",
    "check_precheck_helpers.mjs",
    "check_click_helpers.mjs",
    # 浏览器收尾 op（关标签页 / 终止浏览器）：批量关闭的「逐项记账」语义只有
    # 扩展侧能证明——Python 桩测的是接口形状，一个把 Promise.all 当成功的实现
    # 也能过 Python 测试，却会在真机上把「关了 2/3」报成「全关了」。
    "check_close_ops.mjs",
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
