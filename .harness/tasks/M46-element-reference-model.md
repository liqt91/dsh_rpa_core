# M46：语义决策 + 引用模型（B1 定稿与 S1 实现）

Status: `done`

Plan: docs/adr-001-element-reference-model.md

## 目标

B1 引用语义三选一定稿并落 ADR；实现引用模型第一阶段（schema 双写 + 运行期解析 +
旧文件兼容 + GUI 写入口径），负向验证收口。

## 决策记录

- 2026-09-29 维护者拍板：**② 引用模型（与影刀同构）**。依据：影刀项目源码逆向
  实锤（flow.json 存元素编码、xbot_selectors/ 存真值、运行期解析）。
- ADR：`docs/adr-001-element-reference-model.md`。

## S1 清单

- [x] `ActionNode.elementRefs` 字段（alias elementRefs，extra=forbid 不开口子）
- [x] `runtime/element_refs.py`：element_value_for_key / make_element_reader /
      resolve_element_refs（缺元素回落快照，runtime 不依赖 devserver）
- [x] orchestrator `_execute_action` 校验前解析；回落落 `elementRefFallback` 事件
- [x] GUI `_insert_element` 双写；参数面板提交时手工改值摘引用、值未动保留
- [x] 判据：test_element_refs.py 9 条 + test_gui_panels.py 2 条；负向验证 4 注入全命中

## 残留（后续阶段）

- S2 引用影响面 UI（改主定位时提示引用节点数）；S3 删除/重命名对账事件；
  S4 B2 桌面自愈复用 reader（flow_dir 反查通道）。
