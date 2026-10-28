/**
 * WorkBuddy Studio 的窗口外壳：RPC、会话通道、查找条、状态栏。
 *
 * 分工刻意划得很清楚：
 * - `editor.js` 只管「文本怎么被编辑」，不认识 Python，也不认识 Agent；
 * - 本文件只管「编辑器怎么和外面说话」，不碰一行文本处理；
 * - Python 侧（api/api.py）只管磁盘与会话服务，不碰 UI 状态。
 * 三层任何一层单独换掉，另外两层都不该改。
 */

import { ppx } from 'ppx-js'
import './style.css'
import { Editor } from './editor.js'
import { languageOf } from './highlight.js'

const el = {
  fileName: document.querySelector('#fileName'),
  docPath: document.querySelector('#docPath'),
  dirtyDot: document.querySelector('#dirtyDot'),
  sessionPill: document.querySelector('#sessionPill'),
  sessionText: document.querySelector('#sessionText'),
  open: document.querySelector('#openBtn'),
  save: document.querySelector('#saveBtn'),
  find: document.querySelector('#findBtn'),
  help: document.querySelector('#helpBtn'),
  status: document.querySelector('#statusMsg'),
  stPos: document.querySelector('#stPos'),
  stSel: document.querySelector('#stSel'),
  stLang: document.querySelector('#stLang'),
  banner: document.querySelector('#banner'),
  bannerText: document.querySelector('#bannerText'),
  bannerAccept: document.querySelector('#bannerAccept'),
  bannerLater: document.querySelector('#bannerLater'),
  findBar: document.querySelector('#findBar'),
  findInput: document.querySelector('#findInput'),
  findCount: document.querySelector('#findCount'),
  findPrev: document.querySelector('#findPrev'),
  findNext: document.querySelector('#findNext'),
  findClose: document.querySelector('#findClose'),
  toasts: document.querySelector('#toasts'),
  openDialog: document.querySelector('#openDialog'),
  openPath: document.querySelector('#openPath'),
  openConfirm: document.querySelector('#openConfirm'),
  helpDialog: document.querySelector('#helpDialog'),
  helpClose: document.querySelector('#helpClose'),
}

// 关窗时 pywebview 会拆掉 JS 桥；还在飞的 RPC 回调会变成
// 「_returnValuesCallbacks.call.xxx is not a function」的红色堆栈。
// 所以一到卸载就停表、并且拒绝再发新请求。
// 只挂 pagehide 不够：WebView2 关窗时不一定给 pagehide，beforeunload 更早也更稳。
let closing = false
const timers = []
function halt() {
  closing = true
  for (const id of timers) clearInterval(id)
}
window.addEventListener('pagehide', halt)
window.addEventListener('beforeunload', halt)

const state = {
  path: null,
  sha: null,
  online: false,
  since: 0,
  seen: new Set(),
  pendingOpen: null,
  conflictText: null,
  findMode: 'find',
  findHits: [],
  findIndex: -1,
  validating: 0,
  lastSaveAt: 0,
}

const editor = new Editor({
  mount: document.querySelector('#editorHost'),
  onChange: ({ dirty }) => {
    el.stLang.textContent = editor.lang
    el.dirtyDot.hidden = !dirty
    el.save.disabled = !state.path
    if (!dirty) return
    scheduleValidate()
  },
  onCursor: ({ line, col, sel }) => {
    el.stPos.textContent = `行 ${line}，列 ${col}`
    el.stSel.hidden = sel <= 0
    if (sel > 0) el.stSel.textContent = `已选 ${sel} 字符`
  },
  onHint: (text) => setStatus(text, 'warn'),
})

// ------------------------------------------------------------------ RPC ----

async function call(method, params) {
  if (closing) return { ok: false, code: 'CLOSING', error: '窗口正在关闭' }
  try {
    return await ppx.call(method, params || {})
  } catch (error) {
    const code = error?.code || 'RPC_FAILED'
    const message = error?.message || String(error)
    setStatus(`${code}：${message}`, 'bad')
    return { ok: false, code, error: message }
  }
}

// --------------------------------------------------------------- 界面状态 ---

function setStatus(text, tone = '') {
  el.status.textContent = text
  el.status.className = `statusMsg${tone ? ` ${tone}` : ''}`
}

function toast(text, tone = '', ttl = 3200) {
  const node = document.createElement('div')
  node.className = `toast${tone ? ` ${tone}` : ''}`
  node.textContent = text
  el.toasts.append(node)
  setTimeout(() => {
    node.style.opacity = '0'
    node.style.transition = 'opacity 200ms ease'
    setTimeout(() => node.remove(), 220)
  }, ttl)
}

function setSession(online, port) {
  state.online = online
  el.sessionPill.className = `pill ${online ? 'online' : 'offline'}`
  el.sessionText.textContent = online ? `会话在线 · ${port}` : '会话离线'
}

function showBanner(text, onAccept, acceptLabel = '接受') {
  el.bannerText.textContent = text
  el.bannerAccept.textContent = acceptLabel
  el.banner.hidden = false
  state.bannerAccept = onAccept
}

function hideBanner() {
  el.banner.hidden = true
  state.bannerAccept = null
}

// ------------------------------------------------------------- 文件打开/保存 ---

function applyDocument(doc) {
  state.path = doc.path
  state.sha = doc.sha
  editor.setValue(doc.text, { path: doc.path, lang: doc.lang })
  const name = doc.path.split(/[\\/]/).pop()
  el.fileName.textContent = name
  el.docPath.textContent = doc.path
  el.docPath.title = doc.path
  el.stLang.textContent = doc.lang
  document.title = `${name} — WorkBuddy Studio`
  el.save.disabled = false
  validateNow()
  setStatus(`已打开 · ${doc.bytes} 字节 · ${doc.lines} 行`)
}

/** 优先用 PPX 自带的 system.openFileDialog（原生对话框、记住上次目录）；
 *  拿不到（老运行时 / 非 Windows）才退回手输路径。 */
async function pickWithSystemDialog() {
  try {
    const picked = await ppx.call('system.openFileDialog', {
      file_types: ['全部文件 (*.*)'],
      multiple: false,
    })
    const first = Array.isArray(picked) ? picked[0] : null
    if (first?.path) {
      await openFile(first.path)
    } else {
      setStatus('没有选择文件')
    }
  } catch {
    el.openPath.value = state.path || ''
    el.openDialog.showModal()
  }
}

async function openFile(path) {
  if (!path) return
  if (editor.dirty) {
    showBanner(`「${el.fileName.textContent}」有未保存的改动，打开新文件会丢弃它。`, () => {
      doOpen(path)
    }, '丢弃并打开')
    return
  }
  await doOpen(path)
}

async function doOpen(path) {
  hideBanner()
  setStatus(`正在打开 ${path} …`)
  const res = await call('studio.openFile', { path })
  if (!res?.ok) {
    setStatus(`打开失败 ${res?.code || ''}：${res?.error || '未知原因'}`, 'bad')
    toast(`打开失败：${res?.error || res?.code}`, 'bad')
    return
  }
  applyDocument(res)
  closeFind()
  editor.focus()
}

async function saveFile(force = false) {
  if (!state.path) {
    toast('还没有打开文件', 'warn')
    return
  }
  const text = editor.getValue()
  if (!force && text === editor.baseline && !state.conflictText) {
    setStatus('没有改动，无需保存')
    return
  }
  el.save.disabled = true
  setStatus('保存中…')
  const res = await call('studio.saveFile', {
    path: state.path,
    text,
    baseSha: force ? null : state.sha,
  })
  el.save.disabled = false
  if (res?.code === 'CONFLICT') {
    showBanner('文件在你打开之后被其他程序改过。覆盖会丢掉对方的改动。', () => saveFile(true), '仍然覆盖')
    setStatus('保存被拒：磁盘上的文件已变化', 'warn')
    return
  }
  if (!res?.ok) {
    setStatus(`保存失败 ${res?.code || ''}：${res?.error || ''}`, 'bad')
    toast(`保存失败：${res?.error || res?.code}`, 'bad', 5000)
    return
  }
  state.sha = res.sha
  state.conflictText = null
  editor.markSaved(text)
  const stamp = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  if (res.validation && !res.validation.ok) {
    setStatus(`已保存（${stamp}）但校验未通过：${res.validation.message}`, 'warn')
    editor.setErrorLine(res.validation.line || 0)
  } else {
    setStatus(`已保存 · ${stamp} · ${res.bytes} 字节`, 'ok')
    editor.setErrorLine(0)
  }
  hideBanner()
  toast(res.notified ? '已保存，Agent 已收到通知' : '已保存（会话离线，未通知 Agent）', res.notified ? 'ok' : 'warn')
}

// ---------------------------------------------------------------- 校验 ----

/** 只做「语法级」校验：位置算得准、零成本、每次敲键都能跑。
 *  语义级校验（schema / 模型）交给 Python 侧在保存时做，避免 RPC 风暴。 */
function localValidation(text, lang) {
  if (lang !== 'json') return { ok: true }
  if (!text.trim()) return { ok: true }
  try {
    JSON.parse(text)
    return { ok: true }
  } catch (error) {
    const message = String(error.message || error)
    const at = /position (\d+)/.exec(message)
    const line = at ? text.slice(0, Number(at[1])).split('\n').length : 0
    const col = at ? Number(at[1]) - text.lastIndexOf('\n', Number(at[1]) - 1) : 0
    return { ok: false, message, line, col }
  }
}

function scheduleValidate() {
  clearTimeout(state.validating)
  state.validating = setTimeout(validateNow, 320)
}

function validateNow() {
  const result = localValidation(editor.getValue(), editor.lang)
  if (result.ok) {
    editor.setErrorLine(0)
    if (!state.conflictText) setStatus('JSON 语法正常')
    return result
  }
  editor.setErrorLine(result.line || 0)
  setStatus(`第 ${result.line} 行第 ${result.col} 列：${result.message}`, 'bad')
  return result
}

// ---------------------------------------------------------------- 查找 ----

function openFind(mode = 'find') {
  state.findMode = mode
  el.findBar.hidden = false
  el.findInput.placeholder = mode === 'goto' ? '跳到行号…' : '查找内容'
  el.findInput.value = mode === 'goto' ? '' : editor.getValue().slice(editor.input.selectionStart, editor.input.selectionEnd).slice(0, 80)
  el.findInput.focus()
  el.findInput.select()
  updateFind()
}

function closeFind() {
  el.findBar.hidden = true
  el.findInput.value = ''
  state.findHits = []
  state.findIndex = -1
  editor.focus()
}

function updateFind() {
  if (state.findMode === 'goto') {
    const line = Number(el.findInput.value)
    el.findCount.textContent = Number.isFinite(line) && line > 0 ? `第 ${line} 行` : '—'
    return
  }
  const query = el.findInput.value
  state.findHits = editor.findAll(query)
  state.findIndex = state.findHits.length ? 0 : -1
  el.findCount.textContent = state.findHits.length ? `1/${state.findHits.length}` : '0/0'
  if (state.findIndex >= 0) jumpToHit(0)
}

function jumpToHit(index) {
  if (!state.findHits.length) return
  const total = state.findHits.length
  state.findIndex = ((index % total) + total) % total
  const hit = state.findHits[state.findIndex]
  // 不抢焦点：查找框要保留焦点，否则用户接着敲 Enter 会把光标丢回正文
  editor.selectRange(hit.start, hit.end, { focus: false })
  el.findCount.textContent = `${state.findIndex + 1}/${total}`
}

function stepFind(delta) {
  if (state.findMode === 'goto') {
    const line = Number(el.findInput.value)
    if (!Number.isFinite(line) || line <= 0) return
    editor.revealLine(line)
    setStatus(`已跳到第 ${line} 行`)
    closeFind()
    return
  }
  if (!state.findHits.length) updateFind()
  jumpToHit(state.findIndex + delta)
}

// -------------------------------------------------------------- 会话通道 ---

async function pollSession() {
  const res = await call('studio.pollSession', { since: state.since })
  if (!res) return
  setSession(Boolean(res.online), res.port || '')
  if (!res.online) return
  state.since = res.cursor || state.since
  for (const request of res.requests || []) {
    if (state.seen.has(request.id)) continue
    state.seen.add(request.id)
    handleRequest(request)
  }
}

function handleRequest(request) {
  if (request.kind === 'open') {
    if (editor.dirty && state.path !== request.path) {
      state.pendingOpen = request
      showBanner(`Agent 请求打开「${request.path}」，当前文件有未保存改动。`, () => {
        const pending = state.pendingOpen
        state.pendingOpen = null
        if (pending) doOpen(pending.path)
      }, '丢弃并打开')
      return
    }
    doOpen(request.path)
    if (request.reason) toast(`Agent：${request.reason}`, '', 4200)
    return
  }
  if (request.kind === 'message') {
    showBanner(`Agent：${request.text}`, () => {
      hideBanner()
      call('studio.ack', { id: request.id })
    }, '知道了')
  }
}

// ---------------------------------------------------------------- 事件 ----

el.open.addEventListener('click', () => pickWithSystemDialog())
el.save.addEventListener('click', () => saveFile())
el.find.addEventListener('click', () => openFind('find'))
el.help.addEventListener('click', () => el.helpDialog.showModal())
el.helpClose.addEventListener('click', () => el.helpDialog.close())

el.openConfirm.addEventListener('click', (event) => {
  event.preventDefault()
  const path = el.openPath.value.trim()
  el.openDialog.close()
  if (path) openFile(path)
})
el.openPath.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter') return
  event.preventDefault()
  const path = el.openPath.value.trim()
  el.openDialog.close()
  if (path) openFile(path)
})

el.bannerAccept.addEventListener('click', () => {
  const action = state.bannerAccept
  hideBanner()
  if (action) action()
})
el.bannerLater.addEventListener('click', hideBanner)

el.findInput.addEventListener('input', updateFind)
el.findInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault()
    stepFind(event.shiftKey ? -1 : 1)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    closeFind()
  }
})
el.findPrev.addEventListener('click', () => stepFind(-1))
el.findNext.addEventListener('click', () => stepFind(1))
el.findClose.addEventListener('click', closeFind)

window.addEventListener('keydown', (event) => {
  const mod = event.ctrlKey || event.metaKey
  if (mod && event.key.toLowerCase() === 's') {
    event.preventDefault()
    saveFile()
    return
  }
  if (mod && event.key.toLowerCase() === 'o') {
    event.preventDefault()
    pickWithSystemDialog()
    return
  }
  if (mod && event.key.toLowerCase() === 'f') {
    event.preventDefault()
    openFind('find')
    return
  }
  if (mod && event.key.toLowerCase() === 'g') {
    event.preventDefault()
    openFind('goto')
    return
  }
  if (event.key === 'F1') {
    event.preventDefault()
    el.helpDialog.showModal()
    return
  }
  if (event.key === 'Escape' && !el.findBar.hidden) {
    event.preventDefault()
    closeFind()
  }
})

// 拖拽打开：pywebview 里 File 对象不一定带 path，拿不到就明确说不可用，
// 而不是静默失败让用户以为程序坏了。
window.addEventListener('dragover', (event) => event.preventDefault())
window.addEventListener('drop', (event) => {
  event.preventDefault()
  const file = event.dataTransfer?.files?.[0]
  const path = file?.path || file?.webkitRelativePath || ''
  if (!path) {
    toast('这个窗口拿不到拖入文件的真实路径，请用 Ctrl+O 输入路径', 'warn', 4200)
    return
  }
  openFile(path)
})

// ---------------------------------------------------------------- 启动 ----

async function boot() {
  setStatus('正在连接 Studio 会话…')
  const info = await call('studio.bootstrap')
  if (info?.ok) {
    setSession(Boolean(info.session?.online), info.session?.port || '')
    state.since = info.session?.cursor || 0
    if (info.pending?.path) {
      await doOpen(info.pending.path)
    }
  }
  timers.push(setInterval(pollSession, 1400))
  timers.push(
    setInterval(() => {
      if (!state.path || !state.online) return
      call('studio.heartbeat', { path: state.path, dirty: editor.dirty })
    }, 5000),
  )
  editor.focus()
  el.stLang.textContent = editor.lang
}

boot()

// 供测试与调试使用的钩子：DOM 上的交互测试需要能读到编辑器内部状态。
// 它只暴露只读快照，不提供任何绕过编辑内核的写入入口。
window.__studio = {
  editor,
  state,
  snapshot: () => ({
    text: editor.getValue(),
    dirty: editor.dirty,
    lang: editor.lang,
    line: editor.input.selectionStart,
    selEnd: editor.input.selectionEnd,
    path: state.path,
    canUndo: editor.canUndo,
    canRedo: editor.canRedo,
    errorLine: editor.errorLine,
  }),
  languageOf,
}
