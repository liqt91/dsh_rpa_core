# M67：会话 ↔ 编辑窗口的路径口径对齐（BAD_PATH / 日志 / 失败回传）

Status: `wip`

> **2026-11-06 立项，当日修复并发版 `editor-v0.1.3`**。起因：dsh 里打开任何流程都报
> `INTERNAL_ERROR: Python API 执行失败`，排查结论一度写成「窗口 JS→Python 桥对任何文件都
> 失败、编辑器本体故障、无日志」。本文是**真因取证 + 施工单**，不是复盘。
> 前置：M66（`editor-v0.1.2`）、ADR 0019 宿主边界冻结、ADR 0014 PPX 实证脚手架。
> **不占 active 位**：`project_state.active_plan` 仍是 `M52-selector-advisor.md`。

## 一、真因（三个组件各说一半真话）

| 环节 | 口径 |
|---|---|
| `studio.py::check_path`（会话侧） | 必须**绝对路径**，落在 `--root` 内即放行；`_open` 把它原样推进 inbox |
| `main.js::pollSession` | `openFlow(request.path)` 原样传 |
| `api.py::_flow_path`（窗口侧） | 把 `path` 当**相对自己 WORKSPACE** 的路径，`is_relative_to` 不成立即 `raise ValueError("流程路径越界")` |
| `ppx_py/bridge.py:86-92` | 任何 `Exception` 统一吞成 `INTERNAL_ERROR: Python API 执行失败`；只做 `LOGGER.exception`，冻结 exe 无控制台、无 FileHandler ⇒ traceback 丢失 |

窗口的 WORKSPACE 是 `~/.rpa-core/workspace`，用户的流程在 `D:\…\rpa-test\workflows\`
⇒ **确定性越界**，不是玄学故障，也不是 0.1.2 引入的回归。本机隔离工作区真跑证实：
相对路径 `ok:true`、中文工作区 + 中文绝对路径 `ok:true`（中文不是原因）、工作区外绝对路径
`ValueError → INTERNAL_ERROR`。`rpa.bootstrap` 成功（窗口列得出命令和流程），桥本身是好的。

## 二、修复（不删越界守卫，只让两边共用一个真值源）

1. **会话侧根对齐**：`studio.py gui` 把 `--root` 写进子进程的 `RPA_WORKSPACE`；连已有
   常驻服务时从 `/health.root` 取。窗口 `api.py::_workspace_root()` 本来就最优先读这个 env。
2. **窗口独立启动的兜底**：`api.py::_session_root()` 读 `endpoint.json` 的 `root`（会话服务
   自己声明的授权根），与 `WORKSPACE` 一起进 `_allowed_roots()`。绝对路径落在任一允许根即放行
   —— `Path(root) / absolute` 在 pathlib 里直接返回 absolute，守卫逻辑不用分叉。
   **窗口下拉框的相对路径仍只走 WORKSPACE**，安全边界没有放宽到任意路径。
3. **结构化失败**：`_flow_path` 的两个 `raise ValueError` 换成 `BridgeError("BAD_PATH", …)`，
   消息带传入路径、窗口认的根、以及可照抄的 `studio.py serve --root <目录>` / `gui --root`。
   桥层对 `BridgeError` 有专门分支，会把 `code` 原样带回 JS，不再吞成内部错误。
4. **前端不吞原因**：`openFlow` catch 返回 `{ok:false, error:{code,message}}`，界面用
   `white-space: pre-wrap` 展示多行修法，`open-failed` 事件带上真 `code/message`。
5. **Agent 立刻拿到结论**：`studio.py open --wait` 的等待集合加 `open-failed`，收到即返回
   退出码 2 + `BAD_PATH` + 修法，不再等满超时把「窗口拒绝」误读成「用户没动」。
6. **冻结态日志落盘**：`api.py` 模块级给 `ppx_py.bridge` 的 logger 挂 `RotatingFileHandler`
   （`~/.rpa-core/editor.log`，可用 `RPA_EDITOR_LOG` 改），幂等、失败不影响启动；
   `RPA_EDITOR_SELFTEST` 输出补 `allowedRoots / sessionRoot / logPath`。

## 三、契约判据（动态真跑，不是扫源码）

`.harness/scripts/check_editor_workspace_contract.py`，13 条，接入 `check_all.py`：

- 相对路径 / 同根绝对路径 / 中文会话根绝对路径三种形态真跑 `_flow_path` 与 `open_flow`；
- 越界与「文件不存在」都是 `BAD_PATH`，消息含根、修法、且不含内部术语；
- **桥等价包装**复刻 `bridge.py` 的 `except BridgeError → except Exception` 顺序，动态证明
  「换回 ValueError 就会重新变成 INTERNAL_ERROR」；
- **守卫真在**：monkeypatch 掉会话根后中文路径必须被拒（防「中文能打开」是守卫失效的假绿）；
- `cmd_gui` / `cmd_open --wait` **打桩下游真跑函数**，断言注入的 env 值、等待集合、返回码与回传体；
- 前端接线闸 + 源码接线闸（AST 检查 `_flow_path` 里没有裸异常）。

夹具全部落在仓库内 `.harness/spike/_m67_contract/`（沙箱不允许 Python 写系统 temp），
**不删文件**（删除守卫按 turn 累计）。

## 四、负向验证（9 组，全部精确红）

`.harness/spike/probe_editor_workspace_contract.py`：清单 + 文件末尾哨兵双凭证，
`assert old in s` 确认注入生效，`try/finally` 逐组还原并核 md5，还原后复跑 13/13 全绿。
期望失败集按推理写、跑完逐条比对，结果 `NEGATIVE VERIFICATION PASSED (9/9)`：

| 注入 | 精确红在 |
|---|---|
| ① 越界异常换回裸 ValueError | 越界 BAD_PATH / 桥等价包装 / 守卫真在 / 源码接线闸 |
| ② BAD_PATH 退化成「路径错误」 | 越界 / 文件不存在 / 桥等价包装 |
| ③ 不再注入 `RPA_WORKSPACE` | 两条 cmd_gui |
| ④ 前端又把失败吞掉 | 前端接线 |
| ⑤ `--wait` 丢掉 `open-failed` | --wait 回传 / 源码接线闸 |
| ⑥ 日志 handler 不挂 | 异常落盘 |
| ⑦ 越界守卫改成恒真 | 越界 / 桥等价包装 / 守卫真在 |
| ⑧ 读侧：会话根不进允许根 | 中文会话根 |
| ⑨ 连已有服务不读 `/health.root` | cmd_gui 连已有服务 |

★ **本轮自己写出的假绿灯（单独记）**：`case_exception_logged` 初版用固定 sentinel 查
「日志里有没有 traceback」。日志是滚动追加的，上一轮写过的 sentinel 会顶替本轮 ⇒ 摘掉
`logger.addHandler` 后判据仍 13/13 全绿。修法： sentinel 加 `uuid4()`，且**只看本轮新增字节**
（`read_bytes()[before:]`）。这是「聚合/累积状态掩盖中间态」的同族坑，与门禁 skill 里
「收尾兜底还会再执行一次」一致。修完该组才精确红。

## 五、发版与验收边界

- `ppx.toml` bump `0.1.3` → tag `editor-v0.1.3` → CI 构建上传 `RPA-Editor-win64.zip` + `.sha256`。
- **两侧必须一起更新**：窗口侧（`api.py` / `main.js`）随 Release 升级；会话侧
  （`studio.py` / `SKILL.md`）要 `npm i -g …` + `rpa-skill install --target dsh --force`。
  只升窗口仍能拿到可行动的 `BAD_PATH`，但根没对齐时打开仍会失败。
- dsh 侧起会话要明确根：`studio.py serve --root <工作流所在目录>`，或一条命令
  `studio.py gui --root <目录>`。没给根时窗口只认自己 WORKSPACE，`BAD_PATH` 会写明该放哪。
- 验收现场：`~/.rpa-core/editor.log`（或 `RPA_EDITOR_SELFTEST=1` 的输出）能直接看到
  `allowedRoots` 与真实 traceback；`open --wait` 在拒绝时秒回退出码 2，不再空等。

## 六、没做的（如实挂账）

- 键盘 Tab 焦点到位后按 Enter 触发捕获（M66 P1）；
- `capture browser` 那 5 个被实现忽略的 CLI 参数、显式 `hybrid` 入口（M66 P1）；
- 窗口自检入口没有做成 GUI 按钮，仍靠环境变量触发。
