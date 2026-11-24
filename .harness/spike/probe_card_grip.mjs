/* 卡片拖拽把手探针：真 main.js + 真 CSS，在 headless Chrome 里验
 * 1) 把手在每张卡最左侧；2) 命中框够大；3) 从把手拖出的是 move 载荷；
 * 4) 空分支文案说「拖入指令」而不是「点选后添加」。
 * 跑法：node .harness/spike/probe_card_grip.mjs （需先在 gui 目录起 vite 5199）
 */
import { spawn } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PORT = 9345
const BASE = 'http://localhost:5199/_layout/probe.html?flow=' +
  encodeURIComponent('apps/editor/_sandbox/struct.json') + '&click=0'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

class CDP {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    ws.addEventListener('message', (e) => {
      const msg = JSON.parse(e.data)
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id)
        this.pending.delete(msg.id)
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
      }
    })
  }
  send(method, params = {}) {
    const id = ++this.id
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }
  async evaluate(expression) {
    const r = await this.send('Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true, userGesture: true,
    })
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400))
    return r.result.value
  }
  async waitFor(expression, timeout = 10000) {
    const t0 = Date.now()
    while (Date.now() - t0 < timeout) {
      try {
        if (await this.evaluate(expression)) return true
      } catch {}
      await sleep(100)
    }
    throw new Error(`waitFor timeout: ${expression}`)
  }
}

const failures = []
const check = (label, ok, detail = '') => {
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${label}${detail ? '  ' + detail : ''}`)
  if (!ok) failures.push(label)
}

const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', `--remote-debugging-port=${PORT}`,
  '--user-data-dir=' + join(tmpdir(), `ppx-card-grip-${process.pid}`),
  '--no-first-run', '--no-default-browser-check', 'about:blank',
], { stdio: ['ignore', 'pipe', 'pipe'] })
let chromeErr = ''
chrome.stderr.on('data', (d) => { chromeErr += d.toString() })

try {
  let cdp
  for (let i = 0; i < 40 && !cdp; i++) {
    try {
      const list = await fetch(`http://127.0.0.1:${PORT}/json/list`).then((r) => r.json())
      const page = list.find((t) => t.type === 'page')
      if (page) {
        const ws = new WebSocket(page.webSocketDebuggerUrl)
        await new Promise((res, rej) => {
          ws.addEventListener('open', res, { once: true })
          ws.addEventListener('error', rej, { once: true })
        })
        cdp = new CDP(ws)
      }
    } catch {}
    if (!cdp) await sleep(250)
  }
  if (!cdp) throw new Error(`CDP 连不上\n${chromeErr.slice(-1000)}`)

  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 1280, height: 720, deviceScaleFactor: 1, mobile: false,
  })
  await cdp.send('Page.navigate', { url: BASE })
  await cdp.waitFor('window.__MOCK__ && window.__MOCK__.ready === true')
  await cdp.waitFor("document.querySelector('#cards .card .grip') !== null")

  const shape = await cdp.evaluate(`(() => {
    const rows = [...document.querySelectorAll('#cards .card:not(.group)')].filter((c) => c.querySelector('.grip'))
    const g = rows[0]?.querySelector('.grip')
    const box = g?.getBoundingClientRect()
    return {
      count: rows.length,
      leftMost: rows.every((c) => {
        const g = c.querySelector('.grip')
        const n = c.querySelector('.no')
        return g && n && g.getBoundingClientRect().left < n.getBoundingClientRect().left
      }),
      lastChild: rows[0]?.lastElementChild?.className || '',
      width: box ? Math.round(box.width) : 0,
      height: box ? Math.round(box.height) : 0,
      hints: [...document.querySelectorAll('#cards .card.group .ghint')].map((e) => e.textContent.trim()),
    }
  })()`)
  check('把手在每张卡片最左侧', shape.count > 0 && shape.leftMost === true,
    `cards=${shape.count} last=${shape.lastChild}`)
  check('把手命中框 >= 18x20', shape.width >= 18 && shape.height >= 20,
    `${shape.width}x${shape.height}`)
  check('空分支文案说拖入而非点选',
    shape.hints.length > 0 && shape.hints.every((h) => h.includes('拖入指令') && !h.includes('点选后添加')),
    JSON.stringify(shape.hints))

  const drag = await cdp.evaluate(`(() => {
    const grip = document.querySelector('#cards .card .grip')
    if (!grip) return { payload: null }
    const dt = new DataTransfer()
    grip.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt }))
    const raw = dt.getData('application/x-ppx-node')
    return { payload: raw ? JSON.parse(raw) : null, nodeId: grip.closest('.card').dataset.nodeId }
  })()`)
  check('把手拖出的是 move 载荷',
    drag.payload?.mode === 'move' && drag.payload?.nodeId === drag.nodeId,
    JSON.stringify(drag.payload))
} catch (error) {
  check('探针本身跑通', false, error.message)
} finally {
  chrome.kill()
  try {
    const { rmSync } = await import('node:fs')
    rmSync(join(tmpdir(), `ppx-card-grip-${process.pid}`), { recursive: true, force: true })
  } catch {}
}

console.log(failures.length ? `FAILED ${failures.length}: ${failures.join(' | ')}` : 'CARD GRIP PROBE PASSED')
process.exit(failures.length ? 1 : 0)
