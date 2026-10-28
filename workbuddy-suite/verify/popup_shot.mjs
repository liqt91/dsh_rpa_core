/**
 * 给扩展弹窗拍一张上架用截图。
 *
 * 弹窗只能在真实扩展环境里跑，所以这里把 `chrome.*` 换成一组「有返回值」的替身，
 * 再用 Chrome 按 328px 宽度渲染 —— 跟用户在工具栏点开时看到的布局一致。
 * 替身只影响数据来源，不影响 popup.css / popup.html 的真实排版。
 *
 * 跑法：node verify/popup_shot.mjs --out ../listing/screenshots/extension-popup.png
 */

import { spawn } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SUITE = resolve(HERE, '..')
const args = process.argv.slice(2)
const outIndex = args.indexOf('--out')
const OUT = resolve(outIndex >= 0 ? args[outIndex + 1] : join(SUITE, 'listing/screenshots/extension-popup.png'))
const PORT = 9336
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const profile = mkdtempSync(join(tmpdir(), 'wb-popup-'))
const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-proxy-server',
    '--disable-gpu',
    '--hide-scrollbars',
    '--window-size=420,720',
    'about:blank',
  ],
  { stdio: 'ignore' },
)

let ws
try {
  let target = null
  for (let i = 0; i < 80 && !target; i += 1) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
      target = list.find((item) => item.type === 'page')
    } catch {
      await sleep(200)
    }
  }
  if (!target) throw new Error('Chrome 没起来')

  ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true })
    ws.addEventListener('error', rej, { once: true })
  })

  let seq = 0
  const pending = new Map()
  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data)
    if (message.id && pending.has(message.id)) {
      const { resolve: res, reject } = pending.get(message.id)
      pending.delete(message.id)
      if (message.error) reject(new Error(message.error.message))
      else res(message.result)
    }
  })
  const send = (method, params = {}) => {
    const id = ++seq
    ws.send(JSON.stringify({ id, method, params }))
    return new Promise((res, rej) => pending.set(id, { resolve: res, reject: rej }))
  }

  await send('Page.enable')
  await send('Runtime.enable')

  // 扩展宿主替身：必须在 popup.js 之前装好
  await send('Page.addScriptToEvaluateOnNewDocument', {
    source: `(() => {
      const now = Date.now()
      const store = {
        port: 7788,
        recent: [
          { selector: '#username', tag: 'input', text: '用户名', at: now - 60_000, ok: true },
          { selector: 'button[data-testid="submit-login"]', tag: 'button', text: '登录', at: now - 240_000, ok: true },
          { selector: 'div:nth-of-type(4) > span', tag: 'span', text: '', at: now - 600_000, ok: false },
        ],
      }
      window.chrome = {
        runtime: {
          sendMessage: (message, callback) => {
            const reply = { ok: true, online: true, waiting: true, port: 7788, lastError: '' }
            if (callback) setTimeout(() => callback(reply), 5)
          },
          onMessage: { addListener: () => {} },
          lastError: null,
        },
        storage: {
          local: {
            get: (defaults) => {
              if (typeof defaults === 'string') return Promise.resolve({ [defaults]: store[defaults] })
              const out = {}
              for (const key of Object.keys(defaults || {})) out[key] = store[key] ?? defaults[key]
              return Promise.resolve(out)
            },
            set: (values) => { Object.assign(store, values); return Promise.resolve() },
          },
          onChanged: { addListener: () => {} },
        },
      }
      navigator.clipboard.writeText = () => Promise.resolve()
    })()`,
  })

  await send('Emulation.setDeviceMetricsOverride', {
    width: 328,
    height: 640,
    deviceScaleFactor: 2,
    mobile: false,
  })
  await send('Page.navigate', { url: pathToFileURL(join(SUITE, 'extension', 'popup.html')).href })
  await sleep(1200)

  // 弹窗高度跟着内容走：留一大片空白会让截图看起来像半成品。
  // 注意别用 cssContentSize —— 视口撑着的时候它等于视口高度，量不出真实内容。
  const measured = await send('Runtime.evaluate', {
    expression: 'Math.ceil(document.body.getBoundingClientRect().height) + 2',
    returnByValue: true,
  })
  const height = Math.min(720, measured.result.value)
  await send('Emulation.setDeviceMetricsOverride', { width: 328, height, deviceScaleFactor: 2, mobile: false })
  await sleep(300)
  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
  writeFileSync(OUT, Buffer.from(shot.data, 'base64'))
  console.log(`[SHOT] ${OUT} (${328}x${height})`)
} catch (error) {
  console.error(String(error?.stack || error))
  process.exitCode = 1
} finally {
  if (ws) ws.close()
  chrome.kill()
}
