// CDP 驱动：无第三方依赖（Node 22 自带 fetch + WebSocket）。
// 用途：把真实 main.js 在真实样式下渲染成图，并取回布局度量值。
//
// 用法：node probe.mjs <cases.json>
// cases.json: [{ name, width, height, flow, clickFirst, eval }]

import { writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawn } from 'node:child_process'

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PORT = 9333
const BASE = 'http://localhost:5199/_layout/probe.html'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

class CDP {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    this.events = []
    ws.addEventListener('message', (e) => {
      const msg = JSON.parse(e.data)
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id)
        this.pending.delete(msg.id)
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
      } else if (msg.method) {
        this.events.push(msg)
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

  async evaluate(expression, { awaitPromise = true } = {}) {
    const r = await this.send('Runtime.evaluate', {
      expression,
      awaitPromise,
      returnByValue: true,
      userGesture: true,
    })
    if (r.exceptionDetails) {
      throw new Error(`eval failed: ${JSON.stringify(r.exceptionDetails).slice(0, 400)}`)
    }
    return r.result.value
  }

  async waitFor(expression, timeout = 8000) {
    const t0 = Date.now()
    while (Date.now() - t0 < timeout) {
      try {
        if (await this.evaluate(expression, { awaitPromise: false })) return true
      } catch {}
      await sleep(120)
    }
    throw new Error(`waitFor timeout: ${expression}`)
  }
}

async function connect() {
  for (let i = 0; i < 40; i++) {
    try {
      const list = await fetch(`http://127.0.0.1:${PORT}/json/list`).then((r) => r.json())
      const page = list.find((t) => t.type === 'page')
      if (page) {
        const ws = new WebSocket(page.webSocketDebuggerUrl)
        await new Promise((res, rej) => {
          ws.addEventListener('open', res, { once: true })
          ws.addEventListener('error', rej, { once: true })
        })
        return new CDP(ws)
      }
    } catch {}
    await sleep(250)
  }
  throw new Error('CDP 连不上')
}

const cases = JSON.parse(process.argv[2] ? await import('node:fs').then((fs) => fs.readFileSync(process.argv[2], 'utf8')) : '[]')

const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    '--disable-gpu',
    '--hide-scrollbars=false',
    `--remote-debugging-port=${PORT}`,
    // 每次跑用独立 profile，且放在系统临时目录：上一轮的 chrome 还没退干净时，
    // 共用 profile 会让新实例直接退出；放仓库里又会留下一堆删不掉的目录。
    '--user-data-dir=' + join(tmpdir(), 'ppx-layout-chrome-' + process.pid),
    '--no-first-run',
    '--no-default-browser-check',
    'about:blank',
  ],
  { stdio: ['ignore', 'pipe', 'pipe'] },
)

let chromeErr = ''
chrome.stdout.on('data', () => {})
chrome.stderr.on('data', (d) => { chromeErr += d.toString() })
chrome.on('exit', (code) => { chromeErr += `\n[chrome exit ${code}]` })

const results = []
try {
  const cdp = await connect().catch((e) => {
    throw new Error(`${e.message}\n--- chrome stderr ---\n${chromeErr.slice(-1200)}`)
  })
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')

  for (const c of cases) {
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: c.width,
      height: c.height,
      deviceScaleFactor: 1,
      mobile: false,
    })
    const url = `${BASE}?flow=${encodeURIComponent(c.flow || '.harness/demo/ppx_probe/_sandbox/struct.json')}&click=${c.clickFirst === false ? '0' : '1'}`
    // 连续导航时偶发「页面还停在上一份文档」→ 卡在白等。重试一次比调大超时可靠。
    for (let attempt = 1; attempt <= 2; attempt++) {
      await cdp.send('Page.navigate', { url })
      try {
        await cdp.waitFor('window.__MOCK__ && window.__MOCK__.ready === true', 12000)
        if (!c.skipCardWait) await cdp.waitFor("document.querySelectorAll('#cards .card').length > 0", 8000)
        break
      } catch (e) {
        if (attempt === 2) throw e
        console.log(`  [retry] ${c.name} 首次渲染未就绪`)
        await sleep(600)
      }
    }
    if (c.clickLabel) {
      await cdp.evaluate(
        `[...document.querySelectorAll('#cards .card')].find((e) => e.textContent.includes(${JSON.stringify(c.clickLabel)}))?.click()`,
      )
      await sleep(400)
    }
    const extra = []
    for (const expr of c.steps || []) {
      extra.push(await cdp.evaluate(expr))
    }
    await sleep(c.settle ?? 500)
    const report = await cdp.evaluate(
      `import('/_layout/measure.js').then((m) => m.measure())`,
    )
    const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
    // 文件名以 _ 开头：仓库的 .gitignore 用 `_*.png` 忽略手测截图
    writeFileSync(`_layout_shots/_${c.name}.png`, Buffer.from(shot.data, 'base64'))
    results.push({ ...c, extra, report })
    console.log(`[shot] ${c.name} ${c.width}x${c.height} cards=${report.cardCount}`)
  }
} finally {
  writeFileSync('_layout_shots/report.json', JSON.stringify(results, null, 2))
  chrome.kill()
  try {
    rmSync(join(tmpdir(), `ppx-layout-chrome-${process.pid}`), { recursive: true, force: true })
  } catch {}
}
