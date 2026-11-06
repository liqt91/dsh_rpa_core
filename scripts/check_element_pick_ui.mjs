// M66：元素拾取的 UI 契约判据（进默认门禁）。
//
// 分三层，缺一层都会漏：
//   1. 文案层 —— PICK_TEXT 与后端 _PICK_INSTRUCTIONS 的值不许出现实现名词；
//   2. 样式层 —— 命中区 ≥24×24、正文 ≥14px、焦点环、状态不只靠颜色；
//   3. 渲染层 —— 用最小 DOM shim **真跑** main.js 的渲染函数，断言每个待补行
//      恰好一个主按钮、覆盖项默认折叠、aria-live 与键盘出口都在。
//      只读源码证明不了「结构对但点不着」，只有跑起来能发现。
import fs from 'node:fs'
import vm from 'node:vm'
import path from 'node:path'

const ROOT = path.resolve(import.meta.dirname, '..')
const MAIN = path.join(ROOT, '.harness/demo/ppx_probe/app/gui/src/main.js')
const CSS = path.join(ROOT, '.harness/demo/ppx_probe/app/gui/src/style.css')
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
function assert(cond, msg) {
  if (!cond) throw new Error(msg)
}

const mainSrc = fs.readFileSync(MAIN, 'utf8')
const cssSrc = fs.readFileSync(CSS, 'utf8')
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

const pickText = vm.runInNewContext(`(${objectLiteral(mainSrc, 'const PICK_TEXT =')})`)
const pyInstructions = vm.runInNewContext(
  `(${objectLiteral(apiSrc, '_PICK_INSTRUCTIONS =').replace(/'/g, '"')})`,
)

check('文案表：所有用户可见值都不含实现名词', () => {
  const bad = []
  for (const [key, value] of Object.entries(pickText)) {
    for (const word of BANNED) {
      if (String(value).toLowerCase().includes(word.toLowerCase())) bad.push(`${key}:${word}`)
    }
  }
  for (const [key, value] of Object.entries(pyInstructions)) {
    for (const word of BANNED) {
      if (String(value).toLowerCase().includes(word.toLowerCase())) bad.push(`instructions.${key}:${word}`)
    }
  }
  assert(bad.length === 0, `命中：${bad.join(', ')}`)
})

check('文案表：状态键齐全（漏一个就会把 state 原名显示给用户）', () => {
  for (const key of ['checking', 'ready', 'waiting', 'bound', 'blocked', 'cancelled', 'timeout', 'failed']) {
    assert(typeof pickText[key] === 'string' && pickText[key], `缺 ${key}`)
  }
})

// ---------- 2. 样式层 ----------
function ruleBlock(selector) {
  const at = cssSrc.indexOf(selector)
  assert(at >= 0, `CSS 少了 ${selector}`)
  const open = cssSrc.indexOf('{', at)
  const close = cssSrc.indexOf('}', open)
  return cssSrc.slice(open + 1, close)
}
function px(block, prop) {
  const hit = block.match(new RegExp(`${prop}\\s*:\\s*(\\d+(?:\\.\\d+)?)px`))
  return hit ? Number(hit[1]) : null
}

check('样式层：主按钮命中区 ≥24×24、字号 ≥14px', () => {
  const block = ruleBlock('.pickMain, .pickAction')
  assert(px(block, 'min-height') >= 24, `min-height=${px(block, 'min-height')}`)
  assert(px(block, 'min-width') >= 24, `min-width=${px(block, 'min-width')}`)
  assert(px(block, 'font-size') >= 14, `font-size=${px(block, 'font-size')}`)
})

check('样式层：焦点环、状态符号、正文对比度口径', () => {
  assert(/:focus-visible/.test(cssSrc), '没有 focus-visible 规则')
  assert(/\.pickDot/.test(cssSrc), '状态只有颜色，没有图形')
  assert(/\.pickLiveText|\.pickLive\b/.test(cssSrc), '状态没有文字容器')
  assert(!/font-size:\s*(?:1[0-3](?:\.\d)?)px/.test(
    ruleBlock('.elemName') + ruleBlock('.elemMeta') + ruleBlock('.elemHead'),
  ), '元素面板还有 <14px 正文')
})

// ---------- 3. 渲染层（最小 DOM shim 真跑渲染函数） ----------
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase()
    this.children = []
    this.className = ''
    this.attributes = {}
    // 真实 DOM 会把 dataset.x 映射成 data-x 属性；shim 不映射就会把
    // 「data-state 没写」误判成「状态条没带状态」。
    const attrs = this.attributes
    this.dataset = new Proxy({}, {
      set(target, key, value) {
        target[key] = value
        attrs[`data-${String(key).replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`] = String(value)
        return true
      },
    })
    this.textContent = ''
    this.value = ''
    this.placeholder = ''
    this.open = false
    this.style = { setProperty() {} }
    this.classList = { add() {}, remove() {} }
  }
  append(...nodes) { for (const n of nodes) this.children.push(n) }
  replaceChildren(...nodes) { this.children = [...nodes] }
  setAttribute(k, v) { this.attributes[k] = v }
  addEventListener() {}
  scrollIntoView() {}
}
function walk(node, out = []) {
  out.push(node)
  for (const child of node.children || []) walk(child, out)
  return out
}

function makeContext({ missing, capabilities, responses = {} } = {}) {
  const body = new El('div')
  const timers = []
  const calls = []
  const doc = {
    createElement: (tag) => new El(tag),
    querySelector: (sel) => {
      if (sel.startsWith('#')) return new El('div')
      if (sel.startsWith('.')) return walk(body).find((n) => String(n.className).split(/\s+/).includes(sel.slice(1))) || null
      return null
    },
    querySelectorAll: () => [],
    addEventListener() {},
  }
  const defaults = {
    'rpa.bootstrap': { commands: [], flows: [], catalogDigest: '', python: '', catalogMs: 0 },
    'rpa.missingElements': { ok: true, missing, resolved: [], capabilities },
    'rpa.listElements': { ok: true, elements: [] },
    'rpa.pickPlan': { ok: true, blocked: false, fix: null, decision: { target: 'browser' }, capabilities },
    'rpa.startCapture': {
      ok: true, state: 'waiting', name: 'userField', nodeId: 'n1',
      instructions: '在页面上点一下这个元素', deadlineAt: Date.now() / 1000 + 60,
    },
    'rpa.capturePoll': { ok: true, state: 'bound', reason: '已抓到', boundNodeId: 'n1' },
    'rpa.cancelCapture': { ok: true, state: 'cancelled' },
    'rpa.writeElement': { ok: true, state: 'bound', boundNodeId: 'n1' },
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
    // 定时器只登记不自动跑：端到端判据自己推进时间，才能数清「用户点了几次」。
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length },
    clearTimeout: () => {},
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
    el: new Proxy({}, { get: () => new El('div') }),
    __body: body,
  }
  // 顶层 import 是 ESM 语法，vm 跑的是脚本；两处都不参与本判据的逻辑。
  const src = mainSrc.replace(/^import .*$/gm, '')
  const ctx = vm.createContext(sandbox)
  vm.runInContext(src, ctx, { filename: 'main.js' })
  vm.runInContext(`
    el.elemBody = globalThis.__body
    el.elemCount = document.createElement('span')
    pickMissing = ${JSON.stringify(missing)}
    pickElements = []
    pickCapabilities = ${JSON.stringify(capabilities)}
    pickState = null
    currentPath = 'C:/rpa-m66-fixture/flow.json'
    cards = [{ id: 'n1', no: 3 }, { id: 'n2', no: 5 }]
    renderElementsPanel()
    globalThis.__serialize = (node) => ({
      tag: node.tagName, cls: node.className || '', attrs: node.attributes || {},
      text: node.textContent || '', open: !!node.open,
      children: (node.children || []).map(globalThis.__serialize),
    })
    globalThis.__tree = globalThis.__serialize(el.elemBody)
    globalThis.__api = {
      startPickFlow, beginCapture, pollCapture, cancelPick,
      state: () => pickState,
    }
  `, ctx)
  const tree = vm.runInContext('globalThis.__tree', ctx)
  const api = vm.runInContext('globalThis.__api', ctx)
  return { ctx, timers, calls, tree, api }
}

function renderProbe(missing, capabilities) {
  return makeContext({ missing, capabilities }).tree
}

const flush = () => new Promise((resolve) => setImmediate(resolve))

async function runFlow(responses) {
  const { timers, calls, api } = makeContext({
    missing: TWO_MISSING,
    capabilities: CAPS_OK,
    responses,
  })
  const seen = []
  await api.startPickFlow(TWO_MISSING[0], null)
  seen.push(api.state().state)
  const auto = timers.find((t) => t.ms === 10000)
  assert(auto, '已就绪后没有自动开始的定时器')
  auto.fn() // 不是用户再点一次：10 秒到点自动开始
  await flush()
  seen.push(api.state().state)
  const tick = timers.find((t) => t.ms === 800)
  assert(tick, '没排轮询')
  await tick.fn()
  seen.push(api.state().state)
  return { seen, calls, state: api.state(), api }
}

const TWO_MISSING = [
  { name: 'userField', nodeId: 'n1', command: 'browser.input', decision: { target: 'browser' }, capabilityFix: null },
  { name: 'saveBtn', nodeId: 'n2', command: 'desktop.click', decision: { target: 'desktop' }, capabilityFix: null },
]
const CAPS_OK = { runtime: true, extensionOnline: true, fix: null }
const CAPS_OFF = { runtime: true, extensionOnline: false, fix: '先打开浏览器' }

let tree
check('渲染层：两个缺口都渲染出来、各恰好一个主按钮', () => {
  tree = renderProbe(TWO_MISSING, CAPS_OK)
  const rows = walk(tree).filter((n) => String(n.cls).split(/\s+/).includes('missing'))
  assert(rows.length === 2, `待补行数=${rows.length}`)
  for (const row of rows) {
    const nodes = walk(row)
    const mains = nodes.filter((n) => String(n.cls).split(/\s+/).includes('pickMain'))
    assert(mains.length === 1, `主按钮数=${mains.length}（技术选择题又回来了？）`)
    assert(mains[0].text === pickText.main, `主按钮文案=${mains[0].text}`)
    // 渲染层独立再查一遍：文案表改了、按钮也跟着改，只比相等会一起变绿。
    for (const word of BANNED) {
      assert(!String(mains[0].text).toLowerCase().includes(word.toLowerCase()),
        `主按钮把实现名词 ${word} 显示给用户了`)
    }
  }
})

check('渲染层：覆盖项默认折叠、每态一个键盘可达出口', () => {
  const rows = walk(tree).filter((n) => String(n.cls).split(/\s+/).includes('missing'))
  for (const row of rows) {
    const nodes = walk(row)
    const alt = nodes.find((n) => String(n.cls).split(/\s+/).includes('pickAlt'))
    assert(alt && alt.open === false, '换个方式默认展开了')
    assert(nodes.some((n) => String(n.cls).split(/\s+/).includes('pickManualInput')), '没有手动填写入口')
    assert(nodes.filter((n) => String(n.cls).split(/\s+/).includes('pickAltBtn')).length === 3, '覆盖项不是三项')
  }
})

check('渲染层：能力不在线时给状态条 + aria-live + 图形，不假装已就绪', () => {
  const off = renderProbe(TWO_MISSING, CAPS_OFF)
  const nodes = walk(off)
  const bar = nodes.find((n) => String(n.cls).split(/\s+/).includes('pickBar'))
  assert(bar, '能力不在线却没有状态条')
  assert(bar.attrs['data-state'] === 'offline', `state=${bar.attrs['data-state']}`)
  const live = nodes.find((n) => String(n.cls).split(/\s+/).includes('pickLive'))
  assert(live && live.attrs['aria-live'] === 'polite', '状态播报缺 aria-live')
  assert(nodes.some((n) => String(n.cls).split(/\s+/).includes('pickDot')), '状态只有颜色')
})

check('契约层：前端不传 target/browserType，轮询带 path，可取消', () => {
  assert(!/startCapture[\s\S]{0,240}browserType/.test(mainSrc), 'startCapture 还在传死参数')
  assert(/capturePoll',\s*\{\s*path:/.test(mainSrc), 'capturePoll 没带 path（复合键会串会话）')
  assert(/rpa\.cancelCapture/.test(mainSrc), '没有取消入口')
  assert(/rpa\.writeElement/.test(mainSrc), '没有键盘可达的降级出口')
  assert(/rpa\.pickPlan/.test(mainSrc), '探测没有前置到点按钮时')
})

check('契约层：后端决策唯一、探测在 spawn 之前、复合键', () => {
  assert(/def _pick_plan\(/.test(apiSrc), '没有唯一的探测+决策函数')
  assert(/plan = _pick_plan\(path, name, override\)/.test(apiSrc), 'startCapture 没复用 _pick_plan')
  assert(apiSrc.indexOf('plan = _pick_plan') < apiSrc.indexOf('proc = subprocess.Popen(', apiSrc.indexOf('def start_capture')),
    '能力探测跑到 spawn 后面去了')
  assert(/_CAPTURES: dict\[tuple\[str, str\]/.test(apiSrc), '_CAPTURES 不是复合键')
  assert(/resolve_target\(\(item or \{\}\)\.get\("command"\), capabilities, override\)/.test(apiSrc),
    'resolve_target 没吃到命令类型')
})

// ---------- 4. 端到端状态机（不占前台，不用真窗口） ----------
async function checkAsync(name, fn) {
  try {
    await fn()
    results.push({ name, ok: true })
  } catch (error) {
    results.push({ name, ok: false, detail: String(error.message || error) })
  }
}

await checkAsync('端到端：一次「补一下」走到已就绪，零技术选择', async () => {
  const { seen, calls, state } = await runFlow()
  assert(seen.join('>') === 'ready>waiting>bound', `状态流=${seen.join('>')}`)
  const started = calls.filter((c) => c.name === 'rpa.startCapture')
  assert(started.length === 1, `起会话 ${started.length} 次，用户不该重复操作`)
  assert(!('target' in started[0].args) && !('browserType' in started[0].args),
    '又把技术选择题塞回了调用参数')
  assert(started[0].args.override == null, '自动路径不该带 override')
  assert(calls.some((c) => c.name === 'rpa.pickPlan'), '探测没发生在开始之前')
  assert(state.state === 'bound' && state.name === 'userField' && state.step === '3',
    `成功态丢了上下文：${JSON.stringify(state)}`)
})

await checkAsync('端到端：取消后名字与步骤不丢', async () => {
  const { calls, api } = await runFlow({
    'rpa.capturePoll': { ok: true, state: 'waiting', reason: '等你点一下' },
  })
  await api.cancelPick()
  const state = api.state()
  assert(state.state === 'cancelled', `取消后=${state.state}`)
  assert(state.name === 'userField' && state.step === '3', `上下文丢了：${JSON.stringify(state)}`)
  assert(calls.some((c) => c.name === 'rpa.cancelCapture'), '取消没走到后端')
})

await checkAsync('端到端：超时后可重来，名字与步骤不丢', async () => {
  const { seen, state } = await runFlow({
    'rpa.capturePoll': { ok: true, state: 'timeout', reason: '时间到了，没收到这次点选' },
  })
  assert(seen.join('>') === 'ready>waiting>timeout', `状态流=${seen.join('>')}`)
  assert(state.name === 'userField' && state.step === '3', `上下文丢了：${JSON.stringify(state)}`)
})

const failed = results.filter((r) => !r.ok)
for (const r of results) {
  console.log(`${r.ok ? 'PASS' : 'FAIL'} ${r.name}${r.ok ? '' : ` — ${r.detail}`}`)
}
console.log(`ELEMENT PICK UI CHECK ${failed.length ? 'FAILED' : 'PASSED'} (${results.length - failed.length}/${results.length})`)
process.exit(failed.length ? 1 : 0)
