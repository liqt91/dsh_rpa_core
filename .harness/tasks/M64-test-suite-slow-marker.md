# M64：测试套件的 slow 分层（回应「验证要跑很久」）

Status: `done`

> **2026-10-14 立项并当场收口**。本任务为**插入推进**：按 `check_tasks.py` 约定「同时只能有
> 一个 `active`」，M52（selector-advisor）未收口 ⇒ 本任务以 `planned` 记录、**不占 active 位**
> （沿 M60/M62/M63 先例），收口后转 `done`。
>
> 触及范围：`pyproject.toml`（marker 注册）、`tests/conftest.py`（防呆提示）、
> 三个测试文件的 **6 处** `@pytest.mark.slow`、以及 `AGENTS.md` / `.harness/README.md`。
> **不触产品代码**（`src/` / `commands/` 一行不改）；**不改任何断言**。

## 一、起因（维护者原话）

> 「每次变更后，跑验证要很久，我想知道验证了什么？为什么要跑这么久？」
> → 解释清楚后 → 「给慢测试打标记，日常跳过」

## 二、取证（全部为实测；本轮只读，先量后改）

### 2.1 门禁 28 条命令的耗时构成

| 层 | 规模 | 耗时 |
|---|---|---|
| `pytest` | 1687 收集 = `contract` 1419 + `unit` 252 + `e2e` 16 | **214.37s** |
| `ruff check .` | 全仓 | 0.44s |
| 7 条静态门禁 | architecture 81 py / 89 manifests、tasks 84 feat / 1 active、docs 5 / 26 refs、ledger、param 86+3=89、error 80+6+3=89、matrix 89 | 合计 **3.95s**（architecture 1120ms 最慢） |
| 16 个 `check_*.mjs` | 前端纯函数对账 | 百毫秒级 |

⇒ **pytest 占全量 99%**；其余 27 条命令加起来约 **5s**。这也解释了本仓既有的
「首条 pytest 收尾非零会遮住其后全部」这条铁律——后面 27 条本来就快到没有存在感。

### 2.2 慢在「等」，不在「算」（`pytest --durations=25` 实测）

耗时 top 8 合计 **≈64s**，其余 1639 项平均 **≈70ms**。前 25 名累计 ≈100s = pytest 的一半。
逐条读源码后，慢因只有三类，**全都不可通过优化算力消除**：

| 慢因 | 实例 | 等的是什么 |
|---|---|---|
| **阈值倒计时** | `ext_bridge` 的 4 条 `idle_exit`（4.17 / 8.13 / 8.18 / 10.17s） | idle 退出阈值恒为注入值 **4s**；「不退出」只能靠等满观察窗 **8s** 来证明 |
| **真实进程操作 + 轮询** | `test_close_browser_is_exempt_from_session_gate`（13.8~15.6s） | `_close_browser` 真调 `taskkill`，再在 `min(timeout_s, 10.0)` 内轮询等进程退出 |
| **真实超时** | `test_win32_attach_timeout_reports_element_not_found`（3.10s） | 枚举窗口的轮询循环跑满 attach 预算 |

★ **一次被实测推翻的猜测**（记下来，免得后人重走）：那 13.8s 一度被怀疑是
「该用例恰是文件里第一个执行、替后续用例扛了首次初始化」。单独跑它 **13.79s**、
跑整个文件 **14.08s** 且**仍是它**（同文件另外 20 条只要 0.01~0.35s）⇒ 猜测被证否，
是**它自己在等**。

### 2.3 「把阈值调小」为什么不是可选项

`idle_exit` 那 4 条的阈值本身就是被测语义（「静置 N 秒后自杀」）。测试已经通过
`RPA_CORE_HOST_IDLE_EXIT_SECONDS` 注入到 **4s**（默认 1800s），再压就等于不再测那件事。
⇒ 只能**整条标注**，不能改快。

## 三、落地

1. **`pyproject.toml`** 注册 `markers = ["slow: 需等待真实时间…"]`（此前全仓只用内建的
   `parametrize` / `skipif` / `xfail`，**一个自定义 marker 都没有**）。
2. **6 处 `@pytest.mark.slow`**（每处带一行「等的是什么」的注释）：
   - `test_ext_bridge.py` 4 条 `idle_exit_*`
   - `test_browser_close_commands.py::test_close_browser_is_exempt_from_session_gate`
   - `test_desktop_contract.py::test_win32_attach_timeout_reports_element_not_found`
3. **`tests/conftest.py` 加防呆**（`pytest_deselected` + 现有 `pytest_terminal_summary`）：
   一旦按表达式排除，收尾显式打印「本次排除了 N 项 slow 用例——它们**本次没有被验证**；
   提交前必须全量」。与既有的「desktop e2e skipped」提示同构。
   ★ 这条是**必需的**：没有它，`-m "not slow"` 就是个**静默的判据削弱**。
4. **门禁刻意保持全集**：`check_all.py` 里的 pytest 调用**不带任何 `-m`**。
5. 文档：`AGENTS.md` 加 `Test (fast)` 行；`.harness/README.md` 加「日常跑什么 / 提交前跑什么」。

## 四、验收（全部实测）

| 判据 | 结果 |
|---|---|
| 过滤**精确**（不多不少） | 三个文件收集数 **67 → 61**，**恰好差 6** |
| 排除时**如实报出** | 收尾打印 `slow tests deselected` + 「排除了 6 项…它们本次没有被验证」 |
| 门禁**不受影响** | `grep '"-m", "pytest"' check_all.py` ⇒ `[sys.executable, "-m", "pytest"]`，无 `-m` |
| 语法 / 风格 | 三个测试文件 `ast.parse` OK；`ruff check .` → `All checks passed!` |
| 台账 / 文档门禁 | `DOC CHECK PASSED (5 docs, 27 refs)` / `LEDGER CHECK PASSED` / `TASK CHECK PASSED` |
| 全量门禁 | 见 §六（收口时跑） |

## 五、★ 与 BACKLOG「宿主级瞬态慢窗」的界限（**别混为一谈**）

`tasks/BACKLOG.md`（2026-09-28 M41 段）另有一条**未结**欠账：同一份代码的全量套件耗时在
**177s / 209s / 292s / 314s / 330s** 之间开合，并伴随两条 QProcess 用例的 30s 超时，结论是
「存在会开合的**宿主级窗口**，**不由仓库代码决定**」。

- 本任务的 214s 落在那条分布**之内**，属正常范围；
- 本任务处理的是**结构性耗时**（6 条用例**必定**要等 4~10s 真实时间），与那条的**瞬态开合**
  是**两个正交的问题**：本任务**既没有解决、也没有触及**那条欠账。
- 后人在那条欠账上取证时，**不要**用本任务的资料当作「已归因」——两者都叫「慢」，
  但一个是必然、一个是宿主瞬态。

## 六、顺带发现（同一批实跑里撞到的，都如实记下）

1. **一个 flaky 复现了一次**：`test_gui_node_edit.py::test_delete_suppressed_while_param_editor_focused`
   （断言「焦点在参数表单输入框时『删除节点』应禁用」）在**一次全量跑**里红，**单独复跑 rc=0 绿**。
   属已登记的 `test_delete_suppressed_*` 假红源。注意该次输出**确有** `⚠️ Sandbox bypassed`
   ⇒ 不是「沙箱挂账」，是按「同代码重跑可绿」判 flaky。
2. **退出期环境级非零又出现两次**：全量门禁 `rc=127`（而 pytest 自报 1664 passed）、
   `--durations=0` 那次 `rc=139`（Segmentation fault，且**统计行未打印**、`0 FAILED / 0 ERROR`）。
   两者**逐字复跑即 0**。⇒ 继续印证长期记忆里那条：「**豁免沙箱不是充分条件**，
   处置 = 豁免 + 原命令逐字复跑，认绿要可重复」。
3. `closeBrowser` 的测试会**真的终止本机浏览器进程**（`taskkill`）。跑全量门禁前最好确认
   没有开着 Edge/Chrome 的工作现场——这条已由 `@pytest.mark.slow` 顺带隔离在日常之外。

## 七、未做 / 待拍板

- **GUI 端到端那一批（约 60s）没有打标记**：`test_gui_run_pause` / `test_run_control_channel` /
  `test_gui_breakpoints` / `test_gui_run` 同样是「跑流程 + 轮询」，但 GUI 是**高频改动区**
  （`gui/theme.py`、参数面板、画布），日常跳过它们等于**在最常改的地方撤掉保护**。
  ⇒ 若维护者要更快的日常反馈，可再签一个 `slow-gui` 标记；**默认不做**。
- 本任务**不新增 feature**（属内部开发体验，不是产品能力），故 `feature_list.json` 未动。
