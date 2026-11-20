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
const EDIT_DIALOG = path.join(ROOT, 'src/rpa_core/capture_kit/edit_dialog.py')
const SELECTOR_TREE = path.join(ROOT, 'src/rpa_core/capture_kit/selector_tree.py')

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
const editDialogSrc = fs.readFileSync(EDIT_DIALOG, 'utf8')
const selectorTreeSrc = fs.readFileSync(SELECTOR_TREE, 'utf8')
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
    'deleteBlockedTitle', 'deleteBlockedHint', 'deleteBlockedRef', 'deleteBlockedNote',
    'refDetail', 'refNone',
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
check('结构层：元素面板只有两个捕获按钮，管理按钮在白名单内', () => {
  assert(html.includes('id="elemCapture"'), 'index.html 缺捕获按钮')
  for (const gone of [
    'elemNewName', 'elemNewWhere', 'elemNewCapture', 'elemNewSelector',
    'elemNewWrite', 'elemNewNote',
  ]) {
    assert(!html.includes(`id="${gone}"`), `旧的工具条控件还在：${gone}`)
  }
  const panel = html.slice(html.indexOf('data-panel="elements"'), html.indexOf('data-panel="globals"'))
  const buttons = [...panel.matchAll(/<button[^>]*id="([^"]+)"/g)].map((m) => m[1])
  // 捕获入口必须恰好两个且顺序固定；元素库自己的管理按钮只允许白名单里的
  // 「全部校验」。白名单是等值集合，不是「放行任何新按钮」的宽松判断。
  const captures = buttons.filter((id) => id === 'elemCapture' || id === 'elemCaptureImage')
  assert(captures.length === 2 && captures[0] === 'elemCapture'
    && captures[1] === 'elemCaptureImage', `捕获按钮：${buttons}`)
  const allowed = new Set(['elemCapture', 'elemCaptureImage', 'elemVerifyAll'])
  const extra = buttons.filter((id) => !allowed.has(id))
  assert(extra.length === 0, `面板里混进了旧入口按钮：${extra}`)
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
  replaceChildren(...nodes) { this.children = [...nodes]; this.scrollTop = 0 }
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

function makeContext({ responses = {}, sequences = {} } = {}) {
  // ★ registry 是模块级的：节点跨用例共享会让旧用例的事件处理器在新用例的
  // click 上全部重放（实测：某用例弹了 web 详情框没关，下一个用例点「保存」
  // 时旧处理器替它 closeElemDialog，当前用例的断言凭空变红——N9 注入才暴露）。
  // 每个用例一副新节点，旧处理器留在旧对象上，谁也点不到。
  registry.clear()
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
    // 默认按「桌面编辑框不可用」处理：详情走页面兜底——这样既有详情用例覆盖的是
    // 非 Windows 路径；win32 路径由 sequences 显式给 editing 响应的用例覆盖。
    'rpa.editElement': { ok: false, code: 'EDIT_DIALOG_UNAVAILABLE' },
    'window.restore': { ok: true },
  }
  const sandbox = {
    document: doc,
    ppx: {
      call: async (name, args) => {
        calls.push({ name, args })
        const seq = sequences[name]
        if (seq && seq.length) return { ...(defaults[name] || { ok: true }), ...seq.shift() }
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
  // index.html 里 #elemDialog 起始就是 hidden（编辑框只在需要时才打开），
  // shim 要如实反映，否则「没弹框」的断言会撞在 El 的默认 hidden=false 上。
  nodeById('elemDialog').hidden = true
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
  assert(commit.args.capture_id === 'c1', `capture_id 没带上：${commit.args}`)
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

await checkAsync('渲染层：桌面编辑框开着（editing）只轮询，不弹页面框', async () => {
  const run = await startOnce({
    sequences: { 'rpa.capturePoll': [{ ok: true, state: 'editing' }] },
  })
  assert(dialogNode().hidden !== false, 'editing 却弹了页面框')
  assert(run.api.session(), 'editing 就把会话扔了')
  assert(run.timers.some((t) => t.ms === 1000), 'editing 没排下一轮轮询')
})

await checkAsync('渲染层：桌面确认入库（committed）刷新资产并收尾', async () => {
  const run = await startOnce({
    sequences: { 'rpa.capturePoll': [{ ok: true, state: 'committed', name: '搜索框' }] },
  })
  await flush()
  assert(dialogNode().hidden !== false, 'committed 还弹页面框')
  assert(!run.api.session(), 'committed 后会话没清')
  assert(run.calls.some((c) => c.name === 'rpa.listElements'), 'committed 没刷新资产列表')
  assert(!run.calls.some((c) => c.name === 'rpa.commitElement'), 'win32 路径不该再调 commitElement')
})

await checkAsync('渲染层：详情优先弹桌面编辑框，页面框只是兜底', async () => {
  const run = makeContext({
    responses: { 'rpa.editElement': { ok: true, state: 'editing', editId: 'e9' } },
    sequences: { 'rpa.capturePoll': [{ ok: true, state: 'committed', name: '登录框' }] },
  })
  run.api.setState(ONE_MISSING, [{ name: 'searchBox', kind: 'browser', selector: '#q' }])
  await run.api.openAssetDetail('searchBox')
  await flush()
  const edits = run.calls.filter((c) => c.name === 'rpa.editElement')
  assert(edits.length === 1 && edits[0].args.name === 'searchBox',
    `没走桌面编辑框：${run.calls.map((c) => c.name)}`)
  assert(!run.calls.some((c) => c.name === 'rpa.elementDetail'), '桌面框可用时却走了 elementDetail')
  assert(dialogNode().hidden !== false, '桌面框可用时却弹了页面框')
  const tick = run.timers.find((t) => t.ms === 800)
  assert(tick, '详情编辑没排轮询')
  await tick.fn()
  await flush()
  assert(run.calls.some((c) => c.name === 'rpa.listElements'), '改名完成后没刷新列表')
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
  assert(saved && saved.args.name === 'searchBox' && saved.args.new_name === '登录框', `${saved?.args}`)
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

check('渲染层：面板里没有状态条/提示节点（说明走可关闭的网页弹框）', () => {
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

// 行级等值断言：只搜「on_commit 出现过」会被 lambda 自身喂成恒绿。
// 控制器从 M104 起传 (name, selector, edit_state)：编辑态勾选快照必须与
// 定位文本同一次交给宿主，否则重开详情时用户取消过的层级全丢。
// 接线停在两参时用户点保存直接 TypeError「入库失败」。
check('契约层：确认框保存接线收三参并透传 selector 与勾选快照', () => {
  const lines = apiSrc.split('\n')
  const at = lines.findIndex((l) => /^\s*on_commit=lambda /.test(l))
  assert(at >= 0, 'start_capture 没接 on_commit')
  const joined = (lines[at] || '') + (lines[at + 1] || '')
  assert(
    /^\s*on_commit=lambda name, selector, edit_state=None: _commit_from_dialog\(\s*entry, name, selector, edit_state\),\s*$/.test(joined),
    `保存接线没把 selector/edit_state 透传给入库：${joined.trim()}`,
  )
})

// ---------- 6. kit 边界：只管捕获/编辑，删除与引用检查归网页端 ----------
check('契约层：删前引用检查在网页端，删除不借桌面浮窗', () => {
  const head = apiSrc.indexOf('def delete_element(')
  assert(head > 0, '找不到 delete_element')
  const region = apiSrc.slice(head, head + 1100)
  assert(!/_notify\(/.test(region), '删除失败还在弹桌面浮窗（关不掉的那个框）')
  assert(/referencedBy/.test(region), '没把引用节点回给网页端')

  const web = mainSrc.slice(
    mainSrc.indexOf('async function deleteAsset'),
    mainSrc.indexOf('function missingRow'),
  )
  assert(web.includes('rpa.elementDetail'), '网页端删前没查引用')
  assert(
    web.indexOf('rpa.elementDetail') < web.indexOf('rpa.deleteElement'),
    '删前检查没排在删除 RPC 之前',
  )
  assert(/refs\.length/.test(web), '查到引用没有拦下删除')
  assert(/showDeleteProblem/.test(web), '删不掉时网页端没给可关闭的说明')
})

check('契约层：捕获期 Esc 接取消，编辑态不抢桌面编辑框', () => {
  assert(/rpa\.cancelCapture/.test(mainSrc), '网页端 Esc 没接取消 RPC')
  assert(/capturePhase\s*===\s*'pending'/.test(mainSrc), '没把 editing 态排除')
  // 行级断言：光「源码里出现过这个函数名」会被函数定义自己糊弄成恒绿。
  assert(/^\s*cancelCaptureByEsc\(\)\s*$/m.test(mainSrc), 'Esc 分支没调取消')
})

// ---------- 7. ref-only：引用得看得见、找得到、改得掉 ----------
// 用户实测：删元素提示「第三步引用它」，可第三步 selector 显示「未设置」，
// 面板里也没有任何一项指着这个资产 —— 引用在 elementRefs 里，界面却完全没渲染。
check('契约层：引用清单带 nodeId/param/name，删除提示逐条摊开字段', () => {
  const head = apiSrc.indexOf('def _all_element_refs(')
  assert(head > 0, '找不到唯一权威的引用扫描器')
  const region = apiSrc.slice(head, head + 1500)
  // ★ 精确产出锚点：只判 region.includes('"param"') 会被下面的排序键
  // `ref["param"]` 喂成恒绿——产出里删掉 param 字段它照样过。
  for (const field of [
    '"nodeId": str(node.get("id") or "")',
    '"param": str(param)',
    '"name": str(ref)',
    '"command": str(node.get("command") or "")',
  ]) {
    assert(region.includes(field), `引用清单缺产出字段 ${field}，网页端说不清哪个参数`)
  }
  assert(/for key in _NESTED/.test(region), '引用扫描没递归真实 AST 分支')
  assert(/def _element_references\(/.test(region), '删除护栏没复用同一个扫描器')

  const detail = mainSrc.slice(
    mainSrc.indexOf('async function openAssetDetail'),
    mainSrc.indexOf('function showBrokenDetail'),
  )
  assert(/ref\.param/.test(detail) && /ref\.name/.test(detail), '详情没读结构化引用字段')
  const link = mainSrc.slice(mainSrc.indexOf('function refLink'), mainSrc.indexOf('function showDeleteProblem'))
  assert(/param: ref\.param/.test(link) && /name: ref\.name/.test(link), '删除提示没带字段/资产名')
  assert(/cards\.find/.test(link) && /selectCard\(card\)/.test(link), '引用行没跳到对应节点')
})

check('契约层：被引用参数不假报未设置，解除引用随保存全量写回', () => {
  const build = mainSrc.slice(
    mainSrc.indexOf('function buildControl'),
    mainSrc.indexOf('function groupFields'),
  )
  assert(/const refName = pendingUnbinds\.has\(field\.key\) \? null : nodeRefs\[field\.key\]/.test(build),
    '参数表没读 elementRefs')
  assert(/control\.type = 'hidden'/.test(build), '被引用字段没保留全量提交控件（会 INCOMPLETE_SUBMIT）')
  assert(/已由元素引用/.test(build), '被引用字段没换成引用说明，还在假报「必填但当前未设置」')
  const panel = mainSrc.slice(
    mainSrc.indexOf('function buildRefPanel'),
    mainSrc.indexOf('function renderForm'),
  )
  assert(/Object\.entries\(nodeRefs\)/.test(panel) && /pendingUnbinds\.add\(param\)/.test(panel),
    '引用面板没给可撤销的解除入口')
  const save = mainSrc.slice(
    mainSrc.indexOf('async function saveParams'),
    mainSrc.indexOf('function revertParams'),
  )
  assert(/element_refs:/.test(save), '保存没提交 element_refs')
  assert(/!pendingUnbinds\.has\(key\)/.test(save), '解除项没从全量提交里去掉')
  const apiSave = apiSrc.slice(
    apiSrc.indexOf('def save_node_params'),
    apiSrc.indexOf('@api_method("rpa.saveFlowInputs")'),
  )
  assert(/element_refs: dict\[str, str\] \| None = None/.test(apiSave), '服务端没接 element_refs')
  assert(/node\.pop\("elementRefs", None\)/.test(apiSave), '清空引用没真的删掉键')
})

check('结构层：编辑框有层级/属性/定位区与三个新出口', () => {
  const dialog = html.slice(html.indexOf('id="elemDialog"'))
  for (const id of [
    'elemDialogEditor', 'elemTreePane', 'elemAttrPane', 'elemSimplePane',
    'elemDialogLevels', 'elemDialogAttrs', 'elemDialogSelector',
    'elemDialogPreview', 'elemDialogRecapture', 'elemDialogValidate',
    'elemDialogSaveContinue',
  ]) {
    assert(dialog.includes(`id="${id}"`), `编辑框缺 #${id}`)
  }
})

check('契约层：编辑模型/重组/校验/重捕走语义 RPC，前端不拼 css', () => {
  for (const needle of [
    "ppx.call('rpa.elementEditModel'",
    "ppx.call('rpa.elementCompose'",
    "ppx.call('rpa.elementValidate'",
    "ppx.call('rpa.recaptureElement'",
    'confirmElemDialog(true)',
    'dialogLevels = levels.map(',
  ]) {
    assert(mainSrc.includes(needle), `main.js 缺 ${needle}`)
  }
  for (const rpc of [
    'rpa.elementEditModel', 'rpa.elementCompose', 'rpa.elementValidate', 'rpa.recaptureElement',
  ]) {
    assert(apiSrc.includes(`@api_method("${rpc}")`), `api.py 缺 ${rpc} 的定义`)
  }
  assert(!/selector\s*=\s*[`'"]#\$\{/.test(mainSrc), '前端自己拼了 css 选择器')
})

check('契约层：层级行点文字只看属性、点左方框才切换勾选', () => {
  for (const needle of [
    'win32con.BS_LEFT | win32con.BS_FLAT', // 行是纯文字按钮：无勾框，hover 重绘不盖小方框
    '_ID_LEVEL_CHECK_BASE + index',
    '("level_check", index)',
    'if role == "level" and notif == 0:  # BN_CLICKED：只选中看属性，不改勾选',
    'selector_tree.level_included_after_toggle(',
  ]) {
    assert(editDialogSrc.includes(needle), `edit_dialog.py 缺 ${needle}`)
  }
  // 行与小方框必须分开摆：两者同 x 重叠时，hover 重绘的空勾框看起来像“勾没了”
  assert(!/BS_CHECKBOX \| win32con\.BS_LEFT/.test(editDialogSrc),
    '层级行又变回 BS_CHECKBOX，与小方框重叠')
  assert(/win32gui\.SetWindowPos\(\s*box, 0, 16, y/.test(editDialogSrc),
    '小方框没摆在 x=16')
  assert(/win32gui\.SetWindowPos\(\s*row, 0, 44, y/.test(editDialogSrc),
    '行文字没避开小方框（x=44）')
  // 目标级（链尾）不再整行禁用成灰色，但仍强制参与定位
  assert(!/EnableWindow\(box, False\)/.test(editDialogSrc), '目标级又被整行禁用成灰色')
  assert(selectorTreeSrc.includes('def level_included_after_toggle('), '内核缺目标级勾选兜底')
  assert(mainSrc.includes('levelIncludedAfterToggle(dialogLevels.length, index'),
    'Web 层级树没走同一条目标级兜底')
  assert(!mainSrc.includes('box.disabled = index === dialogLevels.length - 1'),
    'Web 目标级复选框还被禁用成灰色')
})

check('契约层：多层级树重绘不丢滚动位置，原生框接滚轮', () => {
  const keepers = (mainSrc.match(/const keepTop = container\.scrollTop/g) || []).length
  const restorers = (mainSrc.match(/container\.scrollTop = keepTop/g) || []).length
  assert(keepers === 2 && restorers === 2,
    `Web 层级/节点树都要存+还原 scrollTop，实际 ${keepers}/${restorers}`)
  assert(!/row\.addEventListener\('click'[\s\S]{0,240}renderElemLevels\(\)/.test(mainSrc),
    'Web 点层级行又整树重绘了（滚动位置会跳回顶部）')
  for (const needle of [
    'def _on_mouse_wheel(self, hwnd, wparam)',
    'if msg == win32con.WM_MOUSEWHEEL:',
    'self._sync_level_checks(hwnd)',
  ]) {
    assert(editDialogSrc.includes(needle), `edit_dialog.py 缺 ${needle}`)
  }
})

check('契约层：勾属性自动勾上所在层，取消属性不反取消', () => {
  // 内核一条布尔口径，四条产出路径（原生层级/原生桌面/Web 层级/Web 桌面）都调它
  assert(selectorTreeSrc.includes('def included_after_attr_check('), '内核缺属性联动纯函数')
  assert(/return bool\(included\) or bool\(attr_on\)/.test(selectorTreeSrc),
    '属性联动口径不是「勾上拉进来、取消不推出去」')
  const nativeHits = (editDialogSrc.match(/selector_tree\.included_after_attr_check\(/g) || []).length
  assert(nativeHits === 2, `原生两条属性路径该各调一次内核口径，实际 ${nativeHits}`)
  const webHits = (mainSrc.match(/includedAfterAttrCheck\(/g) || []).length
  assert(webHits === 3, `Web 应有一处定义 + 两处调用，实际 ${webHits}`)
  assert(/level\.included = includedAfterAttrCheck\(/.test(mainSrc), 'Web 层级属性没联动层勾选')
  assert(/node\.selected = includedAfterAttrCheck\(/.test(mainSrc), 'Web 桌面属性没联动节点勾选')
  assert(/selector_tree\.included_after_attr_check\(\s*level\["included"\]/.test(editDialogSrc),
    '原生层级属性没调内核口径')
  assert(/selector_tree\.included_after_attr_check\(\s*node\.get\("selected"/.test(editDialogSrc),
    '原生桌面属性没调内核口径')
  // 联动后要同步复选框 UI，不然数据勾了、界面还是空框
  assert(/self\._level_check_boxes\[self\._selected\]/.test(editDialogSrc),
    '原生层级属性联动没同步小方框')
  assert(/self\._node_boxes\[self\._selected_node\]/.test(editDialogSrc),
    '原生桌面属性联动没同步节点框')
  assert(/if \(level\.included !== wasIncluded\) renderElemLevels\(\)/.test(mainSrc),
    'Web 层级属性联动没重绘层级框')
  assert(/if \(node\.selected !== wasSelected\) renderDesktopLevels\(\)/.test(mainSrc),
    'Web 桌面属性联动没重绘节点框')
})

check('契约层：参数面板的选择入口拿内核参数键集合', () => {
  for (const needle of [
    "ppx.call('rpa.elementCandidates'",
    'elemCapableKeys.has(field.key)',
    'nodeRefs[field.key] = name',
  ]) {
    assert(mainSrc.includes(needle), `main.js 缺 ${needle}`)
  }
  assert(apiSrc.includes('element_capable_keys()'), '候选集合没取内核权威')
  assert(apiSrc.includes('element_kind_for_param_key(param_key)'), '没按参数键过滤元素类型')
})

await checkAsync('渲染层：层级模型渲染成树，组合选择器由后端给', async () => {
  const levels = [
    { tag: 'html', included: false, id: { on: false, value: '' }, classes: [], nth: { on: false, value: '' } },
    { tag: 'div', included: true, id: { on: true, value: 'root' }, classes: [], nth: { on: false, value: '' } },
  ]
  const { calls } = await startOnce({
    responses: {
      'rpa.elementEditModel': { ok: true, name: '元素1', kind: 'browser', selector: '#root', levels },
      'rpa.elementCompose': { ok: true, selector: '#root' },
    },
  })
  assert(calls.some((c) => c.name === 'rpa.elementEditModel'), '没取编辑模型')
  assert(nodeById('elemDialogLevels').children.length === 2, '层级树没渲染出来')
  assert(nodeById('elemTreePane').hidden === false, '层级面板没显示')
  assert(nodeById('elemSimplePane').hidden === true, '简式定位没隐藏')
  assert(nodeById('elemDialogPreview').value === '#root', '组合选择器没填')
})

await checkAsync('渲染层：网页层级点文字只选中，目标级方框点掉弹回', async () => {
  const levels = [
    { tag: 'html', included: false, id: { on: false, value: '' }, classes: [], nth: { on: false, value: '' } },
    { tag: 'span', included: true, id: { on: true, value: 'root' }, classes: [], nth: { on: false, value: '' } },
  ]
  const { calls } = await startOnce({
    responses: {
      'rpa.elementEditModel': { ok: true, name: '元素1', kind: 'browser', selector: '#root', levels },
      'rpa.elementCompose': { ok: true, selector: '#root' },
    },
  })
  const rows = () => nodeById('elemDialogLevels').children
  const boxes = () => walk(nodeById('elemDialogLevels')).filter((n) => n.type === 'checkbox')
  assert(boxes().length === 2 && boxes()[1].disabled === false, '目标级复选框不该灰掉')

  await rows()[0].click()
  await flush()
  assert(hasClass(rows()[0], 'selected'), '点层级文字没选中该层')
  assert(boxes()[0].checked === false, '点层级文字不该切换勾选')

  boxes()[1].checked = false
  await boxes()[1].handlers.change[0]({ type: 'change' })
  await flush()
  const compose = calls.filter((c) => c.name === 'rpa.elementCompose').at(-1)
  assert(compose, '目标级勾选弹回后没触发重组')
  assert(compose.args.levels[1].included === true, '目标级被勾掉了')
  assert(boxes()[1].checked === true, '目标级复选框没弹回')
})

await checkAsync('渲染层：多层级点行查看属性不丢滚动位置', async () => {
  const levels = Array.from({ length: 12 }, (_, i) => ({
    tag: `tag${i}`, included: i === 11,
    id: { on: false, value: '' }, classes: [], nth: { on: false, value: '' },
  }))
  await startOnce({
    responses: {
      'rpa.elementEditModel': { ok: true, name: '多层级', kind: 'browser', selector: 'tag11', levels },
      'rpa.elementCompose': { ok: true, selector: 'tag11' },
    },
  })
  const container = nodeById('elemDialogLevels')
  assert(container.children.length === 12, '12 层没全渲染出来')
  container.scrollTop = 42
  await container.children[3].click()
  await flush()
  assert(container.scrollTop === 42, '点行看属性把层级区滚回顶部')
  assert(hasClass(container.children[3], 'selected'), '点行没选中该层')
})

await checkAsync('渲染层：未勾层勾属性会自动勾上层，取消属性不反取消', async () => {
  const levels = [
    { tag: 'html', included: false, id: { on: false, value: '' },
      classes: [{ on: false, value: 'root', mode: '精确' }], nth: { on: false, value: '' } },
    { tag: 'span', included: true, id: { on: true, value: 'root' },
      classes: [], nth: { on: false, value: '' } },
  ]
  const { calls } = await startOnce({
    responses: {
      'rpa.elementEditModel': { ok: true, name: '元素1', kind: 'browser', selector: '#root', levels },
      'rpa.elementCompose': { ok: true, selector: '#root' },
    },
  })
  const levelBoxes = () => walk(nodeById('elemDialogLevels')).filter((n) => n.type === 'checkbox')
  const attrBoxes = () => walk(nodeById('elemDialogAttrs')).filter((n) => n.type === 'checkbox')
  // 点第一层（未勾）看它的属性：id 框 + class 框
  await nodeById('elemDialogLevels').children[0].click()
  await flush()
  assert(levelBoxes()[0].checked === false, '起始第一层就不该勾选')
  const classBox = attrBoxes()[1] // id 是第 0 行，class 是第 1 行
  assert(classBox && classBox.checked === false, '起始 class 属性不该勾选')
  classBox.checked = true
  await classBox.handlers.change[0]({ type: 'change' })
  await flush()
  assert(levelBoxes()[0].checked === true, '勾上属性后所在层复选框没跟着勾')
  const last = calls.filter((c) => c.name === 'rpa.elementCompose').at(-1)
  assert(last && last.args.levels[0].included === true, '联动没进重组请求')
  // 取消该属性：层应保持勾选
  await nodeById('elemDialogLevels').children[0].click()
  await flush()
  const again = walk(nodeById('elemDialogAttrs')).filter((n) => n.type === 'checkbox')[1]
  again.checked = false
  await again.handlers.change[0]({ type: 'change' })
  await flush()
  assert(levelBoxes()[0].checked === true, '取消属性不该反过来取消层')
})

check('契约层：桌面编辑也走内核模型与重组，前端不拼 locator', () => {
  for (const needle of [
    'dialogEditorKind = \'desktop\'',
    'renderDesktopLevels()',
    'renderDesktopAttrs()',
    'nodes: dialogNodes',
    'capture_id: dialogCaptureId',
    'dialogCaptureId = options.captureId || null',
  ]) {
    assert(mainSrc.includes(needle), `main.js 缺 ${needle}`)
  }
  for (const needle of [
    'compose_selector as _kernel_compose_selector',
    'nodes: list[dict[str, Any]] | None = None',
    '_kernel_compose_selector(descriptor, nodes)',
  ]) {
    assert(apiSrc.includes(needle), `api.py 缺 ${needle}`)
  }
  // 匹配方式只给运行期真支持的字段，别做点了不生效的假下拉
  const attrs = mainSrc.slice(
    mainSrc.indexOf('function renderDesktopAttrs'),
    mainSrc.indexOf('async function refreshElemCompose'),
  )
  assert(attrs.includes("attr.field === 'name' || attr.field === 'automationId'"),
    '桌面属性表的匹配方式没限定到运行期支持的字段')
  assert(attrs.includes('mode.disabled = !canFuzzy'), '其它属性行的匹配方式没锁成精确')
})

await checkAsync('渲染层：桌面模型渲染成层级树与属性表，勾选让后端重组 selector', async () => {
  const nodes = [
    { type: 'wnd', name: '微信', selected: false, readonly: true, attributes: [
      { field: 'windowTitle', label: '窗口标题', value: '微信', operator: '精确',
        required: true, readonly: true },
    ] },
    { type: 'Button', name: '发送', selected: true, attributes: [
      { field: 'controlType', label: '控件类型', value: 'Button', operator: '精确', required: true },
      { field: 'name', label: '名称', value: '发送', operator: '精确', required: true },
    ] },
  ]
  const { calls } = await startOnce({
    responses: {
      'rpa.elementEditModel': { ok: true, name: '发送按钮', kind: 'desktop',
        selector: '{"locator":{"backend":"uia","name":"发送"}}', nodes },
      'rpa.elementCompose': { ok: true, selector: '{"locator":{"backend":"uia","name":"新"}}' },
    },
  })
  assert(nodeById('elemDialogLevels').children.length === 2, '桌面层级树没渲染')
  assert(nodeById('elemTreePane').hidden === false, '桌面层级面板没显示')
  assert(nodeById('elemDialogAttrs').children.length === 2, '目标级属性表没渲染')
  const boxes = walk(nodeById('elemDialogAttrs')).filter((n) => n.type === 'checkbox')
  assert(boxes.length === 2 && !boxes[0].disabled && !boxes[1].disabled,
    '目标级两行属性该可勾选')
  const selects = walk(nodeById('elemDialogAttrs')).filter((n) => n.tagName === 'SELECT')
  assert(selects.length === 2 && selects[0].disabled && selects[1].disabled === false,
    '控件类型该锁精确、名称该允许换匹配方式')

  // 点窗口信息行：属性表切成只读，不能让用户改出运行期不认的字段
  await nodeById('elemDialogLevels').children[0].click()
  await flush()
  const winBoxes = walk(nodeById('elemDialogAttrs')).filter((n) => n.type === 'checkbox')
  assert(winBoxes.length === 1 && winBoxes[0].disabled, '窗口信息行不该可改')
  assert(nodeById('elemDialogAttrs').children.length === 1, '窗口属性表没切成单行')

  // 点回目标级，勾掉名称属性 → 后端重组（带 capture_id 才保住 candidates）
  await nodeById('elemDialogLevels').children[1].click()
  await flush()
  const targetBoxes = walk(nodeById('elemDialogAttrs')).filter((n) => n.type === 'checkbox')
  targetBoxes[1].checked = false
  await targetBoxes[1].handlers.change[0]({ type: 'change' })
  await flush()
  const compose = calls.filter((c) => c.name === 'rpa.elementCompose').at(-1)
  assert(compose, '勾属性后没让后端重组')
  assert(Array.isArray(compose.args.nodes), '重组没把桌面 nodes 交给后端')
  assert(compose.args.capture_id === 'c1', `没带 capture_id：${JSON.stringify(compose.args)}`)
  assert(nodeById('elemDialogPreview').value.includes('"新"'), '桌面组合定位预览没更新')
})

const failed = results.filter((r) => !r.ok)
for (const r of results) {
  console.log(`${r.ok ? 'PASS' : 'FAIL'} ${r.name}${r.ok ? '' : ` — ${r.detail}`}`)
}
console.log(`ELEMENT PICK UI CHECK ${failed.length ? 'FAILED' : 'PASSED'} (${results.length - failed.length}/${results.length})`)
process.exit(failed.length ? 1 : 0)
