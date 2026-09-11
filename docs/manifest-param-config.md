# Manifest 参数配置能力审计

> 审计对象：`commands/**/*.json`（65 条命令，241 个输入字段）
> 代码入口：`src/rpa_core/model/command.py`（契约模型）、`catalog/loader.py`（加载校验）、
> `devserver/static/app.js`（属性面板渲染）、`compiler/compiler.py` + `runtime/orchestrator.py`（门禁）
> 结论日期：2026-09-10

---

## 一、速览

manifest 的参数配置分四层：**能力骨架已具备，但除 `x-outputs` 外几乎都没铺开。**

| 层 | 关键字 | 覆盖 | 状态 |
|---|---|---|---|
| manifest 顶层 | `input_schema` / `output_schema` / `errors` / `default_timeout_seconds` / `effect` | 65/65 | 完整 |
| manifest 顶层 | `retryable` | 11/65 | 有数据、无 UI 契约（本次修复） |
| manifest 顶层 | `x-outputs` | 34/65 | 已铺开（label 57 / primary 26 / hidden 6） |
| manifest 顶层 | `x-var-write` | 1/65 | 只 `data.setVar` |
| input_schema 根 | `x-param-groups` | **1/65** | 只 `browser.navigate` |
| input_schema 根 | `x-depends` | **1/65** | 只 navigate，且只支持「单字段等值」 |
| 字段级 | `x-enum-labels` | **4 / 32 个 enum 字段** | 28 个枚举值在 UI 里仍是英文 |
| 字段级 | `x-fx` / `x-python` | 4 / 2（共 241 字段） | 变量/表达式入口几乎没声明 |
| 字段级 | 控件类型 | **无此关键字** | 前端按 key 名硬编码 |

一句话：**参数的「是什么」（JSON Schema）很完整；「怎么给用户看、怎么填」几乎全靠前端硬编码。**

---

## 二、现有配置全景

### 1. manifest 顶层（`CommandManifest`，`extra="forbid"`）

| 关键字 | 语义 | 覆盖 |
|---|---|---|
| `input_schema` | Draft 2020-12 子集：`type`/`properties`/`required`/`enum`/`default`/`minimum`/`minLength`/`pattern`/`items`/`oneOf`/`additionalProperties` | 65/65 |
| `output_schema` | 输出结构（运行值校验） | 65/65 |
| `errors` | 该命令可抛出的错误码白名单 | 65/65 |
| `default_timeout_seconds` | 默认超时 | 65/65 |
| `effect.{kind,replay,idempotency}` | 副作用种类 / 重放策略 / 幂等策略 | 65/65（replay：unsafe 39、safe 20、idempotent 6） |
| `retryable` | 该命令的错误是否值得重试 | 11/65（true） |
| `x-outputs` | 输出字段元数据：`label`（别名框标题）/`primary`（主输出）/`hidden`（不给别名框） | 34/65 |
| `x-var-write` | `{"field": "varName"}`：该字段是变量写入目标 | 1/65 |
| `x-fx` / `x-python` | 顶层声明（模型里有字段） | **0 使用（死字段，见 P2-10）** |

### 2. input_schema 根（JSON Schema 允许未知关键字，故可自由挂扩展）

| 关键字 | 语义 | 覆盖 |
|---|---|---|
| `x-param-groups` | 参数分区：`[{label, fields[], collapsed?}]`，对标影刀「常规/高级」 | 1/65 |
| `x-depends` | 字段联动：`{field: {控制字段: 期望值}}`，不满足时置灰并归入「其他通道参数」折叠区 | 1/65 |
| `oneOf` | 标准 JSON Schema 分支 | 1/65 |

### 3. 字段级（`properties.<name>`）

**标准键**：`type`(250)、`description`(114)、`minLength`(87)、`default`(62)、`minimum`(48)、`enum`(32)、`items`(10)、`minItems`(3)、`minProperties`(2)、`pattern`(1)

**扩展键**：
- `x-fx`(4)：该字段支持 `${变量}` 引用 → 渲染 fx 切换按钮
- `x-python`(2)：该字段支持 Python 表达式 → 渲染 py 切换按钮
- `x-enum-labels`(4)：枚举值中文化（`msedge` → "Microsoft Edge"）

### 4. 节点侧（`model/workflow.py` 的 `ActionNode`）

与参数实例相关：`with`、`output_aliases`、`timeout_seconds`、`retry_count`、`retry_backoff_seconds`、`retry_backoff_max_seconds`、`_exprModes`（字段表达式模式）。

### 5. 标签层（`devserver/static/i18n.js`，不在 manifest 内）

`commands`(65) / `fields`(71) / `commandFields`(11 条命令覆盖) / `kinds`(4) / `effects`(5) / `ops`(8) / `glossary`(11)。
→ **参数中文名与枚举值是"前端字典 + manifest"双份来源**，这是后面多个缺口的根因。

---

## 三、缺口清单

### P0 — 直接影响"参数面板是否可信"

**P0-1　重试能力没有 manifest→UI 契约（本次已修）**
- 修复前：前端用 `manifest.effect.replay === "unsafe"` 自行推断，且只是**置灰**输入框 + 警告，仍然占位；
- 三处口径还不一致：编译器按 `replay==unsafe` 硬拒；运行时按 `error.retryable AND manifest.retryable` 门禁；
- 后果：**9 个 safe + 6 个 idempotent（共 15 条）命令声明 `retryable=false`，用户填了重试次数却永不生效**（静默失效）；
- 修复：`retryable` 成为唯一事实源，`retryable !== true` 时**不渲染**「重试次数」；详见第四节。

**P0-2　参数分组只覆盖 1/65**
除 navigate 外的 64 条命令，参数平铺在「输入参数」下（如 `browser.click` 有 8 个字段、`desktop.click` 有 7 个），
无法对标影刀的「常规/高级」折叠，长表单难扫读。

**P0-3　字段联动只覆盖 1/65，且表达能力单一**
`x-depends` 只支持「某控制字段 == 某值」一种条件（`{transport: "playwright"}`），
不支持 `in`、`!=`、多条件与/或，也不支持「当前值不满足时清空」。通道类命令（playwright/extension）尤其需要。

**P0-4　参数控件类型无声明 → 前端按 key 名硬编码**
`app.js` 里仍有三处特判：
- `key === "varName" && node.command === "data.setVar"` → 变量下拉
- `key === "sessionId"` → 会话选择器（按 `resources` 猜类型）
- `key === "selector"` → 元素选择器
新增命令若也想要这些控件，**只能改前端代码**。这是"参数能力欠缺"里最硬的一条。

### P1 — 影响填写体验与对标度

**P1-5　枚举值中文化只覆盖 4/32**
仅 `navigate.json` 的 `action/waitUntil/channel/transport` 有 `x-enum-labels`。
其余 28 个字段（`clickType: single/double`、`button: left/right/middle`、
`state: visible/hidden/detached/attached`、`matchBy`/`matchMode`/`selectBy`…）在面板里显示**英文原值**。
前端虽有 `i18n.ops` 兜底，但它只覆盖 8 个条件运算符键，兜不住这些。

**P1-6　缺字段级输入提示与单位**
无 `x-placeholder`、`x-unit`（如 `timeoutMs` 现在的 label 靠手写「（毫秒）」）、`x-example`。

**P1-7　缺字段级显隐/高级标记**
无 `x-hidden`（内部字段）、`x-advanced`（自动进「高级」组）。
当前只能靠手写 `x-param-groups` 的 `fields` 列表，**新增字段忘了登记就会掉进「其他」组**（默认行为，但不显式）。

**P1-8　缺条件必填 / 互斥**
只有 `required`（静态）。没有 `requiredIf`（如 `action=goto` 时 `url` 必填）、
`oneOfRequired`（channel 与 transport 二选一）、`mutuallyExclusive`。

**P1-9　`x-fx` / `x-python` 覆盖率极低**
241 个字段里只有 4 个声明 `x-fx`、2 个声明 `x-python`。
`browser.click` 的 `selector`、`browser.getText` 的 `selector` 等**理应可引用变量**的字段都没有入口。

### P2 — 卫生与延伸

**P2-10　顶层 `x-fx` / `x-python` 是死字段**
模型里声明在 manifest 顶层，但 65 个 manifest 中 0 使用；实际用法都在 `properties.<field>` 下。
同名不同层，容易写错位置且不报错（顶层写了会被模型接受，前端却不读）。

**P2-11　标签无 locale 维度**
`x-enum-labels` / `x-outputs.label` / `i18n.js` 全是中文硬编码，无 `en` 兜底。

**P2-12　缺敏感字段标记**
密码/token 类字段（如 `browser.cookieSet.cookies`）无 `x-sensitive`，日志与事件里原样落盘。

**P2-13　缺显式参数顺序**
渲染顺序＝`properties` 的插入序（JSON 规范里对象无序），跨工具格式化后可能变序。无 `x-order`。

**P2-14　缺「元素对象」参数类型**
项目有 `elements/` 元素库，但 manifest 无法声明「该参数是一个元素对象」，
`selector` 始终是裸字符串，无法与元素库联动（影刀的元素参数是可拾取、可复用、可失效重定位的）。

---

## 四、本次已修：重试次数改为 manifest 驱动

**设计原则：`retryable` 是「能否配置重试」的唯一事实源，三层共用同一口径。**

```
可重试 ⟺ manifest.retryable === true
  ├─ 前端：retryable !== true → 不渲染「重试次数」输入框（旧版是置灰占位）
  ├─ 编译器：retry_count > 0 且 replay == unsafe → 编译失败（原有）
  └─ 运行时：error.retryable AND manifest.retryable → 才真正重试（原有）
```

`retryable !== true` 分两种原因，UI 给出不同文案：
- `unsafe-replay`：重放不安全（重复开网页/重复提交），填了会编译失败；
- `not-declared`：指令未声明可重试，填了不会生效。

手改 `workflow.json` 遗留的非法值不再静默：给一条红色说明 + 「清除重试次数」按钮。

改动文件：
- `src/rpa_core/devserver/static/app.js`：新增纯函数 `retryPolicy(manifest)`；`retryCountField` 改为
  「不允许时返回 `null`（不渲染）」，仅当节点上残留非法值时给出清除入口。
- `scripts/check_retry_policy.mjs`（新增）：从 `app.js` 抽取同一份 `retryPolicy` 源码校验 8 个判定用例，
  并对 `commands/` 全量 manifest 交叉校验，防止声明与判定漂移。

修复后的覆盖面：**11 条可显示重试，54 条不再显示**（39 unsafe + 15 未声明）。

> 若希望某些命令支持重试，正确做法是把该 manifest 的 `retryable` 改为 `true`
> （`safe` / `idempotent` 才允许；`unsafe` 会被契约模型直接拒绝）。
> 当前 `retryable=true` 的 11 条都是"等待/查找/读取文本"这类有瞬时抖动的命令，
> 而 `cookieGet` / `getWindowList` 等确定性读取保持 `false` —— 这是**策略**而非疏漏，不应无脑全开。

---

## 五、建议的关键字草案（待定优先级）

```jsonc
{
  "input_schema": {
    "type": "object",
    "properties": {
      "selector": {
        "type": "string",
        "minLength": 1,
        "x-widget": "element",        // 控件类型：element | session | variable | file | password | textarea | json
        "x-placeholder": "支持 CSS / 文本 / XPath",
        "x-fx": true,                 // 允许 ${变量}
        "x-element": { "libraries": ["web"] }  // 可绑定 elements/ 元素库
      },
      "url": { "type": "string", "x-fx": true, "x-required-if": { "action": ["goto"] } },
      "timeoutMs": { "type": "integer", "x-unit": "毫秒", "x-advanced": true, "x-order": 30 }
    },
    "x-param-groups": [
      { "label": "常规", "auto": true },            // auto：非 advanced 字段自动归入，免手写 fields
      { "label": "高级", "fields": ["timeoutMs"], "collapsed": true }
    ],
    "x-depends": { "userDataDir": { "transport": { "in": ["playwright"] } } }  // 扩展条件表达
  }
}
```

配套要求（否则新关键字又变成"声明了没人读"）：
1. `x-widget` 落地时，**删掉 `app.js` 里 `varName`/`sessionId`/`selector` 三处硬编码**，改为读 manifest；
2. `x-param-groups.auto` + `x-advanced` 落地后，把 65 条命令的分组一次性铺开（P0-2）；
3. 新增一个 `scripts/check_manifest_param_meta.mjs`（或并入 pytest），
   校验「枚举无中文标签」「控件类型非法」「分组漏登记字段」等，防止再退回硬编码。
