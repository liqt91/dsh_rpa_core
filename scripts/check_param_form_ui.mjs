// 参数表单「表达式模式」的 UI 契约判据（M108，进默认门禁）。
//
// 为什么必须有这一层：`fx` 按钮、变量菜单、标签删光自动撤 fx 这三段都只在浏览器里
// 跑。Python 侧读源码最多证明「写了 addEventListener」——证明不了「点一下真的插进去了
// 光标处」「点一下真的问了后端作用域」。而这三个交互正是本轮把 `x-fx` / `x-python`
// 从死声明变成能用入口的全部内容。
//
// 三组判据：
//   1. 渲染层：只有 manifest 声明了 x-fx / x-python 的字段才出现对应按钮；
//   2. 行为层：点 fx → 真的走 rpa.variableScopes（键名与后端签名逐字一致）；
//             点菜单项 → 真的把 [ref] 插进光标处并切进 fx 态；
//             把标签删光 → fx 态自动撤销；
//   3. 提交层：保存时 expr_modes 是**整份**（不是补丁），结构表单不带这条通道。
//
// 用最小 DOM shim 真跑 main.js：`vm.runInContext` 把整个模块执行一遍，
// `function` 声明会成为上下文的全局绑定，于是可以直接调 `buildControl` / `saveParams`。
import fs from 'node:fs'
import vm from 'node:vm'
import path from 'node:path'

const ROOT = path.resolve(import.meta.dirname, '..')
const MAIN = path.join(ROOT, 'apps/editor/gui/src/main.js')
const mainSrc = fs.readFileSync(MAIN, 'utf8')

const results = []
// 判据先登记、最后**按顺序 await 全跑**：`checkAsync` 的结论是异步 push 的，
// 边登记边打印会让异步那几条在打印之后才落盘——表现就是「4 条只报 2 条」。
const tasks = []
function check(name, fn) {
  tasks.push({ name, fn })
}
function checkAsync(name, fn) {
  tasks.push({ name, fn })
}
function assert(cond, msg) {
  if (!cond) throw new Error(msg)
}

/* ---- 最小 DOM：只实现 main.js 走到的成员 ------------------------------- */
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase()
    this.children = []
    this.dataset = {}
    this.style = { setProperty() {} }
    this.attrs = {}
    this.handlers = {}
    this.className = ''
    this.textContent = ''
    this.value = ''
    this.disabled = false
    this.parentNode = null
    this.classList = {
      add: (c) => {
        if (!this.classList.contains(c)) this.className = `${this.className} ${c}`.trim()
      },
      remove: (c) => {
        this.className = this.className
          .split(/\s+/)
          .filter((x) => x && x !== c)
          .join(' ')
      },
      contains: (c) => this.className.split(/\s+/).filter(Boolean).includes(c),
      toggle: (c, on) => {
        const want = on === undefined ? !this.classList.contains(c) : Boolean(on)
        if (want) this.classList.add(c)
        else this.classList.remove(c)
      },
    }
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node == null) continue
      node.parentNode = this
      this.children.push(node)
    }
  }
  appendChild(node) {
    this.append(node)
    return node
  }
  removeChild(node) {
    this.children = this.children.filter((n) => n !== node)
    node.parentNode = null
    return node
  }
  replaceChildren(...nodes) {
    this.children = []
    this.append(...nodes)
  }
  remove() {
    if (!this.parentNode) return
    this.parentNode.children = this.parentNode.children.filter((n) => n !== this)
    this.parentNode = null
  }
  setAttribute(key, value) {
    this.attrs[key] = String(value)
  }
  getAttribute(key) {
    return this.attrs[key]
  }
  addEventListener(type, fn) {
    ;(this.handlers[type] ||= []).push(fn)
  }
  removeEventListener(type, fn) {
    this.handlers[type] = (this.handlers[type] || []).filter((f) => f !== fn)
  }
  dispatch(type) {
    for (const fn of this.handlers[type] || []) {
      fn({ target: this, preventDefault() {}, stopPropagation() {} })
    }
  }
  focus() {}
  setSelectionRange(start, end) {
    this.selectionStart = start
    this.selectionEnd = end
  }
  getBoundingClientRect() {
    return { left: 10, bottom: 20, top: 0, right: 0, width: 100, height: 20 }
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null
  }
  querySelectorAll(sel) {
    const out = []
    const walk = (node) => {
      for (const child of node.children || []) {
        if (matches(child, sel)) out.push(child)
        walk(child)
      }
    }
    walk(this)
    return out
  }
  contains(node) {
    if (node === this) return true
    return this.children.some((child) => child.contains && child.contains(node))
  }
}
function matches(node, sel) {
  // 复合类选择器（`.exprBtn.fx`）必须全含：只判第一个类会让「fx 按钮」判据恒真——
  // Py 按钮的类是 `exprBtn py`，一样能过 ⇒ 那就成了假判据。
  //
  // 属性选择器可以**挂在类选择器后面**（`.field[data-field="x"]`）：x-depends 整行
  // 置灰要按 data-field 找行，只支持裸 `[data-...]` 会让那条判据永远走不到。
  const attrMatch = /\[data-([\w-]+)="([^"]*)"\]$/.exec(sel)
  let base = sel
  if (attrMatch) {
    const key = attrMatch[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase())
    if (String(node.dataset[key]) !== attrMatch[2]) return false
    base = sel.slice(0, attrMatch.index)
  }
  if (!base) return true
  if (base.startsWith('.')) {
    const wanted = base.slice(1).split('.').filter(Boolean)
    return wanted.every((name) => node.classList.contains(name))
  }
  return false
}

const byId = new Map()
function nodeById(id) {
  if (!byId.has(id)) byId.set(id, new El('div'))
  return byId.get(id)
}
const calls = []
let saveResponse = null
const doc = {
  createElement: (tag) => new El(tag),
  querySelector: (sel) => (sel.startsWith('#') ? nodeById(sel.slice(1)) : null),
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
  activeElement: null,
}
const sandbox = {
  document: doc,
  ppx: {
    call: async (name, args) => {
      calls.push({ name, args })
      if (name === 'rpa.bootstrap') return { commands: [], flows: [], catalogDigest: '' }
      if (name === 'rpa.nodeDetail') return { fields: [], groups: [], node: {} }
      if (name === 'rpa.elementCandidates') return { ok: true, capableKeys: [] }
      if (name === 'rpa.variableScopes') {
        return {
          ok: true,
          groups: [
            { label: '流程参数', items: [{ name: 'who', ref: 'inputs.who', origin: '流程输入' }] },
            { label: '变量', items: [{ name: 'alpha', ref: 'alpha', origin: 'set1' }] },
          ],
        }
      }
      if (name === 'rpa.saveNodeParams') return saveResponse
      return { ok: true, missing: [], resolved: [], elements: [] }
    },
  },
  console,
  performance: { now: () => 0 },
  setInterval: () => 0,
  clearInterval: () => {},
  setTimeout: () => 0,
  clearTimeout: () => {},
  window: { confirm: () => true, innerWidth: 1280, innerHeight: 800 },
  CSS: { escape: (s) => s },
  URL,
  Date,
  Math,
  JSON,
  String,
  Number,
  Array,
  Object,
  Set,
  Map,
  Promise,
  RegExp,
  Error,
}
const ctx = vm.createContext(sandbox)
vm.runInContext(mainSrc.replace(/^import .*$/gm, ''), ctx, { filename: 'main.js' })
const run = (expr) => vm.runInContext(expr, ctx)

/* ---- 夹具：一个声明了 x-fx 的字段、一个只声明 x-python 的、一个都没声明的 ---- */
const FIELD = {
  key: 'msg',
  label: '日志内容',
  type: 'string',
  typeSource: 'declared',
  semantic: 'param',
  declared: true,
  required: true,
  set: true,
  value: '你好',
  valueText: '你好',
  default: null,
  hasDefault: false,
  enum: [],
  description: '',
  multiline: false,
  fx: true,
  python: false,
  mode: '',
  varWrite: false,
  depends: [],
  group: null,
  hidden: false,
}

function primeForm(field) {
  calls.length = 0
  run('currentPath = "workflows/probe/workflow.json"')
  run('selected = { id: "log1", kind: "action", label: "记录日志" }')
  run('panelMode = "param"')
  run('nodeRefs = {}')
  run('outputs = []')
  run('pendingUnbinds = new Set()')
  run('elemCapableKeys = new Set()')
  run('groups = []')
  run(`fields = [${JSON.stringify(field)}]`)
  // 走**真实渲染路径**（renderForm → buildControl），不是单独调 buildControl：
  // 单独调等于绕开「表单真的挂进面板」这一步，而 fx 菜单正是相对面板定位的。
  run('renderForm()')
  return run('el.form')
}

// ---------- 1. 渲染层 ----------
check('渲染层：声明 x-fx 才有 fx 按钮，声明 x-python 才有 Py', () => {
  const both = primeForm(FIELD)
  const bar = both.querySelector('.exprBar')
  assert(bar, '声明了 x-fx 的字段没渲染模式按钮组')
  assert(bar.querySelector('.exprBtn.fx'), '没有 fx 按钮')
  assert(!bar.querySelector('.exprBtn.py'), '没声明 x-python 却渲染了 Py 按钮')

  const pyOnly = primeForm({ ...FIELD, fx: false, python: true })
  const bar2 = pyOnly.querySelector('.exprBar')
  assert(bar2 && bar2.querySelector('.exprBtn.py'), '声明了 x-python 却没渲染 Py 按钮')
  assert(!bar2.querySelector('.exprBtn.fx'), '没声明 x-fx 却渲染了 fx 按钮')

  const none = primeForm({ ...FIELD, fx: false, python: false })
  assert(!none.querySelector('.exprBar'), '两个能力都没声明却渲染了模式按钮组')
})

// ---------- 2. 行为层 ----------
checkAsync('行为层：点 fx 拉清单、点菜单项把 [ref] 插进光标处', async () => {
  const form = primeForm(FIELD)
  form.querySelector('.exprBtn.fx').dispatch('click')
  // openScopeMenu 是 async：让微任务队列跑完再看 calls 与菜单
  await new Promise((resolve) => setImmediate(resolve))
  const hit = calls.find((c) => c.name === 'rpa.variableScopes')
  assert(hit, '点 fx 没有请求变量清单（菜单会是空的）')
  assert(
    Object.keys(hit.args).sort().join(',') === 'node_id,path',
    `variableScopes 的参数键必须是 path + node_id（桥层严格绑定），实得 ${JSON.stringify(hit.args)}`,
  )
  assert(hit.args.path === 'workflows/probe/workflow.json', '没带上当前流程路径')
  assert(hit.args.node_id === 'log1', '没带上当前节点 id')

  const menu = form.querySelector('.scopeMenu')
  assert(menu, '菜单没有挂进参数面板')
  const items = menu.querySelectorAll('.scopeItem')
  assert(items.length === 2, `菜单项数量不对（流程参数 1 + 变量 1），实得 ${items.length}`)

  const control = form.querySelector('[data-key="msg"]')
  control.value = '前缀-后缀'
  control.setSelectionRange(3, 3)
  items[0].dispatch('click')
  assert(control.value === '前缀-[inputs.who]后缀', `插入位置不对：${control.value}`)
  assert(run('exprModes["msg"]') === 'fx', '插入标签后没有切进 fx 态')
  assert(control.selectionStart === '前缀-[inputs.who]'.length, '光标没停在插入文本之后')
})

check('行为层：标签删光自动撤销 fx 态（与后端 _clean_expr_modes 同口径）', () => {
  const form = primeForm(FIELD)
  const control = form.querySelector('[data-key="msg"]')
  // 进 fx 态的唯一正路是「点 fx 插入」或「上一次保存下来的模式」——**不是**「文本里
  // 出现了方括号就自动进 fx」：`a[title]` 这种 CSS 选择器会被误判成变量标签。
  run('exprModes["msg"] = "fx"')
  control.value = '有标签[alpha]'
  control.dispatch('input')
  assert(run('exprModes["msg"]') === 'fx', '有标签时不该撤销 fx')
  control.value = '标签被删光了'
  control.dispatch('input')
  assert(
    !run('exprModes["msg"]'),
    '标签删光后 fx 态还留着（下次打开面板会显示「fx 开着」而字段里没有变量）',
  )
})

// ---------- 3. 提交层 ----------
checkAsync('提交层：保存时 expr_modes 是整份，结构表单不带这条通道', async () => {
  const form = primeForm(FIELD)
  const control = form.querySelector('[data-key="msg"]')
  control.value = '你好[alpha]'
  // 走「点 fx 插入」那条真实路径进 fx 态。**刻意不做「有方括号就自动进 fx」**：
  // `selector` 字段里的 `a[title]` 会被误判成变量标签（那正是记忆里踩过的坑）。
  run('exprModes["msg"] = "fx"')
  control.dispatch('input')
  saveResponse = {
    ok: true,
    saved: true,
    changed: true,
    bytes: [10, 20],
    sha: 'abcdef123456',
    added: [],
    removed: [],
    modesCleared: [],
    compile: { ok: true },
    flow: { cards: [] },
  }
  calls.length = 0
  await run('saveParams()')
  const save = calls.find((c) => c.name === 'rpa.saveNodeParams')
  assert(save, '没有发出保存请求')
  assert(
    JSON.stringify(save.args.expr_modes) === JSON.stringify({ msg: 'fx' }),
    `expr_modes 不是整份 {msg:"fx"}：${JSON.stringify(save.args.expr_modes)}`,
  )

  // 结构表单（if / forEach 的参数）没有 _exprModes 通道，带上去就是 INVALID_PARAMS。
  // 重新 prime 一份：上一轮保存后 `fields` 已被 nodeDetail 的响应刷成 []，
  // 不重置的话 saveParams 会在第一行 `!fields.length` 直接 return——那样这条判据
  // 会因为「什么都没发」而绿，等于没验。
  primeForm(FIELD)
  run('panelMode = "struct"')
  calls.length = 0
  await run('saveParams()')
  const structSave = calls.find((c) => c.name === 'rpa.saveStructuredParams')
  assert(structSave, '结构表单没走 saveStructuredParams')
  assert(!('expr_modes' in structSave.args), '结构表单的请求里塞了 expr_modes')
})


// ---------- 4. 输出变量列（M108 改名联动的前端一侧） ----------
checkAsync('输出变量列：按 outputs 描述符渲染，改名随参数同一次提交', async () => {
  const form = primeForm(FIELD)
  run(
    `outputs = ${JSON.stringify([
      { key: 'value', label: '格式化时间', primary: true, alias: 'now', declared: true },
      { key: 'iso', label: 'ISO 时间', primary: false, alias: '', declared: true },
    ])}`,
  )
  run('renderForm()')

  const panel = form.querySelector('.outPanel')
  assert(panel, '没有渲染输出变量列（自定义该节点的输出变量名没有入口）')
  const input = panel.querySelector('[data-alias="value"]')
  assert(input, '输出变量行没有输入框')
  assert(input.value === 'now', `别名的当前值没回填：${input.value}`)

  input.value = 'when'
  input.dispatch('input')
  assert(run('isDirty()') === true, '改了别名却没判成「未保存」')

  saveResponse = {
    ok: true,
    saved: true,
    changed: true,
    bytes: [10, 20],
    sha: 'abcdef123456',
    added: [],
    removed: [],
    modesCleared: [],
    aliasesAdded: ['value'],
    aliasesRemoved: [],
    renamed: { now: 'when' },
    refs: [{ nodeId: 'log1', field: 'message', count: 2 }],
    pySkipped: [],
    compile: { ok: true },
    flow: { cards: [] },
  }
  calls.length = 0
  await run('saveParams()')
  const save = calls.find((c) => c.name === 'rpa.saveNodeParams')
  assert(save, '没有发出保存请求')
  assert(
    JSON.stringify(save.args.aliases) === JSON.stringify({ value: 'when', iso: '' }),
    `aliases 必须是整份 {value,iso}：${JSON.stringify(save.args.aliases)}`,
  )
  const status = run('el.status.textContent')
  assert(
    /now→when/.test(status) && /同步 2 处引用/.test(status),
    `回执没说清改了几处引用（用户会以为只改了个名字）：${status}`,
  )
})

// ---------- 5. 参数联动（x-depends） ----------
check('联动层：depends 不满足时置灰，条件变化即时重算，值仍随提交', () => {
  const controller = {
    ...FIELD,
    key: 'mode',
    label: '输入方式',
    value: 'fill',
    valueText: 'fill',
    set: true,
    fx: false,
    python: false,
    enum: [
      { value: 'fill', label: '直接填写' },
      { value: 'type', label: '逐字模拟' },
    ],
  }
  const dependent = {
    ...FIELD,
    key: 'keyIntervalMs',
    label: '按键间隔',
    type: 'integer',
    value: '50',
    valueText: '50',
    set: true,
    fx: false,
    python: false,
    enum: [],
    depends: [{ key: 'mode', value: 'type' }],
  }
  primeForm(controller)
  run(`fields = ${JSON.stringify([controller, dependent])}`)
  run('renderForm()')
  const form = run('el.form')

  const dep = form.querySelector('[data-key="keyIntervalMs"]')
  assert(dep, '依赖字段没渲染出来')
  const wrap = form.querySelector('.field[data-field="keyIntervalMs"]')
  assert(wrap && wrap.classList.contains('depOff'), '条件不满足时整行没有置灰态')
  assert(dep.disabled === true, '条件不满足时控件仍可编辑')
  const hint = wrap.querySelector('.depHint')
  assert(hint && /输入方式 = 逐字模拟/.test(hint.textContent), `提示没写清依赖谁：${hint && hint.textContent}`)

  const ctrl = form.querySelector('[data-key="mode"]')
  ctrl.value = 'type'
  ctrl.dispatch('change')
  assert(dep.disabled === false, '控制字段改成满足条件的值后没有解除置灰')

  ctrl.value = 'fill'
  ctrl.dispatch('change')
  assert(dep.disabled === true, '控制字段改回去后没有重新置灰')

  // ★ 置灰不许把它从提交里摘掉：全量提交少一个键 ⇒ 服务端 INCOMPLETE_SUBMIT
  const values = run('currentValues()')
  assert(values.keyIntervalMs === '50', '置灰后值从提交里消失了')
})

check('联动层：引用型控制值不置灰（静态判不了运行期的值）', () => {
  const controller = {
    ...FIELD,
    key: 'mode',
    label: '输入方式',
    value: '${inputs.how}',
    valueText: '${inputs.how}',
    set: true,
    fx: false,
    python: false,
    enum: [
      { value: 'fill', label: '直接填写' },
      { value: 'type', label: '逐字模拟' },
    ],
  }
  const dependent = {
    ...FIELD,
    key: 'keyIntervalMs',
    label: '按键间隔',
    type: 'integer',
    value: '',
    valueText: '',
    set: false,
    fx: false,
    python: false,
    enum: [],
    depends: [{ key: 'mode', value: 'type' }],
  }
  primeForm(controller)
  run(`fields = ${JSON.stringify([controller, dependent])}`)
  run('renderForm()')
  const control = run('el.form').querySelector('[data-key="keyIntervalMs"]')
  assert(
    control.disabled === false,
    '控制值是引用（运行期才知道）却把这一格置灰了——用户会以为参数不能用',
  )
})

for (const task of tasks) {
  try {
    await task.fn()
    results.push({ name: task.name, ok: true })
  } catch (error) {
    results.push({ name: task.name, ok: false, detail: String(error.message || error) })
  }
}

for (const item of results) {
  console.log(`${item.ok ? 'PASS' : 'FAIL'} ${item.name}${item.ok ? '' : ' — ' + item.detail}`)
}
const failed = results.filter((item) => !item.ok)
if (failed.length) {
  console.log(`PARAM FORM UI CHECK FAILED (${results.length - failed.length}/${results.length})`)
  process.exit(1)
}
console.log(`PARAM FORM UI CHECK PASSED (${results.length}/${results.length})`)
