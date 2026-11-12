// M68：元素捕获交互的 UI 判据（进默认门禁）。
//
// 本轮改的是**交互形状**，所以判据必须三层齐全，缺一层都会漏：
//   1. 文案层 —— ELEM_TEXT 的值不许出现实现名词（文案表是唯一来源）；
//   2. 样式层 —— 命中区 ≥24×24、正文 ≥14px、焦点环、编辑框浮层；
//   3. 渲染层 —— 用最小 DOM shim **真跑** main.js：一个按钮起捕获、抓完弹编辑框、
//      确认才入库、取消就丢弃、详情复用同一个框。
// 只读源码证明不了「按钮点得着」「弹出来的框能改名」，只有跑起来能发现。
import fs from 'node:fs'
import vm from 'node:vm'
import path from 'node:path'

const ROOT = path.resolve(import.meta.dirname, '..')
const GUI = path.join(ROOT, '.harness/demo/ppx_probe/app/gui')
const MAIN = path.join(GUI, 'src/main.js')
const HTML = path.join(GUI, 'index.html')
const CSS = path.join(GUI, 'src/style.css')
const API = path.join(ROOT, '.harness/demo/ppx_probe/app/api/api.py')

const results = []
function check(name, fn) {
  try {
    fn()
    results.push({ name, ok: true })
  } catch (error) {
    results.push({ name, ok: false, detail: String(error.message || error) })
  }
}
async function checkAsync(name, fn) {
  try {
    await fn()
    results.push({ name, ok: true })
  } catch (error) {
    results.push({ name, ok: false, detail: String(error.message || error) })
  }
}
function assert(cond, msg) {
  if (!cond) throw new Error(msg)
}

const mainSrc = fs.readFileSync(MAIN, 'utf8')
const html = fs.readFileSync(HTML, 'utf8')
const css = fs.readFileSync(CSS, 'utf8')
const apiSrc = fs.readFileSync(API, 'utf8')
const BANNED = ['capture', 'browser', 'desktop', 'serve', 'session', 'UIA', '扩展']

// ---------- 1. 文案表 ----------
function objectLiteral(src, marker) {
  const start = src.indexOf(marker)
  assert(start >= 0, `找不到 ${marker}`)
  const open = src.indexOf('{', start)
  let depth = 0
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1
    else if (src[i] === '}') {
      depth -= 1
      if (depth === 0) return src.slice(open, i + 1)
    }
  }
  throw new Error(`${marker} 没闭合`)
}

const elemText = vm.runInNewContext(`(${objectLiteral(mainSrc, 'const ELEM_TEXT =')})`)

check('文案表：所有用户可见值都不含实现名词', () => {
  const bad = []
  for (const [key, value] of Object.entries(elemText)) {
    for (const word of BANNED) {
      if (String(value).toLowerCase().includes(word.toLowerCase())) bad.push(`${key}:${word}`)
    }
  }
  assert(bad.length === 0, `命中：${bad.join(', ')}`)
})

check('文案表：必备键齐全（漏一个就会把 key 原名显示给用户）', () => {
  for (const key of [
    'capture', 'fill', 'detail', 'remove', 'save', 'cancel',
    'confirmTitle', 'detailTitle', 'nameEmpty', 'saveFailed', 'none',
  ]) {
    assert(typeof elemText[key] === 'string' && elemText[key], `缺 ${key}`)
  }
  // 「环境准备」这类提示已全部移出面板：表里不该再留状态条文案。
  for (const gone of ['checking', 'ready', 'waiting', 'blocked', 'offlineHint']) {
    assert(!(gone in elemText), `面板里还在留状态文案：${gone}`)
  }
})

// ---------- 2. 样式层 ----------
function ruleBlock(selector) {
  const at = css.indexOf(selector)
  assert(at >= 0, `CSS 少了 ${selector}`)
  const open = css.indexOf('{', at)
  const close = css.indexOf('}', open)
  return css.slice(open + 1, close)
}
function px(block, prop) {
  const hit = block.match(new RegExp(`${prop}\\s*:\\s*(\\d+(?:\\.\\d+)?)px`))
  return hit ? Number(hit[1]) : null
}

check('样式层：捕获按钮与行内动作命中区 ≥24×24、字号 ≥14px', () => {
  for (const selector of ['.elemCapture {', '.elemFill, .elemAct {']) {
    const block = ruleBlock(selector)
    assert(px(block, 'min-height') >= 24, `${selector} min-height=${px(block, 'min-height')}`)
    assert(px(block, 'min-width') >= 24, `${selector} min-width=${px(block, 'min-width')}`)
    assert(px(block, 'font-size') >= 14, `${selector} font-size=${px(block, 'font-size')}`)
  }
})

check('样式层：编辑框是浮层、按钮可禁用、焦点环可见', () => {
  assert(/\.modal\s*\{[^}]*position:\s*fixed/.test(css), '编辑框不是浮层（会撑坏底栏）')
  assert(/\.modal\[hidden\]/.test(css), '编辑框没有 hidden 规则，关不掉')
  assert(/\.modalBar button:disabled/.test(css), '保存按钮没有禁用态')
  assert(/:focus-visible/.test(css), '没有 focus-visible 规则')
})

// ---------- 3. 结构层（index.html） ----------
check('结构层：元素面板只有一个捕获按钮，没有预填名/选类型/写选择器', () => {
  assert(html.includes('id="elemCapture"'), 'index.html 缺单个捕获按钮')
  for (const gone of [
    'elemNewName', 'elemNewWhere', 'elemNewCapture', 'elemNewSelector',
    'elemNewWrite', 'elemNewNote',
  ]) {
    assert(!html.includes(`id="${gone}"`), `旧的工具条控件还在：${gone}`)
  }
  const panel = html.slice(html.indexOf('data-panel="elements"'), html.indexOf('data-panel="globals"'))
  const buttons = [...panel.matchAll(/<button[^>]*id="([^"]+)"/g)].map((m) => m[1])
  assert(buttons.length === 1 && buttons[0] === 'elemCapture', `面板里的按钮：${buttons}`)
  assert(!/提示|hint/i.test(panel.replace(/<!--[\s\S]*?-->/g, '')), '元素面板里还留着提示区')
})

check('结构层：编辑框有名称输入、元信息、错误位与保存/取消', () => {
  const dialog = html.slice(html.indexOf('id="elemDialog"'))
  for (const id of [
    'elemDialogTitle', 'elemDialogName', 'elemDialogMeta',
    'elemDialogNote', 'elemDialogSave', 'elemDialogCancel', 'elemDialogClose',
  ]) {
    assert(dialog.includes(`id="${id}"`), `编辑框缺 #${id}`)
  }
  assert(/<input id="elemDialogName"[^>]*type="text"/.test(dialog), '名称不是普通文本框（中文输入会被挡）')
})

// ---------- 4. 渲染层（最小 DOM shim 真跑 main.js） ----------
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase()
    this.children = []
    this.id = ''
    this.className = ''
    this.attributes = {}
    this.handlers = {}
    this.dataset = {}
    this.textContent = ''
    this.value = ''
    this.placeholder = ''
    this.title = ''
    this.hidden = false
    this.open = false
    this.disabled = false
    this.readOnly = false
    this.style = { setProperty() {} }
    const self = this
    this.classList = {
      add(c) {
        const set = new Set(self.className.split(/\s+/).filter(Boolean))
        set.add(c)
        self.className = [...set].join(' ')
      },
      remove(c) {
        self.className = self.className.split(/\s+/).filter((x) => x && x !== c).join(' ')
      },
      contains(c) { return self.className.split(/\s+/).includes(c) },
      toggle(c, on) { if (on) this.add(c); else this.remove(c) },
    }
  }
  append(...nodes) { for (const n of nodes) this.children.push(n) }
  replaceChildren(...nodes) { this.children = [...nodes] }
  setAttribute(k, v) { this.attributes[k] = v }
  addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn) }
  click() {
    return Promise.all((this.handlers.click || []).map((fn) => fn({ type: 'click' })))
  }
  querySelector() { return null }
  querySelectorAll() { return [] }
  focus() {}
  select() {}
  scrollIntoView() {}
}
const walk = (node, out = []) => {
  out.push(node)
  for (const child of node.children || []) walk(child, out)
  return out
}
const hasClass = (node, name) => String(node.className).split(/\s+/).includes(name)

const registry = new Map()
function nodeById(id) {
  if (!registry.has(id)) {
    const node = new El('div')
    node.id = id
    registry.set(id, node)
  }
  return registry.get(id)
}

const ONE_MISSING = [
  { name: 'userField', nodeId: 'n1', command: 'browser.input', decision: { target: 'browser' } },
]

function makeContext({ responses = {} } = {}) {
  const timers = []
  const calls = []
  const dockTabs = ['elements', 'globals', 'log'].map((key) => {
    const node = new El('button')
    node.dataset.tab = key
    return node
  })
  const dockPanels = ['elements', 'globals', 'log'].map((key) => {
    const node = new El('div')
    node.dataset.panel = key
    return node
  })
  const doc = {
    head: new El('head'),
    createElement: (tag) => new El(tag),
    // `el` 对象就是靠这些 #id 查询建起来的：返回登记节点，绑定才会落在判据看得见的
    // 同一个对象上（返回一次性节点 = 断言恒绿）。
    querySelector: (sel) => (sel.startsWith('#') ? nodeById(sel.slice(1)) : null),
    querySelectorAll: (sel) => {
      if (sel === '.dockTab') return dockTabs
      if (sel === '.dockPanel') return dockPanels
      if (sel === '.card') return []
      if (sel === '.dockFold') return []
      return []
    },
    addEventListener() {},
    activeElement: null,
  }
  const defaults = {
    'rpa.bootstrap': { commands: [], flows: [], catalogDigest: '', python: '', catalogMs: 0 },
    'rpa.missingElements': { ok: true, missing: ONE_MISSING, resolved: [], capabilities: {} },
    'rpa.listElements': { ok: true, elements: [] },
    'rpa.startCapture': {
      ok: true, state: 'waiting', captureId: 'c1', name: null, suggestedName: '元素1', nodeId: null,
    },
    'rpa.capturePoll': {
      ok: true, state: 'ready', captureId: 'c1',
      preview: { name: '元素1', kind: 'browser', selector: '#q', matches: 1, nodeId: 'n1' },
    },
    'rpa.commitElement': { ok: true, name: '搜索框', boundNodeId: 'n1', renamedRefs: [] },
    'rpa.discardCapture': { ok: true, discarded: true },
    'rpa.saveElement': { ok: true, name: '登录框', renamedRefs: ['openHome'] },
    'rpa.elementDetail': {
      ok: true, name: 'searchBox', selectorText: '#q', referencedBy: ['openHome'],
      document: { kind: 'browser', selector: { css: '#q' }, verifyCount: 2, capturedAt: '2026-11-12' },
    },
    'rpa.deleteElement': { ok: true, deleted: true, remaining: [] },
    'window.restore': { ok: true },
  }
  const sandbox = {
    document: doc,
    ppx: {
      call: async (name, args) => {
        calls.push({ name, args })
        return { ...(defaults[name] || { ok: true }), ...(responses[name] || {}) }
      },
    },
    console,
    performance: { now: () => 0 },
    setInterval: () => 0,
    clearInterval: () => {},
    // 定时器只登记不自动跑：判据自己推进时间，才能数清「用户点了几次」。
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length },
    clearTimeout: () => {},
    Date, Math, JSON, String, Number, Array, Object, Set, Map, Promise, RegExp, Error,
  }
  // 顶层 import 是 ESM 语法，vm 跑的是脚本；它不参与本判据的逻辑。
  const src = mainSrc.replace(/^import .*$/gm, '')
  const ctx = vm.createContext(sandbox)
  vm.runInContext(src, ctx, { filename: 'main.js' })
  vm.runInContext(
    `globalThis.__api = {
       startCapture, pollCapture, confirmElemDialog, cancelElemDialog,
       openAssetDetail, renderElementsPanel, deleteAsset,
       session: () => captureSession,
       setState: (missing, elements) => { pickMissing = missing; pickElements = elements; renderElementsPanel() },
     }
     currentPath = 'C:/rpa-m68-fixture/flow.json'
     cards = [{ id: 'n1', no: 3 }, { id: 'n2', no: 5 }]`,
    ctx,
  )
  return { ctx, timers, calls, api: vm.runInContext('globalThis.__api', ctx) }
}

const flush = () => new Promise((resolve) => setImmediate(resolve))

async function startOnce(options = {}) {
  const run = makeContext(options)
  await nodeById('elemCapture').click()
  await flush()
  const tick = run.timers.find((t) => t.ms === 800)
  assert(tick, '点完捕获没排轮询')
  await tick.fn()
  await flush()
  return run
}

const dialogNode = () => nodeById('elemDialog')
const nameNode = () => nodeById('elemDialogName')
const saveNode = () => nodeById('elemDialogSave')
const cancelNode = () => nodeById('elemDialogCancel')
const noteNode = () => nodeById('elemDialogNote')

await checkAsync('渲染层：点唯一的按钮就起捕获，不预填名字、不先探测', async () => {
  const { calls, api } = await startOnce()
  const started = calls.filter((c) => c.name === 'rpa.startCapture')
  assert(started.length === 1, `起捕获 ${started.length} 次`)
  assert(started[0].args.name == null, `还要求预填名字：${JSON.stringify(started[0].args)}`)
  assert(!('override' in started[0].args) && !('target' in started[0].args), '又把类型选择塞回调用参数')
  assert(!calls.some((c) => c.name === 'rpa.pickPlan'), '还在先探测一遍（用户得点两次）')
  assert(api.session() === null, '抓完没把会话收掉')
})

await checkAsync('渲染层：抓完弹编辑框，名字预填、可保存', async () => {
  const { calls } = await startOnce()
  assert(calls.some((c) => c.name === 'rpa.capturePoll'), '没轮询捕获结果')
  assert(calls.some((c) => c.name === 'window.restore'), '抓完没把主窗拉回来')
  assert(dialogNode().hidden === false, '抓完没弹编辑框')
  assert(nameNode().value === '元素1', `名字没预填：${nameNode().value}`)
  assert(saveNode().disabled === false, '保存按钮是禁用的')
  const meta = walk(nodeById('elemDialogMeta')).map((n) => n.textContent).join(' ')
  assert(meta.includes('#q'), `编辑框没给出定位：${meta}`)
  assert(meta.includes('第 3 步'), `编辑框没说明绑到哪一步：${meta}`)
})

await checkAsync('渲染层：确认才入库，中文名照发', async () => {
  const { calls } = await startOnce()
  nameNode().value = '搜索框'
  await saveNode().click()
  await flush()
  const commit = calls.find((c) => c.name === 'rpa.commitElement')
  assert(commit, '没走确认入库')
  assert(commit.args.name === '搜索框', `名字被改过：${commit.args.name}`)
  assert(commit.args.captureId === 'c1', `captureId 没带上：${commit.args}`)
  assert(dialogNode().hidden === true, '入库后编辑框没关')
  assert(calls.some((c) => c.name === 'rpa.listElements'), '入库后没刷新资产列表')
})

await checkAsync('渲染层：取消编辑框就丢弃这次捕获，不入库', async () => {
  const { calls } = await startOnce()
  await cancelNode().click()
  await flush()
  assert(calls.some((c) => c.name === 'rpa.discardCapture'), '取消后没丢弃暂存')
  assert(!calls.some((c) => c.name === 'rpa.commitElement'), '取消了还入库')
  assert(dialogNode().hidden === true, '取消后编辑框没关')
})

await checkAsync('渲染层：入库失败把原因写在框里，不关框也不刷新', async () => {
  const { calls } = await startOnce({
    responses: { 'rpa.commitElement': { ok: false, code: 'NAME_TAKEN', error: '已经有一个叫「搜索框」的元素了' } },
  })
  nameNode().value = '搜索框'
  const before = calls.length
  await saveNode().click()
  await flush()
  assert(dialogNode().hidden === false, '失败却把框关了（用户得重抓一次）')
  assert(hasClass(noteNode(), 'error'), '失败没标红')
  assert(noteNode().textContent.includes('已经有一个'), `没说清原因：${noteNode().textContent}`)
  const after = calls.slice(before).map((c) => c.name)
  assert(!after.includes('rpa.listElements'), '失败还刷新了列表')
})

await checkAsync('渲染层：名称为空时不发 RPC，就地提示', async () => {
  const { calls } = await startOnce()
  nameNode().value = '   '
  const before = calls.length
  await saveNode().click()
  await flush()
  assert(calls.length === before, '空名字还是发了 RPC')
  assert(hasClass(noteNode(), 'error') && noteNode().textContent.length > 0, '空名字没给提示')
})

await checkAsync('渲染层：详情打开同一个编辑框，改名走 saveElement', async () => {
  const run = makeContext()
  run.api.setState(ONE_MISSING, [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'elemRow') && hasClass(n, 'asset'))
  assert(row, '没有资产行')
  const detail = walk(row).find((n) => hasClass(n, 'elemAct') && n.textContent === elemText.detail)
  assert(detail, '资产行没有详情按钮')
  await detail.click()
  await flush()
  assert(run.calls.some((c) => c.name === 'rpa.elementDetail'), '详情没取完整描述符')
  assert(dialogNode().hidden === false, '详情没弹编辑框')
  assert(nameNode().value === 'searchBox', `详情没带出名字：${nameNode().value}`)
  nameNode().value = '登录框'
  await saveNode().click()
  await flush()
  const saved = run.calls.find((c) => c.name === 'rpa.saveElement')
  assert(saved && saved.args.name === 'searchBox' && saved.args.newName === '登录框', `${saved?.args}`)
})

check('渲染层：待补行只有一个「补一下」，没有类型选择与手写入口', () => {
  const run = makeContext()
  run.api.setState(ONE_MISSING, [])
  const row = walk(nodeById('elemBody')).find((n) => hasClass(n, 'missing'))
  assert(row, '没有待补行')
  const buttons = walk(row).filter((n) => n.tagName === 'BUTTON')
  assert(buttons.length === 1, `待补行按钮数=${buttons.length}`)
  assert(buttons[0].textContent === elemText.fill, `主按钮文案=${buttons[0].textContent}`)
  assert(!walk(row).some((n) => hasClass(n, 'pickAlt')), '「换个方式」又回来了')
  assert(!walk(row).some((n) => hasClass(n, 'pickManualInput')), '手写选择器入口又回来了')
})

check('渲染层：面板里没有状态条/提示节点（提示全在桌面浮窗）', () => {
  const run = makeContext()
  run.api.setState(ONE_MISSING, [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  const nodes = walk(nodeById('elemBody'))
  for (const banned of ['pickBar', 'pickLive', 'pickDetail', 'elemNewNote']) {
    assert(!nodes.some((n) => hasClass(n, banned)), `面板里还有提示节点：${banned}`)
  }
  assert(!run.calls.some((c) => c.name === 'rpa.pickPlan'), '还在探测能力并播报状态')
})

// ---------- 5. 契约层（前后端同一条链） ----------
check('契约层：前端不再手写选择器、不再二次探测', () => {
  assert(!/rpa\.writeElement/.test(mainSrc), 'main.js 还在手写选择器入库')
  assert(!/rpa\.pickPlan/.test(mainSrc), 'main.js 还在先探测再开始')
  assert(/capturePoll',\s*\{\s*path:/.test(mainSrc), 'capturePoll 没带 path')
  assert(/rpa\.commitElement/.test(mainSrc), '入库没走确认 RPC')
  assert(/rpa\.discardCapture/.test(mainSrc), '取消没丢弃暂存')
})

check('契约层：后端没有手填写入口、捕获不落盘、名字允许中文', () => {
  assert(!/def write_element\(/.test(apiSrc), 'api.py 还留着 writeElement')
  assert(!/def pick_plan\(/.test(apiSrc), 'api.py 还留着 pickPlan')
  const startRegion = apiSrc.slice(apiSrc.indexOf('def start_capture'), apiSrc.indexOf('def _show_blocked'))
  assert(!/_persist_captured|write_bytes/.test(startRegion), 'startCapture 里还在落盘')
  assert(/def commit_element\(/.test(apiSrc), '没有确认入库的 RPC')
  assert(!/A-Za-z0-9_-/.test(apiSrc), '名称规则还是纯 ASCII（中文会被挡）')
  assert(/def _rewrite_refs\(/.test(apiSrc), '改名没联动流程引用')
})

const failed = results.filter((r) => !r.ok)
for (const r of results) {
  console.log(`${r.ok ? 'PASS' : 'FAIL'} ${r.name}${r.ok ? '' : ` — ${r.detail}`}`)
}
console.log(`ELEMENT PICK UI CHECK ${failed.length ? 'FAILED' : 'PASSED'} (${results.length - failed.length}/${results.length})`)
process.exit(failed.length ? 1 : 0)
