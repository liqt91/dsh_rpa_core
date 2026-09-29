# ADR-001：元素引用模型（B1 定稿）

- 状态：Accepted（2026-09-29，维护者拍板）
- 关联：docs/yingdao-gap-catchup.md §B1/M46；M39 §8.4-② 实测

## 背景与问题

流程节点的元素参数是**值拷贝**（`with.selector` 存 css 串）：在元素库修改主定位后，
已插入指令不变、其自愈候选按旧 css 反查**静默失效**（实测候选 2→0 条且无提示）；
桌面腿连自愈都没有。「修复元素 → 引用处自动更新」（影刀核心体验）没有地基。

## 影刀实锤（2026-09-29 社区逆向，yingdao.com/community/detaildiscuss?id=911956087635619840）

- 流程定义 `*.flow.json` 里指令参数存**元素编码**（组编码+元素 ID，如
  `element_481a49d6>订单图标`）；编码版 API 同样传引用串；
- 选择器真值独立存放于 `xbot_selectors/`（按组编码分子目录），运行期解析。

## 决策

采纳**引用模型**（与影刀同构）：指令引用元素名，值只在元素库存一份，运行期解析。
否决「维持现状+警告」与「仅保存时同步改写」（后者体验等价但运行期无引用概念，
重命名/换绑/相似元素成组永远缺地基）。

## 本期实现（M46 S1，分阶段的第一步）

- **schema 双写**：`ActionNode.elementRefs`（alias `elementRefs`，`参数键 → 元素名`）。
  节点仍保留值快照在 `with`——旧文件零迁移、元素删除时可回落。
- **运行期解析**：`_execute_action` 在 schema 校验前把引用键的值换入元素库最新值
  （`runtime/element_refs.py`；reader 读 `flow_dir/elements/<名>.json`，
  runtime 不依赖 devserver）。缺元素/文件损坏 → **回落快照 + 落
  `elementRefFallback` 事件**（可观测回落，与自愈候选的静默失效相反）。
- **GUI 写入口径**：`_insert_element` 双写（值进 `with`、引用进 `elementRefs`）；
  参数面板手工改引用键的值 = 用户接管，**摘除引用**（否则运行期元素库值会盖掉
  手工输入）；值未动的提交保留引用。
- 键集合封闭：`selector → selector.css`（browser）、`locator → selector.locator`
  （desktop，允许结构化 dict）。与插入路径的写入口径一一对应。

## 后续阶段（登记，不在本期）

- S2：元素库编辑器保存主定位时的「引用影响面」提示（引用节点计数 UI）；
- S3：删除/重命名元素的对账事件；canvas 摘要显示引用徽标；
- S4：B2 桌面自愈搭 `flow_dir` 反查通道（复用本模块的 reader）。

## 判据（负向验证已做）

`tests/contract/test_element_refs.py`（9 条）+ `test_gui_panels.py` 追加 2 条：
运行期取新值 / 两次运行间改元素库自动生效 / 缺元素回落+事件 / 旧格式零事件 /
模型兼容与 extra=forbid 不开口子 / GUI 双写 / 手工接管摘引用。
注入「解析不覆盖」→ 恰 3 红；注入「回落不落事件」→ 恰 1 红；
注入「GUI 不写引用」与「提交不摘引用」→ 各 1 红；均无 error，逐字节还原复绿。
