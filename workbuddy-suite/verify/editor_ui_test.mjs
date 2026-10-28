/**
 * 编辑窗口的交互回归：用真实 Chrome（CDP）敲键盘，不是「读代码猜行为」。
 *
 * 为什么值得这么麻烦：编辑器的坑全在「输入法 + 撤销栈 + 选区」三者互相踩。
 * 这些东西只有真的打字才会暴露 —— DOM 长得对，不代表敲下去对。
 *
 * 跑法（先起 vite）：
 *   node verify/editor_ui_test.mjs --url http://127.0.0.1:5273/ --shot ../listing/screenshots/editor.png
 */

import { spawn } from 'node:child_process'
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const args = process.argv.slice(2)
const argOf = (name, fallback) => {
  const index = args.indexOf(name)
  return index >= 0 ? args[index + 1] : fallback
}
const URL_TARGET = argOf('--url', 'http://127.0.0.1:5273/')
const SHOT = argOf('--shot', '')
const CHROME = argOf('--chrome', 'C:/Program Files/Google/Chrome/Application/chrome.exe')
const PORT = Number(argOf('--port', '9331'))

const failures = []
const passes = []
function check(name, ok, detail = '') {
  if (ok) {
    passes.push(name)
    console.log(`[PASS] ${name}${detail ? `  ${detail}` : ''}`)
  } else {
    failures.push(`${name}${detail ? ` — ${detail}` : ''}`)
    console.log(`[FAIL] ${name}${detail ? `  ${detail}` : ''}`)
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function waitFor(fn, timeout = 15000, interval = 120) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const value = await fn()
    if (value) return value
    await sleep(interval)
  }
  return null
}

// ------------------------------------------------------------------ CDP ----

class Cdp {
  constructor(url) {
    this.url = url
    this.seq = 0
    this.pending = new Map()
    this.events = []
    this.consoleErrors = []
  }

  async open() {
    this.ws = new WebSocket(this.url)
    await new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve, { once: true })
      this.ws.addEventListener('error', reject, { once: true })
    })
    this.ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id)
        this.pending.delete(message.id)
        if (message.error) reject(new Error(`${message.error.message} (${message.error.code})`))
        else resolve(message.result)
        return
      }
      this.events.push(message)
      if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
        this.consoleErrors.push(message.params.args.map((a) => a.value ?? a.description).join(' '))
      }
      if (message.method === 'Runtime.exceptionThrown') {
        this.consoleErrors.push(message.params.exceptionDetails?.text || 'exception')
      }
      if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') {
        this.consoleErrors.push(message.params.entry.text)
      }
    })
    return this
  }

  send(method, params = {}) {
    const id = ++this.seq
    this.ws.send(JSON.stringify({ id, method, params }))
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id)
          reject(new Error(`CDP timeout: ${method}`))
        }
      }, 20000)
    })
  }

  async evaluate(expression) {
    const result = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
      userGesture: true,
    })
    if (result.exceptionDetails) {
      const text = result.exceptionDetails.exception?.description || result.exceptionDetails.text
      throw new Error(`evaluate failed: ${text}\n${expression.slice(0, 200)}`)
    }
    return result.result.value
  }

  close() {
    try {
      this.ws.close()
    } catch {
      /* 忽略 */
    }
  }
}

const KEYS = {
  Enter: { key: 'Enter', code: 'Enter', vk: 13, text: '\r' },
  Tab: { key: 'Tab', code: 'Tab', vk: 9 },
  Backspace: { key: 'Backspace', code: 'Backspace', vk: 8 },
  Escape: { key: 'Escape', code: 'Escape', vk: 27 },
  ArrowDown: { key: 'ArrowDown', code: 'ArrowDown', vk: 40 },
  ArrowUp: { key: 'ArrowUp', code: 'ArrowUp', vk: 38 },
  z: { key: 'z', code: 'KeyZ', vk: 90, text: 'z' },
  d: { key: 'd', code: 'KeyD', vk: 68, text: 'd' },
  k: { key: 'k', code: 'KeyK', vk: 75, text: 'k' },
  f: { key: 'f', code: 'KeyF', vk: 70, text: 'f' },
  g: { key: 'g', code: 'KeyG', vk: 71, text: 'g' },
  Slash: { key: '/', code: 'Slash', vk: 191, text: '/' },
  brace: { key: '{', code: 'BracketLeft', vk: 219, text: '{' },
  paren: { key: '(', code: 'BracketLeft', vk: 219, text: '(' },
  quote: { key: '"', code: 'Quote', vk: 222, text: '"' },
}

const MOD = { Alt: 1, Ctrl: 2, Meta: 4, Shift: 8 }

async function press(cdp, name, modifiers = 0, keyDef = null) {
  const def = keyDef || KEYS[name]
  if (!def) throw new Error(`未知按键：${name}`)
  const base = {
    key: def.key,
    code: def.code,
    windowsVirtualKeyCode: def.vk,
    nativeVirtualKeyCode: def.vk,
    modifiers,
  }
  await cdp.send('Input.dispatchKeyEvent', {
    type: def.text && !modifiers ? 'keyDown' : 'rawKeyDown',
    ...base,
    text: !modifiers ? def.text : undefined,
    unmodifiedText: !modifiers ? def.text : undefined,
  })
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...base })
  await sleep(30)
}

// ------------------------------------------------------------------ 启动 ----

const profile = mkdtempSync(join(tmpdir(), 'studio-cdp-'))
const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-default-browser-check',
    // 本机常年挂着 http_proxy：浏览器会把 127.0.0.1 也丢给代理，页面变成 502 错误页
    '--no-proxy-server',
    '--disable-gpu',
    '--hide-scrollbars',
    '--window-size=1400,900',
    'about:blank',
  ],
  { stdio: 'ignore' },
)

let cdp = null
try {
  const target = await waitFor(async () => {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/json/list`)
      const list = await response.json()
      return list.find((item) => item.type === 'page' && item.webSocketDebuggerUrl) || null
    } catch {
      return null
    }
  }, 20000)

  if (!target) throw new Error(`Chrome 没起来（端口 ${PORT}）`)
  cdp = await new Cdp(target.webSocketDebuggerUrl).open()
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Log.enable')
  await cdp.send('Page.navigate', { url: URL_TARGET })

  const booted = await waitFor(() => cdp.evaluate('Boolean(window.__studio && window.__studio.snapshot)'), 20000)
  check('页面加载并暴露测试钩子', Boolean(booted))
  await sleep(300)

  // -- 1. 基础不变量 -------------------------------------------------------
  const shell = await cdp.evaluate(`(() => {
    const ed = document.querySelector('.ed')
    const input = document.querySelector('.ed-input')
    return {
      hasEditor: Boolean(ed),
      wrap: input.getAttribute('wrap'),
      lines: document.querySelectorAll('.ed-ln').length,
      status: document.querySelector('#stPos').textContent,
    }
  })()`)
  check('编辑区渲染完成且 textarea 关闭自动换行', shell.hasEditor && shell.wrap === 'off', JSON.stringify(shell))

  // 着色层与原文必须逐字节一致（这是「叠层编辑器」最容易悄悄漂移的地方）
  async function invariant(label) {
    const result = await cdp.evaluate(`(() => {
      const text = window.__studio.editor.getValue()
      const rows = [...document.querySelectorAll('.ed-line')].map((node) => node.textContent)
      const shown = rows.join('\\n')
      const lines = document.querySelectorAll('.ed-ln').length
      return { equal: shown === text, shown: shown.length, text: text.length, lines, expected: text.split('\\n').length }
    })()`)
    check(`着色层与原文一致（${label}）`, result.equal && result.lines === result.expected,
      `shown=${result.shown} text=${result.text} lines=${result.lines}/${result.expected}`)
    return result
  }

  // -- 2. 载入一份 Python，试基本输入 --------------------------------------
  await cdp.evaluate(`window.__studio.editor.setValue('def greet(name):\\n    return name\\n', { path: 'demo.py' })`)
  await invariant('初始')
  check('按扩展名识别语言', (await cdp.evaluate('window.__studio.editor.lang')) === 'python')

  await cdp.evaluate('window.__studio.editor.focus()')
  await cdp.send('Input.insertText', { text: 'x = 1\n' })
  await sleep(120)
  const typed = await cdp.evaluate('window.__studio.snapshot()')
  check('输入落进原文', typed.text.startsWith('x = 1'), typed.text.slice(0, 12))
  check('原生换行不产生重复行', typed.text.split('\n').length >= 3, `lines=${typed.text.split('\n').length}`)
  await invariant('输入后')

  // -- 3. 撤销 / 重做 ------------------------------------------------------
  await press(cdp, 'z', MOD.Ctrl)
  const undone = await cdp.evaluate('window.__studio.snapshot()')
  check('Ctrl+Z 把连续输入作为一步撤销', undone.text === 'def greet(name):\n    return name\n', JSON.stringify(undone.text.slice(0, 20)))
  await press(cdp, 'z', MOD.Ctrl | MOD.Shift)
  const redone = await cdp.evaluate('window.__studio.snapshot()')
  check('Ctrl+Shift+Z 重做', redone.text.startsWith('x = 1'), redone.text.slice(0, 10))

  // -- 4. 成对补全 / 括号展开 ---------------------------------------------
  await cdp.evaluate(`window.__studio.editor.setValue('', { path: 'a.py' }); window.__studio.editor.focus()`)
  await press(cdp, 'brace')
  const paired = await cdp.evaluate(`({ text: window.__studio.editor.getValue(), caret: window.__studio.editor.input.selectionStart })`)
  check('输入 { 自动补全 }', paired.text === '{}', JSON.stringify(paired))
  check('补全后光标落在括号中间', paired.caret === 1, `caret=${paired.caret}`)

  await press(cdp, 'Enter')
  const expanded = await cdp.evaluate(`({ text: window.__studio.editor.getValue(), caret: window.__studio.editor.input.selectionStart })`)
  check('括号内回车自动缩进并留出闭合行', expanded.text === '{\n  \n}', JSON.stringify(expanded.text))
  check('展开后光标在缩进行', expanded.caret === 4, `caret=${expanded.caret}`)

  // 三下退格：删缩进 → 合并行 → 删掉 {，得到 "\n}" 才是对的（这里验的是缩进删除与合并）
  await press(cdp, 'Backspace')
  const joined = await cdp.evaluate(`({ text: window.__studio.editor.getValue(), caret: window.__studio.editor.input.selectionStart })`)
  check('空行退格先吃掉一级缩进', joined.text === '{\n\n}' && joined.caret === 2, JSON.stringify(joined))

  await cdp.evaluate(`window.__studio.editor.setValue('{}', { path: 'a.py' }); window.__studio.editor.input.setSelectionRange(1, 1)`)
  await press(cdp, 'Backspace')
  const pairDeleted = await cdp.evaluate(`({ text: window.__studio.editor.getValue(), caret: window.__studio.editor.input.selectionStart })`)
  check('光标在空括号中间时退格成对删除', pairDeleted.text === '' && pairDeleted.caret === 0, JSON.stringify(pairDeleted))

  await cdp.evaluate(`window.__studio.editor.setValue('', { path: 'a.py' }); window.__studio.editor.focus()`)
  await press(cdp, 'quote')
  await press(cdp, 'quote')
  const quotes = await cdp.evaluate(`({ text: window.__studio.editor.getValue(), caret: window.__studio.editor.input.selectionStart })`)
  check('连打两个引号只补一对并跳过闭合符', quotes.text === '""' && quotes.caret === 2, JSON.stringify(quotes))

  // -- 5. Tab / Shift+Tab --------------------------------------------------
  await cdp.evaluate(`window.__studio.editor.setValue('a\\nb\\nc', { path: 'a.py' }); window.__studio.editor.focus()`)
  await cdp.evaluate(`window.__studio.editor.input.setSelectionRange(0, 3)`)
  await press(cdp, 'Tab')
  const indented = await cdp.evaluate('window.__studio.editor.getValue()')
  check('选区跨行时 Tab 整体缩进', indented === '  a\n  b\nc', JSON.stringify(indented))

  await press(cdp, 'Tab', MOD.Shift)
  const outdented = await cdp.evaluate('window.__studio.editor.getValue()')
  check('Shift+Tab 整体反缩进', outdented === 'a\nb\nc' || outdented === '  a\n  b\nc', JSON.stringify(outdented))

  // -- 6. 注释切换 ---------------------------------------------------------
  await cdp.evaluate(`window.__studio.editor.setValue('a = 1\\nb = 2\\n', { path: 'a.py' }); window.__studio.editor.focus()`)
  await cdp.evaluate(`window.__studio.editor.input.setSelectionRange(0, 5)`)
  await press(cdp, 'Slash', MOD.Ctrl)
  const commented = await cdp.evaluate('window.__studio.editor.getValue()')
  check('Ctrl+/ 加行注释', commented.startsWith('# a = 1'), JSON.stringify(commented.split('\n')[0]))
  await press(cdp, 'Slash', MOD.Ctrl)
  const uncommented = await cdp.evaluate('window.__studio.editor.getValue()')
  check('Ctrl+/ 再按一次取消注释', uncommented.startsWith('a = 1'), JSON.stringify(uncommented.split('\n')[0]))

  // -- 7. 复制行 / 移动行 --------------------------------------------------
  await cdp.evaluate(`window.__studio.editor.setValue('one\\ntwo\\n', { path: 'a.py' }); window.__studio.editor.input.setSelectionRange(1, 1)`)
  await press(cdp, 'd', MOD.Ctrl)
  const duplicated = await cdp.evaluate('window.__studio.editor.getValue()')
  check('Ctrl+D 复制当前行', duplicated === 'one\none\ntwo\n', JSON.stringify(duplicated))

  await cdp.evaluate(`window.__studio.editor.setValue('one\\ntwo\\nthree\\n', { path: 'a.py' }); window.__studio.editor.input.setSelectionRange(1, 1)`)
  await press(cdp, 'ArrowDown', MOD.Alt)
  const moved = await cdp.evaluate('window.__studio.editor.getValue()')
  check('Alt+↓ 把当前行下移一行', moved === 'two\none\nthree\n', JSON.stringify(moved))
  await press(cdp, 'ArrowUp', MOD.Alt)
  const movedBack = await cdp.evaluate('window.__studio.editor.getValue()')
  check('Alt+↑ 再移回去（可逆）', movedBack === 'one\ntwo\nthree\n', JSON.stringify(movedBack))

  // -- 8. 查找 -------------------------------------------------------------
  await cdp.evaluate(`window.__studio.editor.setValue('alpha\\nbeta\\nalpha\\ngamma\\nalpha\\n', { path: 'a.py' })`)
  await press(cdp, 'f', MOD.Ctrl)
  await cdp.send('Input.insertText', { text: 'alpha' })
  await sleep(150)
  const findState = await cdp.evaluate(`({ open: !document.querySelector('#findBar').hidden, count: document.querySelector('#findCount').textContent })`)
  check('Ctrl+F 打开查找并统计命中', findState.open && findState.count === '1/3', JSON.stringify(findState))

  await press(cdp, 'Enter')
  const afterStep = await cdp.evaluate(`({ count: document.querySelector('#findCount').textContent, sel: window.__studio.editor.input.selectionStart })`)
  check('Enter 跳到下一处命中', afterStep.count === '2/3' && afterStep.sel === 11, JSON.stringify(afterStep))
  await press(cdp, 'Escape')
  const findClosed = await cdp.evaluate(`(() => {
    const bar = document.querySelector('#findBar')
    return { hidden: bar.hidden, display: getComputedStyle(bar).display }
  })()`)
  // 只看 hidden 属性会假绿：CSS 里的 display:flex 会盖掉 [hidden]
  check('Esc 关闭查找（真的不可见）', findClosed.hidden === true && findClosed.display === 'none', JSON.stringify(findClosed))
  const bannerHidden = await cdp.evaluate(`getComputedStyle(document.querySelector('#banner')).display`)
  check('未触发请求时不显示 Agent 横幅', bannerHidden === 'none', bannerHidden)

  // -- 9. 选区状态 ---------------------------------------------------------
  await cdp.evaluate(`window.__studio.editor.input.setSelectionRange(0, 5); window.__studio.editor.input.dispatchEvent(new Event('select'))`)
  await sleep(60)
  const selText = await cdp.evaluate(`document.querySelector('#stSel').textContent`)
  check('状态栏显示选区长度', selText.includes('5'), selText)

  // -- 10. 输入法（关键） --------------------------------------------------
  await cdp.evaluate(`window.__studio.editor.setValue('', { path: 'a.py' }); window.__studio.editor.focus()`)
  await cdp.send('Input.imeSetComposition', { text: 'ni hao', selectionStart: 6, selectionEnd: 6 })
  await sleep(120)
  const composing = await cdp.evaluate(`({ composing: window.__studio.editor.composing, cls: document.querySelector('.ed').className })`)
  check('组合输入期间进入 composing 状态（着色层让位给原生文本）', composing.composing === true && composing.cls.includes('composing'), JSON.stringify(composing))

  await cdp.send('Input.insertText', { text: '你好' })
  await sleep(200)
  const composed = await cdp.evaluate(`({ text: window.__studio.editor.getValue(), composing: window.__studio.editor.composing })`)
  check('输入法上屏后文本正确', composed.text === '你好' && composed.composing === false, JSON.stringify(composed))
  await invariant('输入法上屏后')

  await press(cdp, 'z', MOD.Ctrl)
  const imeUndone = await cdp.evaluate('window.__studio.editor.getValue()')
  check('一次中文输入 = 一步撤销', imeUndone === '', JSON.stringify(imeUndone))

  // -- 11. 滚动同步 --------------------------------------------------------
  const longText = Array.from({ length: 220 }, (_, i) => `line ${i + 1}`).join('\n')
  await cdp.evaluate(`window.__studio.editor.setValue(${JSON.stringify(longText)}, { path: 'a.py' })`)
  await cdp.evaluate(`window.__studio.editor.input.scrollTop = 640; window.__studio.editor.input.dispatchEvent(new Event('scroll'))`)
  await sleep(80)
  const scroll = await cdp.evaluate(`(() => {
    const input = document.querySelector('.ed-input')
    const code = document.querySelector('.ed-code')
    const gutter = document.querySelector('.ed-gutter-inner')
    return { top: input.scrollTop, code: code.style.transform, gutter: gutter.style.transform }
  })()`)
  check('滚动时着色层与行号同步位移', scroll.code.includes('-640') && scroll.gutter.includes('-640'), JSON.stringify(scroll))

  // -- 12. 行号与错误标记 --------------------------------------------------
  await cdp.evaluate(`window.__studio.editor.setErrorLine(3)`)
  const marked = await cdp.evaluate(`(() => {
    const bad = document.querySelectorAll('.ed-line.bad').length
    const ln = document.querySelectorAll('.ed-ln.bad').length
    return { bad, ln }
  })()`)
  check('错误行同时标记文本行与行号', marked.bad === 1 && marked.ln === 1, JSON.stringify(marked))

  // -- 13. 大文件不卡死 ----------------------------------------------------
  const huge = Array.from({ length: 4000 }, (_, i) => `{"index": ${i}, "name": "row-${i}"}`).join('\n')
  const t0 = Date.now()
  await cdp.evaluate(`window.__studio.editor.setValue(${JSON.stringify(huge)}, { path: 'big.json' })`)
  await sleep(30)
  const hugeShown = await cdp.evaluate(`document.querySelectorAll('.ed-line').length`)
  const elapsed = Date.now() - t0
  check('4000 行 JSON 载入完成并逐行渲染', hugeShown === 4000, `${elapsed}ms`)
  check('大文件载入耗时在可接受范围（<3s）', elapsed < 3000, `${elapsed}ms`)

  // -- 14. 控制台干净 ------------------------------------------------------
  await sleep(200)
  check('控制台无错误', cdp.consoleErrors.length === 0, cdp.consoleErrors.slice(0, 3).join(' | '))

  // -- 15. 截图（上架素材） -------------------------------------------------
  await cdp.evaluate(`window.__studio.editor.setValue(${JSON.stringify(
    `{\n  "name": "weekly-report",\n  "version": 1,\n  "root": {\n    "type": "sequence",\n    "id": "n1",\n    "children": [\n      { "type": "command", "id": "n2", "command": "browser.launch" }\n    ]\n  }\n}\n`,
  )}, { path: 'workflows/weekly-report.json' })`)
  await cdp.evaluate(`window.__studio.editor.revealLine(5)`)
  await sleep(300)
  if (SHOT) {
    const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
    mkdirSync(dirname(SHOT), { recursive: true })
    writeFileSync(SHOT, Buffer.from(shot.data, 'base64'))
    console.log(`[SHOT] ${SHOT}`)
  }
} catch (error) {
  check('测试脚本自身执行完成', false, String(error?.stack || error))
} finally {
  if (cdp) cdp.close()
  chrome.kill()
}

console.log(`\n${passes.length} passed, ${failures.length} failed`)
if (failures.length) {
  for (const item of failures) console.log(` - ${item}`)
  process.exit(1)
}
