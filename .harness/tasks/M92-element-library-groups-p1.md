# M92：元素库对齐影刀 P1——分组与元数据模型先行

Status: `done`

> **2026-11-18 用户诉求**：「元素库的管理界面跟影刀的对齐，看下影刀逆向文件夹的几个 md 报告后出方案。」
> 取证后用户拍板：**贴影刀单树形态**，但**第一版只做 P1 数据模型**，界面树形交互留到 P2。

## 1. 取证结论

读了 `rea-yigndao` 的 4 份报告：`影刀元素库与流程编辑器逆向报告.md`、
`影刀元素捕获落盘数据报告.md`、`影刀元素编辑方式报告.md`、`影刀元素捕获逆向报告.md`。

- 影刀元素库是底部面板 Tab，核心是 `ListTreeView`：**分组 + 元素同树**，支持折叠、
  拖拽、右键、多选；操作全集含新建/分组 CRUD/复制/移动/单删多删/搜索/未使用/引用/远端/AI 修复。
- 数据模型：`id/name/group/framework/processName/productName/winTitle/screenshot/path/anchor/semantic/version`。
- 报告明确：WPF/XAML 展示层不必逐行复刻；对齐的是信息架构与操作闭环，不是 XML 或 IL。

我方现状：已有底部元素资产 Tab、搜索、捕获/详情/校验/删除护栏；缺真实分组、树形交互、
多选批量、状态列、缩略图、未使用/引用筛选、修复闭环。P1 先把数据地基补齐。

## 2. 本次实现（只做 P1，不动界面）

- `src/rpa_core/model/capture.py`：`ElementDescriptor` 显式新增
  `group / framework / processName / winTitle / screenshot / lastVerifiedAt / lastVerifyError`。
  ★ 顶层未知键会被 pydantic `extra=ignore` 静默丢弃，所以新字段必须显式声明；
  不迁移旧资产，缺字段就显示未分组/未知。
- `.harness/demo/ppx_probe/app/api/api.py`：
  - 分组存 `<flowDir>/element_groups.json`（`version/groups[{id,name,order,collapsed}]`）。
    **绝不放进 `elements/`**，否则会被 `listElements` 当成一条假元素。
  - 元素归属用顶层显式 `group`（组 id），文件名仍是 ref-only 权威。
  - `listElements` 扩展返回 `group/groupName/framework/processName/winTitle/screenshot/
    capturedAt/verifyCount/lastVerifiedAt/lastVerifyError`；旧资产缺字段返回 `None`。
  - 新增 RPC：`listElementGroups`、`createElementGroup`、`renameElementGroup`、
    `deleteElementGroup`、`moveElementsToGroup`、`copyElement`、`findUnusedElements`、
    `elementReferences`。
  - 新入库资产由 `_fill_library_fields` 补齐字段；复制资产不绑引用。
- 红线不变：不恢复 `writeElement`/`pickPlan`；删除仍走引用护栏；文件名仍是元素与
  `elementRefs` 的唯一接线。

## 3. 判据与验证

- 新增元素库 RPC/模型测试 4 条：分组 CRUD 与批量移动落盘、复制/未使用/结构化引用、
  新入库字段补齐且不迁移旧资产、显式字段 roundtrip。
- 契约门禁新增「元素库 P1：分组存储与显式元数据」，钉住分组文件路径、`_fill_library_fields`
  两处调用与 5 个显式字段各出现一次。
- 负向验证 `probe_m92_negative.py`：5 个注入失败集逐项相等、2 文件 md5 逐字节还原、
  还原后三组判据复跑全绿。
- 全量 `check_all.py`：`FULL GATE PASSED` + `rc=0`，**1005 passed / 20 skipped / 2 xfailed**。

## 4. 边界 / 下一版

- **本版没有改界面**：P2 才把分组树、多选、右键、拖拽、批量条接上这些 RPC。
- 截图字段先有权威与类型，捕获时截图落盘策略留到 P3。
- 校验状态字段先有权威；让 `verifyElement` 写回状态留到 P3。
- AI 修复与远端共享库不在本版范围。
- PPX 原型是三分支共享资产，本次未跨分支同步；代码按用户规矩只留本地、未提交未 push。
