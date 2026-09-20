# M28 元素自愈与执行前预检

状态：`active`

关联：M10（元素候选 + 语义特征，已落地数据模型）、ADR 0013（浏览器执行收敛为扩展单通道）、
`docs/element-self-healing-plan.md`（本计划的调研依据与结论）
前置：`selector.candidates` 与语义 metadata 已在捕获端落盘（M10）；运行期只消费 `selector.css`

## 背景

站点改版即失效是我们元素文档的结构性弱点：捕获端已经存了**备选候选 + 语义特征**，但运行期
（`executors/browser.py` → `extension/background.js` 的 `page.call`）**只用单个 `selector`** 做
`querySelector`，既没有回退、也没有执行前校验——点在被覆盖元素上还会**静默点到遮罩层**。

外部调研（`browser-use/jev-ultrafast`，MIT）给出了这条路的后半段：动作时按节点身份重新解析 +
**校验通过才执行**（可见性/禁用/遮挡），失败显式报错而非盲重试。详见
`docs/element-self-healing-plan.md`。

## 任务（切片）

- [ ] **S1 运行期消费候选（元素自愈）**
  - `page.call` 的定位环节改为：先 `selector.css`，未命中/校验不过时按 `selector.candidates`
    的**稳定性顺序**逐个尝试；命中的候选与顺位进入执行证据（可观测「这次靠哪个候选救回来的」）。
  - 元素资产 → 命令参数的传递方式需要落地：当前 `browser.click/input/...` 只接 `selector` 字符串，
    要么扩展参数（如 `candidates` 由编辑器在插入元素时一并写入节点参数），要么按元素资产名解析。
    实现前先定这一处契约（见「风险 / 注意」第 1 条）。
  - 验收：站点改版导致主选择器失效、但候选仍可命中时，流程**不再失败**且证据里标明所用候选。
- [ ] **S2 执行前预检与错误分类**
  - 预检（扩展侧，注入前执行）：`isConnected`、`disabled` / `aria-disabled`、`inert`、
    `checkVisibility({checkOpacity:true, checkVisibilityCSS:true})`、rect 非零且在视口内、
    **`elementFromPoint` 包含性（遮挡拒绝）**。
  - 失败分类为稳定错误码：`ELEMENT_NOT_FOUND` / `ELEMENT_COVERED` / `ELEMENT_DISABLED` /
    `ELEMENT_NOT_VISIBLE`，并把「谁挡住了」写进 details（便于排查）。
  - 验收：被遮挡/隐藏/禁用的目标**不再静默误点**，报错可读且可定位。
- [x] **S3 参数漂移修复（缺陷，提前于增强）**（2026-09-20 done）
  - `keyIntervalMs` 在扩展通道真正生效（逐字输入按间隔；实现上需让 `page.call` 支持异步等待，
    或改为「分片逐字 + 由执行器按间隔多次调用」——实现前定这一处契约）。
  - `clipboard` 模式实装（或明确报「未实现」而不是静默退化成逐字）。
  - 验收：设了间隔的逐字输入确实有间隔；`clipboard` 有真实粘贴语义或明确失败。
- [ ] **S4 度量与边界文档**
  - 记录每步**扩展通道往返数 + 耗时**作为基线（防性能回归）；把 MVP 边界（shadow DOM / iframe /
    canvas / 上传下载 / 弹窗标签页 / 嵌套滚动 / 键盘控件）写成文档，避免被反复当成缺陷追问。
  - 验收：full gate 通过；文档落地。

## 不做（已定案 2026-09-20）

- **新增 `mode: "insert"`**（select-all + 单次插入）：其输入方式是速度/框架兼容技巧，**不是反检测
  手段**（且不产生按键事件）；我们已有 `fill/type/clipboard` 三档，不增第四档。
- **后台标签页焦点模拟**：影刀无此设计（其体系要求目标窗口前台有焦点），且我们无 CDP；
  留作「后台标签页可靠性成为实际问题再评估」。

## 验收

- 主选择器失效时能靠候选自愈，且证据可追溯；执行前预检杜绝静默误点；
- 已声明参数不再「声明了不生效」；每切片：契约/单测 + `check_all.py` 全门禁通过；PROGRESS 追加一行。

## 风险 / 注意

1. **候选的来源契约**（实现前必须定）：候选目前存在**元素资产文档**里，而命令参数只接 `selector`
   字符串。三种走法——(a) 编辑器插入元素时把 `candidates` 一并写进节点参数；(b) 命令参数改为
   接元素资产名，由执行器查资产文档；(c) 扩展侧按 `selector` 反查页面语义自行生成候选。需要
   按「不改运行期语义、不引入隐式耦合」来定，倾向 (a) 或 (b)。
2. **自愈的副作用**：候选命中可能命中**语义相近但非目标**的元素（如页面上有两个同名按钮）。
   因此候选顺序必须按稳定性排序、并在证据里如实记录 matchedCount 与所用候选，必要时把
   「多候选命中且不唯一」当作显式错误而非静默选择。
3. **异步/间隔实现**：`keyIntervalMs` 与预检都要在扩展侧等待，而当前 `page.call` 是「一问一答」；
   改动会触及扩展协议（`background.js` ↔ `browser_ext.py`），需保持既有命令的向后兼容。
4. **不影响既有契约**：`browser.*` 命令的 `output_schema`/effect 语义不变；新增错误码要进
   manifest `errors` 与 i18n 文案，并补契约测试。
