# M52：选择器优选（唯一 · 稳定 · 短）

Status: `active`

> **S1 已落地（2026-10-10）**：桌面 locator 择优引擎（唯一性硬门 + penalty 最小化）+
> `capture_at` 接线 + 判据 11 条 + 负向验证 6 注入全命中。详见文末「S1 实施记录」。
> **S2 已落地（2026-10-10）**：抽出共享排序引擎 `model.selector_ranking.rank_candidates`
> （两腿共用同一函数对象），浏览器候选先验表 + `_element_candidates` 接线 + 判据 14 条 +
> 负向验证 6 注入全命中。详见文末「S2 实施记录」。
> **S3 部分落地（2026-10-10）**：捕获确认框（GUI）**只读列出全部候选 + 分数**
> （候选与分数在**捕获期算好落盘**）；判据 6 条 + 负向验证 8 注入全命中。
> 详见文末「S3 实施记录」。
> 后续切片：S3 余项（「推荐 / 理由」**可采纳**入口）、S4 M51 候选扩容消费。

> 本轮**不实现**，只立范围与验收口径（2026-10-10 维护者提问「影刀的 AI 判断元素选择器，我们能不能做」派生）。

Plan: 影刀「智能元素」对标（2026-10-10 外部核查）／由 M28 风险项「候选顺序必须按稳定性排序」（未实现）
与 M51 ②「候选生成面过窄」派生，升级为独立里程碑。

## 为什么独立立项（与 M51 的分界）

M51 与 M52 同源于「元素不稳定」，但回答的是**两个问题**：

- **M51 = 供给面**：换一种定位语言（XPath）、多生成几套候选 —— 答「除主 css 外**还有没有别的方案**」。
- **M52 = 优选面**：在已有候选里挑出唯一、稳定、短的那一个，并说清**为什么推荐它** ——
  答「已有的方案里**哪个最好**」。

M51 任务单开篇已声明「本轮不实现，只留证据与范围」，且把 XPath 与候选扩容合成一片的理由是
「同一根（还有别的定位方案可选吗）」。选择器优选是**消费侧**的另一个问题，并入 M51 会让
「界面回归 vs 定位语义回归分不开」的顾虑重新出现 ⇒ 单独立项。

**依赖是软的**：M51-B（候选扩容）决定优选的上限，但 M52 的打分/排序/落盘/推荐 UI
可以在**当前候选面**（浏览器 id + 7 属性 / 桌面 3 候选）上先跑通，M51 落地后自动受益，
不必等 M51 收口。

## 影刀在做什么（外部核查，2026-10-10）

- 影刀有两个易混功能，**别混**：
  1. **智能元素**（设置 → 启动项 → 捕获元素时使用 AI 辅助）——**捕获期**生成更稳定的元素路径与属性，
     官方表述为「自训练 AI 模型深度理解页面结构」，可观测能力＝自动忽略易变属性（随机 class）、
     优先稳定属性（标签文字 + 固定 ID）。**这就是本里程碑对标的对象。**
  2. **AI 辅助定位**——**运行期**「未找到元素」时用图像 + 语义描述兜底并推送一键修复。
     我方已定性为**自愈第三级（E2）**，属另一条线（依赖 B1/B2 自愈通道），**不在本片范围**。
- 本质拆解 = **候选生成 × 唯一性实测 × 打分排序**。「AI」主要体现在**评分/选择层**
  （学的是「哪类属性组合更稳」）。这一层用**确定性权重**即可复刻绝大部分，且更可解释、可审计，
  契合本仓「无 LLM 决策层、可复现」定位（`docs/element-self-healing-plan.md` §2、
  `AGENTS.md` 规则 6）。

## 我方现状（2026-10-10 调研，带证据）

| 环节 | 浏览器腿 | 桌面腿 |
| --- | --- | --- |
| 候选生成 | 有 `content.js::candidatesFor`（L197；`CANDIDATE_ATTRS` L193，id + 7 属性，逐条实测 `matchedCount`），**面窄、无唯一性过滤、无打分**，顺序＝固定属性优先级 | 几乎无：hover 只出 1 条（`desktop_agent._describe_info` L481）；point 出 3 条候选，**选完即弃、不落盘**（`capture_at` L567，`best_locator` L603；规则＝唯一优先、否则命中数最小） |
| 唯一性实测 | **有**活体通道（`capture/verify.py::ElementVerifier`；M47 校验元素/预览） | 仅捕获期 `_verify_in_root`（L398）；编辑器里无活体 |
| 打分 / 排序 | 无（数组固定顺序，非算法排序） | 无（朴素启发式，无权重） |
| 长度度量 | 无 | 无 |
| 候选落盘 | 有（`selector.candidates`） | 无 |
| 采纳入口 | 有（候选点选提升，M39 ②/M44） | M50 起可编辑 `path`/`anchor` |

- **现成底座**：`DesktopExecutor._find(window, locator) -> list[Any]`（`executors/desktop.py` L969）
  —— 两执行器都返回**命中列表**，唯一性＝`len(matches)` ⇒ 可**零新增底层能力**地把候选逐个喂进去
  求命中数（捕获期 `_verify_in_root` 只是它的窄化版）。
- **已登记未实现的欠账**：M28 风险项「候选顺序必须按稳定性排序」；M51 ②「`candidatesFor` 面过窄，
  现代组件候选恒空 ⇒ 运行期自愈实际永不生效」。

## 开源打分实现调研（2026-10-10）

**结论先行**：业界「选最优选择器」的主流口径**不是「多因子加权求和」**，而是
**「唯一性硬门 + 惩罚（penalty）最小化」**——唯一命中是**准入门槛**（不满足就继续加具体度直到唯一），
在**唯一解集**里选**总 penalty 最小**的那条。**「稳定性」就是 penalty 表本身**；
**「长度」被 penalty 天然覆盖**（低 penalty 的分量本就更短），**不需要单列一维**。

| 实现 | 机制 | 可借鉴 |
| --- | --- | --- |
| **@medv/finder**（antonmedv/finder，~1.5kB，MIT，会话回放/埋点常用） | BFS 上溯逐级生成候选，**penalty 表**：`id=0 / class=1 / attribute=2 / tag=5 / nth-of-type=10 / nth-child=50`；按总 penalty 排序、**遇唯一即停**；`optimize()` 删中间路径段保持唯一以缩短；超时**回退 nth-of-type 保底** | **penalty 表＝稳定权重**；唯一性＝硬门；长度并入 penalty；可插拔 `idName/className/tagName/attr` 谓词剔除框架生成名（`ember*`/`is-*`） |
| **optimal-select**（Autarc/optimal-select，@movable 维护） | 最短路径优先；默认**忽略易变属性** `style` / `data-reactid` / `data-react-checksum`；可配 `priority`（属性顺序）、`ignore`（正则剔除） | 「易变属性黑名单」的具体清单；`optimize(selector, elements)` 缩短接口 |
| **Robula+**（cyluxx/robula-plus，学术算法 JS 版） | BFS 顺序应用 7 个变换（`*`→tag → 加 id → 加 text → 加属性 → 属性组合 → 位置 → 层级），**按鲁棒性排优先级**；**白名单** `name/class/title/alt/value`、**黑名单** `href/src/onclick/style/width/height`；`document.evaluate` + SNAPSHOT 验唯一 | **属性白/黑名单**直接对应我们的稳定 penalty 表；印证「顺序加固直到唯一」 |
| **Playwright codegen** | 定位器阶梯 `role+name > label > placeholder > text > testid > css/xpath`；明令**不用 class 链 / nth-child / XPath** | 「**可访问语义优先**」——最高稳定档是**语义标签**（label/aria/name），而非 id（id 可能是框架生成的哈希） |
| **css-selector-generator-benchmark**（midoalone） | 横比 8 个库；结论：optimal-select 最短，**Chromium `DOMPresentationUtils` 常产非唯一选择器** | 我们的 `content.js::pathEntryFor` 与 Chromium 的 CSS path 生成同类；**「生成器」≠「优选器」，择优是独立一步** |
| （边界，非捕获期）**Similo / VON Similo LLM / ATA-QV** | 相似度 / 邻居三角 / LLM 判定来**重新定位**已失效元素 | 属**运行期自愈**（M28 / E2），**不并入 M52** |

**对 M52 的三条修正**：

1. **唯一性必须是硬门（gate），不是加权因子**：先按「能否唯一命中」筛，再在**唯一解集**里比 penalty。
   全不唯一时才退化为「命中数最小」（沿用桌面 `capture_at` 现有规则）。
2. **「三因子加权」改为「penalty 最小化」**：penalty 表＝稳定性先验，长度不单列。这样**只有一个可调
   对象（penalty 表）**，比三套权重好校准、好解释。
3. **稳定表要识别「像哈希的 id」**：Playwright/Robula+ 的口径是**语义标签优先**；`automationId`/`id`
   若是框架生成的哈希串（`css-1x2y3z`、`ember123`…），应**降权**而非给最高分——比「id 最稳」更细。
   配「可插拔过滤谓词」剔除形如随机串的值。

参考：npmjs `optimal-select` · GitHub `Autarc/optimal-select` · DeepWiki `antonmedv/finder` §4.1 Core Algorithm ·
GitHub `cyluxx/robula-plus` · GitHub `midoalone/css-selector-generator-benchmark` · `arXiv:2310.02046`（VON Similo LLM 综述）。

## 范围（待实现）

- [ ] **A.（降级为副产品）候选随元素资产顺带落盘**：**不作为本片独立目标**（维护者 2026-10-10
  判定「鸡肋」）。仅当 B/C/D 的实现**本就产出**候选列表时顺带写入；**不专门设计候选存储结构、
  不加迁移、不进契约**。
- [x] **B. 打分函数（核心，新权威逻辑）**（S1+S2 已落地：桌面 `locator_penalty` / 浏览器
  `web_candidate_penalty` **共用引擎** `model.selector_ranking.rank_candidates`）：**唯一性硬门 + penalty 最小化**——
  ① 先用命中数筛出「唯一解集」（不唯一则退化为「命中最少」）；② 解集内取 **penalty 表**总和最小者。
  penalty 表＝稳定性先验（语义标签 / 固定 id / 测试属性＝低 penalty；class 组合、tag、
  `nth-of-type`、`nth-child`、`foundIndex` 递增＝高 penalty），**易变属性黑名单**直接排除
  （`style`、动态/哈希 class、像哈希的 id）。**必须抽成共享纯函数**（照 M50
  `model.desktop.prune_locator_steps` 先例：捕获侧与编辑器共用一份），并配**成对判据**
  「喂同一候选集，两端排序结果逐字节相等」——光有两份各自绿的判据不够。
- [ ] **C. 枚举器**：候选 → 活体命中数。浏览器复用现通道（M47）；桌面**补活体计数**
  （优先复用 `DesktopExecutor._find` + `len`，避免新造轮子）。
- [ ] **D. GUI**：编辑器/确认框标「**推荐 / 备选 / 理由**」，一键提升为主动选择器
  （衔接 M50 的 `path`/`anchor` 改写入口与 M39 ② 的只读候选区块）。
- [ ] **E.（依赖 M51）候选生成扩容**：M52 **只消费不重复实现**；M51-B 落地后优选自动受益。

## 验收口径（判据 + 负向验证）

> 切片落地时钉具体 nodeid；此处先定**必须有的判据形状**。

- **打分纯函数**：三条硬判据——① **唯一命中恒胜**（唯一性为**硬门**，不是加权因子）；
  ② 全不唯一时退化为「命中数最小」；③ penalty 相同时按**稳定序**（不依赖集合遍历顺序，防 flaky）。
- **同源判据**：捕获侧与编辑器对同一候选集产出**逐字节相等**的排序结果（钉住「只有一份权威」）。
- **长度并入 penalty**：不单列「长度」维度（低 penalty 的分量本就短）；若日后要单列，须先定口径
  （CSS 串长 vs 路径级数）再钉判据。
- **penalty 表（稳定先验）与判据分离**：表是**先验**，须经**真机采样校准**后才定阈值；判据只钉
  「表被读到了、合成生效」，不硬编码具体数值（否则调表即静默变红）。
- **负向验证**（强制）：新增/修改判据后必做注入 → 精确红 → 逐字节还原核 md5；探针起手
  `refresh_backups()`、无哨兵自检、**不接管道**（照 M40/M48/M50 教训）。
- **门禁条目「存在」≠「覆盖到」**：桌面命令 `required == ["locator"]` ⇒ 只比顶层键名的 i18n 门禁
  **扫不到 locator 内部字段**；打分涉及的字段清单事实源取**模型字段别名**，不取手抄的表。

## 依赖

- **软依赖 M51-B**（候选面宽窄决定优选效果；当前候选面可先跑通）。
- 复用：M47 活体通道（浏览器）· M50 编辑器改写入口 · M28 自愈候选消费面
  （**编辑期优选与运行期回退共用同一份排序候选**，只多一个「谁排第一」的打分）。

## 与「捕获相似元素」的关系（边界，2026-10-10）

维护者观察：影刀「捕获相似元素」与「重新捕获」**是同一个元素编辑器的两个页签**（截图实证），
差异只是打分算法。**评估：入口合并成立，但「只差打分算法」低估——实为四层，打分只是其中最表层、
且恰好是唯一可共享的一层。**

| 层 | 唯一元素 | 相似元素（一组） | 差异性质 |
| --- | --- | --- | --- |
| **目标函数** | 命中数 `== 1` 且总 penalty 最小 | **命中集合 `==` 样本集合**（且同构）且 penalty 最小 | 判据口径变化（非权重微调）；**引擎可共享** |
| **数据模型** | 单个 locator | **成组**（group：成员定位器 + 循环变量） | 新模型（`ElementDescriptor` 现无 group） |
| **捕获交互** | 单点捕获 | **二样本**（选第 2 个让系统识别成组） | 新交互 |
| **消费面** | 单次操作 | **循环指令**（批量） | 新指令 |

- **唯一可共享的两层**：① **编辑器入口**（影刀同一对话框两 tab；我方 M44/M50 已是同一
  `ElementEditorForm`，加「模式」开关成本低）；② **penalty 引擎**（开源先例：optimal-select 的
  `getSingleSelector` / `getMultiSelector` 就是**同一套属性/penalty 逻辑 + 两种模式**）。
- **不可省的三层**：数据模型（成组）、捕获交互（二样本）、消费面（循环指令）——
  即 `docs/yingdao-gap-catchup.md` §2.4 / §3 D4 的「**三件套**」，代价「重」，
  依赖 B1（引用语义）+ C1（活体）。
- **判据分界的一句提示**（截图实证）：影刀提示「已找到 30 个元素（如需定位**唯一**元素，请精简节点
  或添加锚点）」——**同一句提示，唯一模式视 `count>1` 为警告，相似模式视 `count>1` 为目标**。
  故差异不是「penalty 表调一调」，而是**目标函数**从 `count==1` 改成 `count==|samples|`（含同构校验）。

⇒ **本片（M52）只做唯一元素优选**；相似元素建议**拆两票**：
票 1（低，可搭 M52）= 编辑器入口模式开关 + 组选择器打分（把 penalty 引擎从单元素扩到多元素，
照 `getMultiSelector`）；票 2（重，需独立立项）= 成组模型 + 二样本交互 + 循环指令三件套。

## 风险 / 明确不做

- **「稳定」不能离线判定**：只能「属性类型先验 + 捕获期实测」；**跨会话唯一性不保证**
  （`controlId` 每次进程启动都漂；动态页面）。
- **候选扩容牵动自愈**：元素资产是「值拷贝 + 按 css 反查」，改主 css 会让自愈候选静默失效（M51 §⑤）。
- **桌面活体通道的坑**：QProcess/焦点竞争（见 M40/M48 挂账）。
- **不做**：① 运行期 LLM 生成/执行（规则 6）；② AI 辅助定位（运行期图像语义兜底，属 E2）；
  ③ 候选生成扩容本体（属 M51）；④ **相似元素成组**（D4 三件套）——本片只共享「编辑器入口」与
  「penalty 引擎」两层，不碰数据模型 / 二样本交互 / 循环指令（见上节）。

## 相关证据（2026-10-10）

- `extension/content.js:193` `CANDIDATE_ATTRS` / `:197` `candidatesFor` / `:141` `pathEntryFor` / `:475` `buildDescriptor`；
- `src/rpa_core/capture/desktop_agent.py:567` `capture_at` / `:603` `best_locator` / `:481` `_describe_info` / `:398` `_verify_in_root`；
- `src/rpa_core/executors/desktop.py:969` `_find` / `:987` `_find_in` / `:939` `_narrow_by_path` / `:61` `DesktopExecutor`；
- `src/rpa_core/model/desktop.py`（`DesktopLocator` / `prune_locator_steps` / `LOCATOR_STEP_KEYS` / `MATCH_MODE_FIELDS`）；
- `src/rpa_core/model/capture.py`（`ElementDescriptor.selector` 的 `candidates` / `matchedCount`）；
- 台账：`.harness/tasks/M51-selector-supply-expansion.md`（②候选面过窄）·
  `.harness/tasks/M28-element-self-healing.md`（「候选顺序必须按稳定性排序」风险项）·
  `docs/yingdao-gap-catchup.md`（E2 定性）· `docs/element-self-healing-plan.md`（无 LLM 决策层）。

## S1 实施记录（2026-10-10）

**范围**：桌面腿 locator 择优引擎 + `capture_at` 接线（范围 B 的桌面部分）。

**交付**：

- `src/rpa_core/model/desktop.py`：新增 `LOCATOR_FIELD_PENALTIES`（表驱动稳定先验）、
  `PATH_STEP_PENALTY`、`HASHY_ID_PENALTY`、`looks_generated_id()`、`locator_penalty()`、
  `choose_best_locator()`。**字典序 `(count, penalty)`**：`count` 是第一关键字 ⇒ 唯一性硬门；
  完全并列 `min` 取首个 ⇒ 稳定序 = 输入序。
- `src/rpa_core/capture/desktop_agent.py`：抽出 `_pick_best_candidate()`（实测 → 组装 entry），
  `capture_at` 改调它（替换原「遇 count==1 即 break」循环）。
- `tests/contract/test_desktop_locator_ranking.py`：11 条判据。
- `.harness/spike/probe_m52_negative.py`：负向验证探针（6 注入）。

**证据**：

- 判据 11 passed；**负向验证 6 注入全命中**——I1 字典序反转红 3（唯一硬门 + 最小命中 +
  接线-唯一）/ I2 path 不计红 1 / I3 生成 id 恒 False 红 1 / I4 硬编码 40 红 1 /
  I5 reverse 破坏稳定序红 1 / I6 接线绕开引擎红 2；逐字节还原核 md5 一致、还原后复绿。
- 全量 `1559 tests / 2 failures / 0 errors / 23 skipped`（2 failed = **既有** QProcess 沙箱挂账
  `test_gui_command_matrix` 两条，与 M52 无关）。
- ruff `All checks passed!`；`check_architecture` / `check_tasks`（81 features, 1 active）/
  `check_param_consumption` / `check_error_contract` / `check_command_matrix` 全 rc=0；
  14 个 `check_*.mjs` 全 rc=0。

**实现修正（记一条）**：初版写成「uniques 显式分支 + 兜底分支」，二者**结果等价** ⇒ 冗余，
且让「唯一硬门」**不可注入**（两分支同结果，注入打不中）；简化为单条字典序 `min` 后，
注入才精确落在唯一硬门判据上。

**未做（后续切片）**：~~S2 浏览器腿候选排序~~（已落地，见下）、
S3 GUI 推荐入口、S4 消费 M51 扩容；候选落盘按维护者判定仅作副产品。

## S2 实施记录（2026-10-10）

**范围**：浏览器腿候选排序（消费 `content.js` 产出的 `candidates`）。落点定在**宿主侧**
——捕获时候选已带实测 `matchedCount`，**不改 `extension/content.js`**。

**接线点**：`executors/browser.py::_element_candidates`（运行期自愈 `_page_call_with_fallback`
按序 try 的消费面）。

**交付**：

- **新增 `src/rpa_core/model/selector_ranking.py`**（跨端共享的唯一权威）：
  - `rank_candidates(entries, *, count_of, penalty_of)`——按 `(count 升序, penalty 升序)`
    **稳定**排序；count 是第一关键字 ⇒ 唯一性硬门，并列保输入序 ⇒ 稳定序。
  - `looks_generated_id`（自 `model.desktop` 迁入）——桌面 `automationId` 与浏览器 `#id`
    **共用同一份**哈希判定，避免两套启发式漂移。
  - `WEB_KIND_PENALTIES` / `WEB_ATTRIBUTE_PENALTIES` / `WEB_HASHY_ID_PENALTY` /
    `WEB_UNKNOWN_KIND_PENALTY` / `WEB_UNKNOWN_ATTRIBUTE_PENALTY` / `WEB_UNMEASURED_COUNT`。
  - `web_candidate_penalty()` / `rank_web_candidates()`。
- **`src/rpa_core/model/desktop.py`**：`looks_generated_id` 迁出并 re-export（S1 判据 import
  不变）；`choose_best_locator` 改调共享 `rank_candidates`（对外行为不变）。
- **`src/rpa_core/executors/browser.py`**：`_element_candidates` 返回
  `rank_web_candidates(usable)`。
- **`tests/contract/test_web_candidate_ranking.py`**：14 条判据。
- **`.harness/spike/probe_m52s2_negative.py`**：S2 探针（6 注入）；`probe_m52_negative.py`
  同步更新锚点（重构后 I1/I3 落点变化——**重构必须同步探针锚点，否则静默假绿**）。

**证据**：

- 判据 **14 passed**；既有候选顺序断言（`test_browser_precheck` / `test_browser_element_fallback`）
  全部保持绿——`name`(penalty 3) < `placeholder`(5)，与 content.js 属性顺序一致。
- **负向验证**：S1 探针 6 注入全命中（I1 红 3 / I2–I5 各红 1 / I6 红 2）；
  S2 探针 6 注入全命中（I1 红 5 / I2 红 9 / I3 红 5 / I4 红 2 / I5 红 1 / I6 红 2），
  全部逐字节还原核 md5、还原后复绿。（I2 是「排序整体反转」，故红面较宽——非 error、非假绿。）
- 全量 `1573 tests / 2 failures / 0 errors / 23 skipped`（2 failed = 既有 QProcess 沙箱挂账）。
- ruff 全绿；5 组 Python 静态门禁全 rc=0；14 个 `check_*.mjs` 全 rc=0。

**同源判据（本轮最强形式）**：`desktop.rank_candidates is selector_ranking.rank_candidates`
——断言两端用的是**同一个函数对象**，不是「两份各自绿」。复制一份引擎即红。

**未做**：S3 GUI 推荐入口、S4 消费 M51 扩容；候选落盘仍仅作副产品。

## S3 实施记录（2026-10-10）

**触发**：维护者「在捕获确认框上把所有的候选和分数列出来吧，方便 debug」。

**先归因（关键事实，决定了这不是「加个展示」那么小）**：

| | 候选数据 | 分数 | 确认框是否显示 |
| --- | --- | --- | --- |
| 浏览器腿 | 有：`selector.candidates = [{kind, selector, matchedCount}]` | **无** | Web 显示 / **GUI 曾移除**（M47.11） |
| 桌面腿 | **无**：`capture_at` 内部三条定位方案，**选完即弃** | **无** | 无 |

即：**任何一端都没有「分数」**，「候选」也只有浏览器腿有。M52 的 penalty 引擎（S1/S2）
已建好，但**还没接进捕获流程** ⇒ 本轮先把「候选 + 分数」在**捕获期产出并落盘**，再在
GUI 只读展示。

**三处维护者拍板（AskUserQuestion）**：① 分数**捕获期算好落盘**（非展示期实时算）；
② 桌面候选**一并列**；③ **只改 GUI**（Web 侧本轮不动）。

**交付**：

- **`src/rpa_core/capture/desktop_agent.py`**：
  - 新增 `_evaluate_candidates(candidates, window, path_steps=None)`——实测每条候选命中数
    并给 `penalty`（`locator_penalty`），**全部保留**（含被选中那条）；`path_steps`（D1 祖先链）
    注入每条候选 locator 并计入分数 ⇒「候选之一」与最终主定位**逐字节一致**。
  - 新增 `_choose_from_scored(scored)`——净函数，把已打分的候选交给共享引擎
    `choose_best_locator`；`_pick_best_candidate` 改为两者的**薄封装**（与 `capture_at`
    走同一条「实测 → 打分 → 择优」路径，单测据它钉住接线）。
  - `capture_at`：先算 `steps`（path），再 `_evaluate_candidates(...)`，把 `scored` 落进
    `selector["candidates"]`（形状 `{kind:"uia", locator, matchedCount, penalty}`）。
- **`src/rpa_core/capture/extension.py`**：新增 `_score_candidates(descriptor)`——宿主侧用
  `web_candidate_penalty` 给浏览器候选就地补 `penalty`（只加字段、不改顺序）；`_read_loop`
  收到 descriptor 后先打分再接截图。
- **`src/rpa_core/gui/element_panel.py`**：`candidates_text` 扩展为**候选 + 命中数 + 分数**
  的 debug 视图（新增 `_candidate_locator_label` 兼容 desktop 的 `locator` 形状——否则
  desktop 候选显示空白）；`ElementDialog` 加只读 `candidates_label`。
- **判据 6 条**：`tests/contract/test_desktop_locator_ranking.py`（`_evaluate_candidates`
  全量保留+打分 / path 注入计分 / 无 window 空 / **`capture_at` 落盘接线**）·
  `test_capture_extension.py`（候选补分 + 顺序不变，走 FakeBridge 全链路）·
  `test_gui_panels.py`（`candidates_text` 显示分数与 desktop 形状 + 确认框只读展示）。
- **`.harness/spike/probe_m52s3_negative.py`**：S3 探针（8 注入）。

**两条关键设计（省掉了契约改动）**：

1. **桌面候选不需要放宽契约**：`model.capture._candidate_errors` **只在 `kind=="browser"`
   分支被调用**（desktop 的 `selector` 是自由字典）⇒ 桌面候选的 `locator` 形状不会被拦，
   `selector.candidates` 可直接复用，无需新字段、无需迁移。
2. **桌面候选自动不影响运行期自愈**：`rank_web_candidates` 只收 `selector` 为非空字符串的
   项 ⇒ 桌面候选（无 `selector` 串）被它自然忽略，两腿数据同住一个键却互不干扰。

**与 M47.11 的边界**：M47.11 移除的是**可编辑**候选 UI（`candidate_list` / `promote_button`
「提升为主定位」）；本轮恢复的是**纯只读 debug 展示**，不提供任何写回入口 ⇒ 不构成
「只读副本与可编辑列表重复」。原判据 `test_element_dialog_has_no_candidate_ui_but_keeps_data`
随之更新为 `test_element_dialog_shows_candidates_readonly`（仍钉死两个可编辑控件不存在 +
数据原样写回）。

**证据**：

- 判据 **6 条全绿**；相关既有判据（`test_gui_panels` / `test_editor_element_display` /
  `test_desktop_locator_ranking` / `test_web_candidate_ranking` / `test_capture_extension` /
  `test_element_descriptor`）**118 passed**。
- **负向验证 8 注入全命中**：I1 agent 不按表打分红 2 / I2 只留首条红 4 / I3 path 不注入红 1 /
  I4 `capture_at` 不落盘红 1 / I5 extension 不按引擎打分红 1 / I6 `_read_loop` 绕开打分红 2 /
  I7 不渲染分数红 2 / I8 确认框不渲染候选红 1；全部逐字节还原核 md5、还原后复绿 **74 passed**。
- 全量 **1556 passed / 0 failed / 21 skipped / 2 xfailed / rc=0**（重跑确认；首次 rc=139
  段错误发生在 pytest **收尾之后**——既有现象，见 M47.12 记录，与结果无关）。
- ruff 全绿；6 组 Python 静态门禁（`check_architecture` / `check_tasks` 81 features 1 active /
  `check_param_consumption` / `check_error_contract` / `check_command_matrix` + ruff）全 rc=0；
  14 个 `check_*.mjs` 全 rc=0。

**未做 / 挂账**：

- **Web 侧展示未改**（维护者只要求 GUI）：desktop 元素若在 Web 确认框打开，其候选会经
  `elementCandidateLines` 显示为 `[uia] <空> · 命中 N`（无 locator 文本）——功能不受影响，
  但属两端展示的**有意不对称**，待后续需要时一并对齐。
- S3 余项「**推荐 / 理由**（可采纳为主定位）」未做——本轮只交付维护者要的**只读 debug 视图**。

## S3 修：hover 快路径不落盘候选（2026-10-10）

**报障**：「我重启了，怎么没在捕获确认框上看到候选和分数」。

**根因（读代码取证，两处都必需）**：

1. GUI 的桌面捕获走 `HybridCaptureSession(hover=True)` ⇒ spawn
   `desktop_agent --hover --hybrid`（`gui/app.py` 3082–3084 行）。
2. `_hover_capture` 的**快路径**——「鼠标仍在 hover 最后命中的 rect 内 ⇒ 复用缓存元素，
   省一次 hit-test + DFS」——**直接 `return _describe_info(last_leaf, …)`**，
   **压根不经过 `capture_at`**。而 S3 首版把候选落盘只加在 `capture_at` 里。

⇒ 日常捕获（悬停后按 F9 / Ctrl+Click）**永远走快路径**，确认框恒无候选。S3 首版的判据
（`test_capture_at_persists_scored_candidates` 等）全绿也拦不住，因为它们只测了 `capture_at`
这一条路径——**判据覆盖了「实现」，没覆盖「用户实际走的那条路」**。

**修法**：

- 抽出 `_locator_candidates(control_type, automation_id, name)` 作**候选形状的唯一权威**
  （`capture_at` 与 `_describe_info` 共用）。此前两条路各自构造候选，是本次事故的形状。
- `_evaluate_candidates` 的 `window` 形参改造成**实测接缝** `verify(criteria, aid) -> int`，
  配 `_window_verify(window)` / `_root_verify(root_hwnd)` 两个适配器——`_describe_info` 手上
  只有根句柄、没有 pywinauto window 对象，故走 root 作用域 `_verify_in_root`。
- `_describe_info` 落盘 `selector["candidates"]`（`path_steps` 与主定位同源注入）。
- **`_describe_info` 原有的 `verifyCount` 实测路径不改**（保守：只新增展示数据，不动主定位
  的选择语义）。`candidates[0]` 恒等于它的主定位（最具体那层）——两条路的候选集同源后，
  这个恒等式由构造保证。

**判据（17 条，新增 2 + 改 3 受签名影响）**：

- `test_describe_info_persists_scored_candidates` —— **本次事故的精确回归**。
- `test_candidates_are_shared_by_both_capture_paths` —— **「两端相等」**判据：喂同一个三属性，
  两条路径产出的候选 locator 列表必须**逐字节相同**。**这条是必需的**：光有「两条路各自绿」
  的判据抓不到「两边各自漂移」，而事故形状恰恰就是「一条有、一条没有」。
- `test_evaluate_candidates_without_verifier_is_empty`（改名，覆盖 `_window_verify(None)` /
  `_root_verify(0)` 返 None）。

**负向验证（探针 8 → 12 注入全命中）**：

- I4 锚点因落盘语句新出现第二处而**从唯一变不唯一** ⇒ 收紧为含 `best_locator` 的整块锚点。
- 新增 I9 `_describe_info` 不落盘（红 2）/ I10 实测接缝断开（红 2）/ I11 候选生成器只留首条
  （红 2）/ I12 `_describe_info` 就地手搓候选（红 2，**复现事故形状**）。
- **探针自身两处加强**（都是「让探针可被审」）：
  1. 各注入登记**期望失败集**，断言「实际红 ⊆ 期望红」否则判「未命中针对性判据」——
     防「把模块打倒也算命中」（M48 I21 教训）。**刚加上即抓到我自己 I1/I2 的期望集不全**
     （逐条按推理补全，不是照抄实际：I1 还应含 `test_evaluate_candidates_injects_path_into_scores`，
     它也断言 penalty 值；I2 的切片在 `_evaluate_candidates` **唯一出口**上，还波及
     `test_candidates_are_shared_by_both_capture_paths` 与两条 `test_pick_best_candidate_*`）。
  2. 本机**退出阶段环境级崩溃**（`rc=0xC0000005`、`76 passed / 0 failed`，与 M47.12 记的
     rc=139 同一现象）会污染对照跑 ⇒ 加**有界重跑**：仅当 rc≠0 **且** failed==0 **且** passed>0
     才重跑一次；重跑 rc=0 **且 passed 数一致**才认绿，并**显式记档** `⚠ 环境级 flaky`——
     **绝不静默放行**；重跑仍不绿则按原结果上报。

**已排除「打包产物没更新」**：`dist/` 只有 `rpa_core_runtime` wheel（执行器运行时），GUI
无打包产物、从源码启动 ⇒ 重启即生效，不需要重装。

**门禁**：全量 **1558 passed / 0 failed / 21 skipped / 2 xfailed / rc=0**（+2 条新判据）；
ruff 全绿；5 组 Python 静态门禁 + 14 个 `check_*.mjs` 全 rc=0；`FULL GATE PASSED`。

**未做**：**真机复验未做**——桌面捕获要弹窗/占机，按纪律先问维护者是否占机再跑。

## M51-B 实施记录（2026-10-10，浏览器候选**供给**扩容）

**触发**：维护者贴图「没有啊 这是重启后的捕获确认界面」（**浏览器**元素、小红书 explore、
主选择器命中 30）——与上一轮修的桌面 hover 路径无关。

**诊断（先取证；结论：不是展示问题，是候选恒空）**：
- 展示已接线：`element_panel.candidates_label` 直读 `descriptor["selector"]["candidates"]`。
- 链路无裁剪：background `...descriptor` 展开 → 宿主 `_score_candidates` 只加字段 →
  `hybrid.pick` 原样返回 → 直接进对话框。
- **先排除「改扩展码没重载」这条经典解释**：trace `build 0.8.0 / stale=false`，而 `0.8.0`
  首现于 `65c2145`（2026-10-09 M47.12）、**晚于**候选收集引入的 `800b651`（2026-09-19，当时
  0.7.0）⇒ 浏览器里跑的必然含 `candidatesFor`。
- **硬证据**：`workflows/test11/elements/el_a.json`（09-29 保存）无 `candidates` 键；
  全库 `workflows/**` 扫 `candidates` **0 命中**。
- **机制**：`candidatesFor` 只收 `el.id` + 7 属性 ⇒ 「只有 class」的普通元素产 `[]`；
  而 `result_document` 写的是 `if self._candidates:` ⇒ 空列表**连键都不写**（所以元素库里是
  「键缺失」而非「空数组」）。

**连带影响（比本轮需求本身更要紧）**：运行期自愈（M28）的
`executors.browser._element_candidates` 读的正是这个键 ⇒ 候选从未存在过，**自愈回退与 S2 的
排序引擎一直空转**。

**实现（M51-B = 候选供给扩容，即任务单「范围 E 消费 M51 扩容」的前置）**：
1. `extension/content.js::candidatesFor` 新增**祖先链收缩候选**（kind=`"path"`）：
   `pathSuffixSelectorsFor(el)` 从 `pathFor(el)` 的祖先链逐级丢最外层（末 N 级，N ≤
   `MAX_PATH_SUFFIX`=3），每级再给「去 `:nth-of-type(∎)`」变体；全部走同一个 `push()`
   （实测 `matchedCount`、去重、命中 0 即丢）。产出合起来正好回答「收到哪一级才唯一命中」。
2. `model/selector_ranking.py`：`WEB_KIND_PENALTIES` 登记 `"path": 20`。**不登记不会崩**——
   会静默落 `WEB_UNKNOWN_KIND_PENALTY`（新来源永远排不上），所以配了成对判据钉住。
3. `extension/{content,background}.js` + `manifest.json` bump **0.9.0**（三方一致）：
   **改扩展码必须重载**（Load unpacked 载入即快照），bump 让维护者能一眼判断浏览器里是不是新版。
4. `scripts/check_capture_helpers.mjs`：桩 DOM 从「只认 `#id` / `tag[attr=v]`」扩到
   class / 后代组合 `>` / `:nth-of-type`——否则新候选在门禁里实测恒 0、**永远不出现**。

**判据 5 条**：JS 3 条（祖先链收缩逐级 + 去 nth 各自命中数 / 只有 class 的元素也拿得到候选 /
有 id 的元素不产祖先链候选）+ py 2 条（`path` 已登记且低于兜底值 / **跨端成对判据**
`test_capture_script_kinds_are_all_registered_in_table`：content.js 产出的 kind 必须**全部**
在宿主表中登记）。

**负向验证 7 注入全命中**（新建 `.harness/spike/probe_m51b_negative.py`——**同时驱动 node 切片
与 pytest**；**哨兵按文件后缀取**：JS 用 `//`、Python 用 `#`；node 的失败判据是
**returncode≠0 + 有 `FAIL |` 行**，不数汇总行）：I1 来源被掐红 2 / I2 去-nth 变体被删红 1 /
I3 kind 改名红 1（跨端成对判据）/ I4 只留末 1 级红 1 / I5 path 未登记红 2 / I6 EXT_BUILD
回退红 1（三方一致）/ I7 **桩 DOM class 分支恒 false 红 2**（「验证验证者」）。全部逐字节
还原核 md5、还原后复绿。

**门禁**：`FULL GATE PASSED`（**1560 passed / 0 failed / 21 skipped / 2 xfailed**）。首次跑
`check_all.py` 时 pytest 全绿、但进程退出码 **127**（退出阶段环境级崩溃，同 M47.12 的 rc=139
现象）⇒ 首条非零即 `SystemExit`、静态门禁全没跑；按纪律逐条单独取证 **19 条全 rc=0** + 单独
复跑 pytest 得 rc=0 ⇒ 判环境级 flaky，重跑门禁才取得 `FULL GATE PASSED`。

**维护者动作**：**重新加载扩展**（`chrome://extensions` → 该扩展「重新加载」），并确认 trace 里
`arm_acked.build` 变成 **0.9.0**。

---

## 补丁：`:nth-of-type` 只在**必要**时才补（维护者反馈「nth-of-type 的选择器感觉不太稳定」）

维护者贴真机选择器（小红书搜索框祖先链）：

```
div.search-area:nth-of-type(1) > div.input-box > div.wendian-wrapper
  > div.textarea-container-single-line:nth-of-type(1)
  > div.xhs-single-line-row-with-reference > div.textarea-wrapper:nth-of-type(2)
```

**先把 nth 的两面性讲清**（不能一棍子打死）：

- `workflows/test11/elements/el_a.json` 的 `li.hotsearch-item:nth-of-type(1)` 是**同构列表**
  （每个热搜项 class 完全一样），位置是分得开的**唯一办法** ⇒ 真必要，必须保留。
- `div.search-area` / `div.textarea-container-single-line` 这类**特征 class 已唯一**的层级，
  序号纯属多余。

**根因（生产侧真缺陷）**：`content.js::pathEntryFor` 只要「父节点有 >1 个同标签兄弟」就补
`:nth-of-type(n)`，**从不检查本层片段（`tag` + 首类）是否已把目标唯一化**。于是父节点多几个
同标签兄弟，`div.search-area` 就跟着变成 `div.search-area:nth-of-type(1)`——页面一重排 / 插入
兄弟即断，而那个 class 本来稳如磐石。

**修法（gating）**：抽出 `classListOf(node)`（顺带把内联的 className 分割收成一处），仅当
`same.length > 1 && 片段在同标签兄弟间仍不唯一` 时才补序号。

**已知取舍（如实钉住，不假装修了）**：片段只取**首类**，特征 class 若排在次位，则首类不足以
唯一化、仍会补序号。根治需让片段按「最能消歧的 class」选——那会改 `fragment` 语义与界面
「首类勾选」口径，属另一处切片。已在门禁里**显式钉住现状**，防止被误认为已修。

**build 0.9.0 → 0.9.1**（content.js / background.js / manifest.json 三方一致）。

**判据**：`scripts/check_capture_helpers.mjs` 改 2 条被钉住的旧期望（**旧期望正是把 bug 写成了
契约**）+ 新增 3 条成对断言（特征 class 唯一不补号 / 同构列表仍补号 / 首类不足时现状）。

**负向验证 4 注入全命中**（新建 `.harness/spike/probe_m52_nth_gating_negative.py`；I4 的 build
值**运行期从 content.js 解析**、不再硬编码，以后 bump 无需改锚点）：

| 注入 | 内容 | 结果 |
|---|---|---|
| I1 | 撤掉 gating（回到旧行为） | 红 3（gating 判据三条） |
| I2 | gating 恒假（过度去号） | 红 4（同构列表 + cover 候选） |
| I3 | `classListOf` 恒空（片段丢 class） | 红 7 |
| I4 | EXT_BUILD 改为不存在的值 | 红 1（三方一致） |

全部逐字节还原核 md5、还原后复绿。

**顺带修掉探针维护性**：`probe_m51b_negative.py` 的 I6 锚点原硬编码 `0.9.0`，被本次 bump 打
失效（注入会报「锚点出现 0 次」）⇒ 同样改为运行期解析；重跑该探针 **7 注入全命中**、复绿。

**门禁**：`FULL GATE PASSED`（**1560 passed / 0 failed / 21 skipped / 2 xfailed**；ruff 全绿；
5 组 Python 静态门禁 + 14 个 `check_*.mjs` 全 rc=0）。

**维护者动作**：**重新加载扩展**（build → **0.9.1**）。

## S4：参数面板里的「从元素库选择」入口（2026-10-10）

**触发**：维护者问「输入文本的指令，`selector` 不是从元素库选择吗？」

**现状取证（回答这个问题的三条事实）**：

1. `commands/browser/input.json` 的 `selector` 是 `{"type": "string", "minLength": 1, "x-fx": true}`
   ⇒ `param_form.py` 按类型通用渲染成**文本框 + fx 开关**，面板里**没有**任何元素入口。
2. 真正的入口在**元素库侧**：元素面板「插入参数」→ `app.py::_insert_element(name)`，
   **前提是先在画布选中一个 action 节点**；按元素 kind 决定写哪个键（browser→`selector`，
   desktop→`locator`），并**双写**：值快照进 `with`、引用进 `elementRefs`。
   （桌面腿的 `input`/`click` 等用的是 **`elementId`**（findElement 产出的会话内句柄），
   只有 `findElement` 用 `locator`——所以能用元素引用的参数键就 `selector` / `locator` 两个。）
3. 引用**真被消费**：`runtime/element_refs.py::resolve_element_refs` 在 schema 校验前替换为
   元素库最新值；缺失/坏文件回落快照并落 `elementRefFallback` 事件。手工改该参数值 ⇒ 摘引用
   （用户接管），避免运行期元素库值盖掉手工输入。

**拍板**：维护者选「加选择入口」。

**改动**：

- `runtime/element_refs.py`：新增 `_KEY_BY_KIND` 与 `param_key_for_element_kind` /
  `element_kind_for_param_key` / `element_capable_keys`——**唯一事实源**，且
  `element_kind_for_param_key` 以 `_VALUE_BY_KEY` 为准（能选却替换不了 = 静默失配）。
  `_insert_element` 改用它，删掉 if/elif 硬编码；取值改用 `element_value_for_key`，
  取不出定位值时明确报「元素 X 没有可用的定位值」而不是写空串。
- `gui/param_form.py`：`_attach_element_row` 给支持引用的参数加一行「元素」
  （`QComboBox` 按 kind 过滤 + `✕` 清除）。选中 = 把值控件填成**元素库最新值**
  （string 直写 / 复合类型写 JSON）并记引用意图；`✕` = 摘引用但**保留手填值**。
  初值从 `raw["elementRefs"]` 读，且**在连信号之前**设置（否则初始化会被当成用户操作）。
  `element_refs()` 只回传**用户显式动过**的键，没动过的不回传。
- `gui/app.py`：`_element_choices()` 提供当前流程元素库的 `(名, 文档)` 列表并注入表单；
  apply 时显式选择优先写入/摘除 `elementRefs`，其余键走旧规则（值变了 ⇒ 摘）。

**判据 +5**：

- `test_kind_key_mapping_is_sourced_from_value_table`：`set(_KEY_BY_KIND.values()) == set(_VALUE_BY_KEY)`，
  且每个 kind 映射到的键真能取出值。
- 面板四件套（`tests/contract/test_gui_panels.py`）：只列同 kind 元素 /
  面板选元素与元素库「插入参数」**产出逐字节相等**（成对判据：光有两份各自绿的判据不行）/
  `✕` 清除保留手填值 / 原样应用保留引用。

**负向验证**（`.harness/spike/probe_m52_element_pick_negative.py`，6 注入全命中、逐字节还原、复绿）：

| 注入 | 方向 | 红 |
| --- | --- | --- |
| I1 两道过滤都去掉 | 混入不匹配 kind 的元素 | 1 |
| I2 `element_refs()` 恒空 | 写侧不记意图 | 2 |
| I3 apply 的 `chosen` 恒空 | 读侧不消费 | 2 |
| I4 stale 去掉 `key not in chosen` | 显式键被当接管 | 1 |
| I5 `_KEY_BY_KIND['desktop']` 指到 selector | 共享表不同源 | 2 |
| I6 元素来源恒空 | provider 没接上 | 3 |

**探针自身踩到的坑（值得记）**：I4 首版锚点带了**尾随 `\n`**，而 `inject()` 是
`replace(old, new + "  " + sentinel)` ⇒ 哨兵与下一行 `and (...)` **粘在同一行**、把条件注释掉
⇒ 注入语义变成「恒真」而非「去掉豁免」（多红了 2 条）。**锚点整行取但不带换行**。
另：I4 首版跑出「3 次同代码全绿」⇒ 查明是**测试构造没打中**（`holder.args` 没同步改，
`previous_args == values` 使得 stale 判据不触发）——修测试构造后才真正覆盖
「手工改过值之后又改用元素」这条真实路径。

**门禁**：`FULL GATE PASSED`（**1570 passed** / 0 failed / 21 skipped / 2 xfailed）。
flaky 两条（`test_gui_node_edit` 焦点竞争、`test_gui_command_matrix` 子进程）均以
「单跑绿 + 同代码全量重跑绿/未碰该模块」判定为**环境级**，不动其代码。本地提交未 push。
