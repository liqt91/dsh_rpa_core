# ADR 0016：GUI 为唯一主力形态（未来技术路线）

- 状态：**已接受（2026-09-17）**
- 日期：2026-09-17
- 关联：ADR 0014（编辑器宿主形态二 —— 方案 E 原生 PySide6 桌面客户端）、ADR 0015（Native Messaging 扩展通道）、ADR 0010（编辑器宿主形态一 —— 保留 Web，已被本决策降级）
- 目的：明确后续功能投入的主入口，避免 GUI 与 Web 两套前端长期并行维护。

## 1. 背景

ADR 0014 选定了「原生桌面 GUI（PySide6）」作为编辑器宿主（方案 E），此后 GUI 切片
（指令树/画布/参数表单/保存闭环/运行控制/元素库/数据表/撤销重做/插件引导/悬浮窗…）
持续补齐，已达到与 Web 编辑器功能对齐（feature `gui-web-parity`）。同时：

- Web 编辑器（`rpa-core devserver` + `devserver/static/`）是零构建形态的产物，长期并行
  维护两套 UI（Qt 与 vanilla JS）成本高、且已在功能与交互上落后于 GUI。
- 扩展通道迁移 Native Messaging（ADR 0015）后，**GUI 独立运行不再需要任何 web 服务器**
  （编辑器能力进程内复用 `DevServerApp` 等库；运行走 `rpa-core run` 子进程；扩展经浏览器
  按需拉起的 bridge host）。

## 2. 决策

**GUI（`rpa-core gui`）是未来唯一主力形态。**

1. 新增能力**优先且默认**落 GUI；Web 编辑器退为**可选形态**，不作为功能对齐目标。
2. 不再为 Web 编辑器补齐 GUI 已有能力；devserver 相关代码只在**成本极低**（如一行前端
   路由改名）或**后端能力层复用**（`DevServerApp` 作为库）时顺带维护。
3. 后端能力层（`model` / `catalog` / `compiler` / `runtime` / `executors` / `workers` /
   `extension_exec` / `local_transport`）与宿主形态无关，继续是唯一事实来源；GUI 与
   devserver 都只是它的宿主。
4. GUI 运行不得引入对 devserver 进程或任何 web 端口的**运行时**依赖（安装期路线如
   `--policy` 托管 CRX 除外，属一次性操作）。

## 3. 后果

**正面**

- 单前端投入，交互与功能只维护一套；GUI 可直接使用桌面能力（全局热键、窗口控制、置顶
  悬浮窗、原生对话框）。
- 消除「GUI 与 Web 功能漂移」类问题（此前的 `gui-web-parity` 差异矩阵即由此产生）。

**代价 / 约束**

- `devserver/static/` 的 Web 编辑器不再演进，其 E2E/合同测试随功能冻结（**不删除**，
  保留可用形态与既有覆盖；若将来确认无人使用再单独评估下线）。
- 面向非开发者/远程场景的「零安装 Web 形态」不再是主路径；如需，属后续独立议题。
- 开发环境：GUI 是主力形态，故同步命令固定为 `uv sync --all-groups --extra gui`
  （`--all-groups` 不含 extras，漏掉 `--extra gui` 会把已装的 Qt 依赖清掉）。

## 4. 被否备选

- **继续双前端并行**：成本高、漂移持续，收益不明。
- **Web 为主、GUI 为辅**：与 ADR 0014 已落地的方向相反，且放弃桌面能力。
- **删除 Web 编辑器**：破坏既有零安装形态与部分测试覆盖，且无紧迫收益；改为「冻结」。
