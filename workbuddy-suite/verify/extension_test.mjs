/**
 * 浏览器插件的「选择器质量」回归。
 *
 * 插件最有价值的不是那把高亮框，而是**算出来的选择器明天还能不能命中**。
 * 所以这里不看截图，直接把 content.js 注入一个真实页面，逐个元素问它：
 * 你给我什么选择器？唯一吗？是不是又退回了脆弱的 nth-of-type 路径？
 *
 * 跑法：node verify/extension_test.mjs
 */

import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SUITE = resolve(HERE, '..')
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PORT = 9334

const failures = []
function check(name, ok, detail = '') {
  if (ok) console.log(`[PASS] ${name}${detail ? `  ${detail}` : ''}`)
  else {
    failures.push(`${name}${detail ? ` — ${detail}` : ''}`)
    console.log(`[FAIL] ${name}${detail ? `  ${detail}` : ''}`)
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitFor(fn, timeout = 15000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const value = await fn()
    if (value) return value
    await sleep(120)
  }
  return null
}

class Cdp {
  constructor(url) {
    this.url = url
    this.seq = 0
    this.pending = new Map()
  }

  async open() {
    this.ws = new WebSocket(this.url)
    await new Promise((res, rej) => {
      this.ws.addEventListener('open', res, { once: true })
      this.ws.addEventListener('error', rej, { once: true })
    })
    this.ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      if (message.id && this.pending.has(message.id)) {
        const { resolve: res, reject } = this.pending.get(message.id)
        this.pending.delete(message.id)
        if (message.error) reject(new Error(message.error.message))
        else res(message.result)
      }
    })
    return this
  }

  send(method, params = {}) {
    const id = ++this.seq
    this.ws.send(JSON.stringify({ id, method, params }))
    return new Promise((res, rej) => {
      this.pending.set(id, { resolve: res, reject: rej })
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id)
          rej(new Error(`CDP timeout ${method}`))
        }
      }, 15000)
    })
  }

  async evaluate(expression) {
    const out = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (out.exceptionDetails) {
      throw new Error(out.exceptionDetails.exception?.description || out.exceptionDetails.text)
    }
    return out.result.value
  }

  close() {
    try {
      this.ws.close()
    } catch {
      /* 忽略 */
    }
  }
}

const profile = mkdtempSync(join(tmpdir(), 'wb-ext-'))
const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--no-proxy-server',
    '--disable-gpu',
    '--window-size=1200,900',
    'about:blank',
  ],
  { stdio: 'ignore' },
)

let cdp = null
try {
  const target = await waitFor(async () => {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
      return list.find((item) => item.type === 'page') || null
    } catch {
      return null
    }
  }, 20000)
  if (!target) throw new Error('Chrome 没起来')

  cdp = await new Cdp(target.webSocketDebuggerUrl).open()
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  const fixture = pathToFileURL(join(HERE, 'fixtures', 'picker.html')).href
  await cdp.send('Page.navigate', { url: fixture })
  await waitFor(() => cdp.evaluate('document.readyState === "complete" && Boolean(document.querySelector("#username"))'), 10000)

  // 先把 chrome.runtime 换成记录器：插件在页面里唯一依赖的宿主能力就是它
  await cdp.evaluate(`(() => {
    window.__sent = []
    window.chrome = {
      runtime: {
        sendMessage: (message, callback) => {
          window.__sent.push(message)
          if (callback) setTimeout(() => callback({ ok: true, id: window.__sent.length }), 0)
        },
        onMessage: { addListener: () => {} },
      },
    }
  })()`)

  const source = readFileSync(join(SUITE, 'extension', 'content.js'), 'utf8')
  await cdp.evaluate(source)
  check('content.js 注入成功并暴露拾取器', await cdp.evaluate('Boolean(window.__wbPicker)'))

  const describe = (selector) =>
    cdp.evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)})
      if (!el) return { missing: true }
      return window.__wbPicker.describe(el)
    })()`)

  const byId = await describe('#username')
  check('有 id 的元素直接用 id 选择器', byId.strategy === 'id' && byId.selector === '#username' && byId.matches === 1,
    JSON.stringify({ s: byId.selector, st: byId.strategy, m: byId.matches }))
  check('同时带出属性与尺寸（Agent 侧要用来判断）',
    byId.attributes?.name === 'user' && typeof byId.rect?.w === 'number' && byId.tag === 'input',
    JSON.stringify({ attrs: byId.attributes, rect: byId.rect }))

  const byTestId = await describe('[data-testid="submit-login"]')
  check('有 data-testid 时优先用它', byTestId.strategy === 'attr:data-testid' && byTestId.matches === 1,
    JSON.stringify({ s: byTestId.selector, st: byTestId.strategy }))
  check('按钮的可见文案被带出', byTestId.text === '登录', JSON.stringify(byTestId.text))

  const byClass = await describe('.css-1a2b3c')
  const classUnique = await cdp.evaluate(`document.querySelectorAll(${JSON.stringify(byClass.selector)}).length`)
  check('构建产物类名（css-xxxx）不被当作选择器', byClass.strategy === 'path', JSON.stringify({ s: byClass.selector, st: byClass.strategy }))
  check('退化路径至少两级（不会返回裸标签名 span）', byClass.selector.includes(' > '), byClass.selector)
  check('退化成路径时仍然唯一', classUnique === 1, `matches=${classUnique}`)

  const dup = await cdp.evaluate(`(() => {
    const second = document.querySelectorAll('#dup')[1]
    return window.__wbPicker.describe(second)
  })()`)
  const dupMatches = await cdp.evaluate(`document.querySelectorAll(${JSON.stringify(dup.selector)}).length`)
  check('id 重复时不硬用 #id', dup.selector !== '#dup' && dupMatches === 1,
    JSON.stringify({ s: dup.selector, st: dup.strategy, m: dupMatches }))

  const inList = await describe('#gamma')
  check('列表项里的元素优先用 id', inList.strategy === 'id' && inList.matches === 1, JSON.stringify(inList.selector))

  const shadow = await cdp.evaluate(`(() => {
    const root = document.getElementById('shadow-host').shadowRoot
    return window.__wbPicker.describe(root.querySelector('.go'))
  })()`)
  check('Shadow DOM 里的元素被如实标记', shadow.shadow === true, JSON.stringify({ s: shadow.selector, shadow: shadow.shadow, st: shadow.strategy }))

  // 点一下真实元素：走完整链路（高亮 → 点击 → 上报）
  const box = await cdp.evaluate(`(() => {
    const r = document.querySelector('#username').getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
  })()`)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: box.x, y: box.y })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 })
  await sleep(250)
  const sent = await cdp.evaluate('window.__sent')
  const payload = sent?.[0]?.payload
  check('点击后把拾取结果发给扩展后台', Boolean(payload) && payload.selector === '#username' && payload.source === 'extension',
    JSON.stringify(payload && { selector: payload.selector, source: payload.source }))
  check('上报里带页面 URL（Agent 需要知道在哪个页面点）', Boolean(payload?.url?.includes('picker.html')), payload?.url || '')
  check('上报里带候选选择器列表', Array.isArray(payload?.alternatives), JSON.stringify(payload?.alternatives))

  // Esc 取消：再点一次不应再上报
  await cdp.evaluate('window.__wbPicker.start()')
  const before = sent.length
  await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
  await sleep(120)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 })
  await sleep(200)
  const after = await cdp.evaluate('window.__sent.length')
  check('Esc 之后点击不再上报（拾取真的退出了）', after === before, `before=${before} after=${after}`)

  // 页面自身的点击行为没被永久劫持
  const pageOk = await cdp.evaluate(`(() => {
    let hit = 0
    document.querySelector('#password').addEventListener('click', () => { hit += 1 })
    document.querySelector('#password').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    return hit
  })()`)
  check('退出拾取后页面点击恢复正常', pageOk === 1, `hits=${pageOk}`)
} catch (error) {
  check('测试脚本自身执行完成', false, String(error?.stack || error))
} finally {
  if (cdp) cdp.close()
  chrome.kill()
}

console.log(`\n${failures.length} failed`)
if (failures.length) {
  for (const item of failures) console.log(` - ${item}`)
  process.exit(1)
}
