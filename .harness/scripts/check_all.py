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
    # 元素编排层的**动态**契约（M68）：真跑 resolve_target / 能力闸 / 暂存不落盘 /
    # 中文名入库 / 改名联动引用。只扫源码证明不了「捕获时真的没写文件」这类假绿灯。
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_element_pick_contract.py")],
    # RPC 线上参数契约（M68 真机事故补闸）：main.js 每处 ppx.call 字面量的参数键必须
    # 与 api.py 签名**逐字绑定**（桥层是 inspect.signature.bind 严格匹配，camelCase 键
    # 配 snake_case 参数会让前端轮询在桥层静默死掉——「捕获完了什么都没发生」）。
    [sys.executable, str(ROOT / ".harness" / "scripts" / "check_rpc_wire_contract.py")],
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
#
# ★ 2026-11-25 清理：清单里曾有 5 条指向 `src/rpa_core/devserver/static/app.js` 的判据
# （param_groups / retry_policy / element_display_helpers / element_name /
# scope_visibility）。那个前端随 Qt 退役（ADR 0021）整体删除，于是这 5 个脚本文件也
# 不在了，而 `if path.exists()` 让它们被静默跳过——一年多里门禁一直在报 PASSED。
# 现在：不存在的登记一律**硬失败**（见文件末尾），这 5 条按「对象已消失」删除登记。
# 参数分组 / 标签 / 表达式模式的判据改由 pytest 承担（tests/contract/test_param_meta.py
# 等），因为它们的事实源在 manifest 与 Python 侧解析，不需要浏览器语义。
PURE_FUNCTION_SCRIPTS = (
    "check_capture_helpers.mjs",
    # 参数表单的表达式模式（M108）：fx 按钮只在声明 x-fx 的字段出现、点一下真的
    # 走 rpa.variableScopes（键名与桥层签名逐字一致）、菜单项真的把 [ref] 插进光标处、
    # 标签删光真的撤销 fx 态、保存真的提交整份 expr_modes。
    # 这四段只在浏览器里跑——Python 侧读源码只能证明「写了 addEventListener」。
    "check_param_form_ui.mjs",
    # M105 运行期选择器分派（xpath/shadow 前缀）：不扩 page.call 参数面，
    # 所以必须有门禁用真切片证明 domOp.query 真按前缀取到了目标，而不是只写了 if。
    "check_selector_query_kinds.mjs",
    # 元素捕获 UI 判据（M68）：文案禁词 / 样式命中区 / 结构单按钮 /
    # 最小 DOM shim 真跑「点一下→弹编辑框→确认才入库→取消就丢弃→详情改名」。
    "check_element_pick_ui.mjs",
    # 编辑器底栏（元素资产 / 全局参数 / 运行日志）：ADR 0022-R3 后 DOM 在 index.html、
    # 版式在 style.css、行为在 main.js；只读源码证明不了三个 tab 真挂上去、
    # 原 details 真搬过去、全局参数真能保存 ⇒ 用最小 DOM shim 真跑一遍绑定与保存。
    "check_editor_bottom_dock.mjs",
    # 元素资产清单（单按钮工具区 + 行内详情/删除）：与捕获链判据分开，
    # 只读源码证明不了「有按钮却点不着」「删除失败却把行抹了」。
    "check_element_library.mjs",
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
    # 网页元素缩略图的取图通道（M102 S2b）：content 的取图函数（四种回包真跑）、
    # background 的 viewportShot 透传与 rpa-shot-request 接线、两端 type 常量对账。
    # 2026-11-19 现场「桌面有截图、网页没有」正是这三段各错一点叠出来的——
    # Python 契约测试只管「拿到数据能不能算对」，管不到「数据有没有被带过去」。
    "check_capture_shot.mjs",
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
    # 活体校验（M47）：content.js verify-helpers 纯求值 + background runVerify
    # 调用矩阵（只发活跃页/补注入重试/结构化报错）+ content 应答接线断言。
    "check_verify.mjs",
    # 工作台「商品库」渲染层（帖子详情采集）：流程逐帖抓 赞/藏/评 + 逐条评论，
    # 数据层存得住不等于页面看得见。切出 renderProducts 真跑一遍，并把 Python
    # Product.to_json 的键与前端 it.<key> 双向对账（任一侧漂移都红）。
    "check_xhs_products_view.mjs",
)
node = shutil.which("node")
if node is None:
    # 以前是「node 不在就整段跳过」，于是清单里的脚本一条都没跑、门禁照样 PASSED。
    # 这与 `if path.exists()` 是同一类盲区（清单是承诺，跳过了却不说），故改硬失败：
    # 跑门禁的机器必须能跑前端纯函数判据，跑不了就别报绿。
    print(f"[gate] 找不到 node —— 前端纯函数判据（{len(PURE_FUNCTION_SCRIPTS)} 条）无法执行")
    raise SystemExit(127)
missing_scripts = []
for script in PURE_FUNCTION_SCRIPTS:
    path = ROOT / "scripts" / script
    if path.exists():
        commands.append([node, str(path)])
    else:
        # ★ 静默跳过是「门禁绿但判据不存在」的根源：本清单曾有 5 条指向已随 Qt 退役
        # 删掉的 app.js 判据，一年多里一直打印 PASSED。清单里登记却不存在的脚本，
        # 只能有两种处理：重建它，或从清单里删掉。不许 `if exists` 假装没看见。
        missing_scripts.append(script)
if missing_scripts:
    print("[gate] 清单登记的脚本不存在：" + "、".join(missing_scripts))
    print("[gate] 要么补上脚本，要么从 PURE_FUNCTION_SCRIPTS 删掉这条登记")
    raise SystemExit(1)

for command in commands:
    print("$", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT, env=gate_env)
    if completed.returncode:
        raise SystemExit(completed.returncode)
print("FULL GATE PASSED")
