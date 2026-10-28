/**
 * WorkBuddy Studio 的编辑内核。
 *
 * 为什么是「透明 textarea + 着色层」而不是自己实现光标：
 * 浏览器原生的 textarea 已经免费给了我们最难做对的那几件事 —— 光标定位、双向选区、
 * 双击选词、拖选、**输入法（IME）候选窗跟随**、滚轮/触控板平滑滚动、无障碍朗读。
 * 自己画光标看着酷，但在中文输入法下一次组合输入就会露馅（候选框位置、退格吞字）。
 *
 * 于是分工是：textarea 负责「输入与光标」，它是一个只保留 caret 的透明层；
 * 下面一层 <pre> 负责「看得见的文本与颜色」，两者共用同一套字体度量，
 * 滚动时同步位移。撤销/重做由本类自己维护（原生 undo 栈在程序化改值后会失效），
 * 这样输入法、自动缩进、成对补全、多行缩进都能被同一套历史记录覆盖。
 */

import { commentSyntaxOf, highlightLines, languageOf } from './highlight.js'

const INDENT = '  '
const HISTORY_LIMIT = 500
const COALESCE_MS = 700
const PAIRS = { '(': ')', '[': ']', '{': '}', '"': '"', "'": "'", '`': '`' }
const CLOSERS = new Set(Object.values(PAIRS))

export class Editor {
  constructor({ mount, onChange, onCursor, onHint }) {
    this.mount = mount
    this.onChange = onChange || (() => {})
    this.onCursor = onCursor || (() => {})
    this.onHint = onHint || (() => {})

    this.text = ''
    this.baseline = ''
    this._lineIndex = null
    this.lang = 'plain'
    this.path = null
    this.errorLine = 0
    this.composing = false
    this.history = []
    this.hIndex = -1
    this.lastEditAt = 0
    this.lastEditType = ''
    this.restoring = false
    this.frame = 0

    this._build()
    this._bind()
    this.history = [{ text: '', s: 0, e: 0, top: 0 }]
    this.hIndex = 0
    this._render()
  }

  // ---------------------------------------------------------------- DOM ----

  _build() {
    const wrap = document.createElement('div')
    wrap.className = 'ed'
    wrap.innerHTML = `
      <div class="ed-gutter" aria-hidden="true"><div class="ed-gutter-inner"></div></div>
      <div class="ed-main">
        <div class="ed-layer" aria-hidden="true">
          <div class="ed-activeline"></div>
          <pre class="ed-code"><code></code></pre>
        </div>
        <textarea class="ed-input" wrap="off" spellcheck="false" autocorrect="off"
          autocomplete="off" autocapitalize="off" aria-label="编辑区"></textarea>
      </div>`
    this.mount.append(wrap)
    this.wrap = wrap
    this.gutter = wrap.querySelector('.ed-gutter')
    this.gutterInner = wrap.querySelector('.ed-gutter-inner')
    this.layer = wrap.querySelector('.ed-layer')
    this.code = wrap.querySelector('.ed-code')
    this.input = wrap.querySelector('.ed-input')
    this.activeLine = wrap.querySelector('.ed-activeline')
  }

  _bind() {
    const input = this.input

    input.addEventListener('input', () => {
      if (this.composing) {
        // 组合输入期间只同步原文，不动着色层、不记历史：候选还在选，
        // 现在记一步会把「一次中文输入」拆成好几步撤销。
        this._setText(input.value)
        this._syncCursor()
        return
      }
      this._setText(input.value)
      this._record('type')
      this._afterEdit()
    })

    input.addEventListener('keydown', (event) => this._onKeyDown(event))
    input.addEventListener('select', () => this._syncCursor())
    input.addEventListener('click', () => this._syncCursor())
    input.addEventListener('keyup', () => this._syncCursor())
    input.addEventListener('scroll', () => this._syncScroll(), { passive: true })

    input.addEventListener('compositionstart', () => {
      this.composing = true
      this.wrap.classList.add('composing')
      this._snapshotBeforeComposition = { text: this.text, s: input.selectionStart, e: input.selectionEnd }
    })
    input.addEventListener('compositionend', () => {
      this.composing = false
      this.wrap.classList.remove('composing')
      const before = this._snapshotBeforeComposition
      this._setText(input.value)
      if (before && before.text !== this.text) {
        // 一次组合输入 = 一步撤销：把组合前状态压栈，再把当前状态作为新的一步。
        this.history.splice(this.hIndex + 1)
        if (before.text !== this.history[this.hIndex]?.text) {
          this.history.push({ text: before.text, s: before.s, e: before.e, top: 0 })
        }
        this.hIndex = this.history.length - 1
        this._record('ime')
      }
      this._afterEdit()
    })

    input.addEventListener('blur', () => this.wrap.classList.remove('focused'))
    input.addEventListener('focus', () => {
      this.wrap.classList.add('focused')
      this._syncCursor()
    })
  }

  // ------------------------------------------------------------- 公开 API ---

  /** 载入一份文本；`lang` 不传就按路径推。resetHistory=false 时保留撤销历史。 */
  setValue(text, { path = null, lang = null, baseline = null } = {}) {
    this.path = path
    this.lang = lang || (path ? languageOf(path) : 'plain')
    this._setText(text)
    this.baseline = baseline == null ? text : baseline
    this.input.value = text
    this.errorLine = 0
    this.history = [{ text, s: 0, e: 0, top: 0 }]
    this.hIndex = 0
    this.lastEditType = ''
    this.input.setSelectionRange(0, 0)
    this.input.scrollTop = 0
    this.input.scrollLeft = 0
    this._render()
    this._syncScroll()
    this._syncCursor()
    this._emitChange()
    this.focus()
  }

  getValue() {
    return this.text
  }

  get dirty() {
    return this.text !== this.baseline
  }

  markSaved(text) {
    this.baseline = text == null ? this.text : text
    this._emitChange()
  }

  setErrorLine(line) {
    this.errorLine = Math.max(0, Number(line) || 0)
    this._render()
  }

  setLanguage(lang) {
    this.lang = lang
    this._render()
  }

  focus() {
    this.input.focus({ preventScroll: true })
  }

  /** 把光标放到某一行（1 基）并对齐视口。 */
  revealLine(line) {
    const target = Math.max(1, Math.min(line, this.lineCount))
    const start = this._lineStart(target)
    const end = this.text.indexOf('\n', start)
    this.input.focus({ preventScroll: true })
    this.input.setSelectionRange(start, end < 0 ? this.text.length : end)
    this._ensureVisible(target - 1)
    this._syncCursor()
  }

  findAll(query) {
    if (!query) return []
    const hits = []
    const haystack = this.text
    let from = 0
    while (from <= haystack.length) {
      const at = haystack.indexOf(query, from)
      if (at < 0) break
      hits.push({ start: at, end: at + query.length })
      from = at + Math.max(1, query.length)
      if (hits.length >= 5000) break
    }
    return hits
  }

  selectRange(start, end, { focus = true } = {}) {
    if (focus) this.input.focus({ preventScroll: true })
    this.input.setSelectionRange(start, end)
    this.input.scrollTop = this.input.scrollTop // 触发一次 layout，避免某些引擎延后滚动
    const line = this._lineOf(start)
    this._ensureVisible(line)
    this._syncCursor()
  }

  undo() {
    if (this.composing || this.hIndex <= 0) return false
    this.hIndex -= 1
    this._restore(this.history[this.hIndex])
    return true
  }

  redo() {
    if (this.composing || this.hIndex >= this.history.length - 1) return false
    this.hIndex += 1
    this._restore(this.history[this.hIndex])
    return true
  }

  get canUndo() {
    return this.hIndex > 0
  }

  get canRedo() {
    return this.hIndex < this.history.length - 1
  }

  get lineCount() {
    return this.text.split('\n').length
  }

  // --------------------------------------------------------------- 渲染 ----

  _render() {
    const lines = highlightLines(this.text, this.lang)
    const html = lines
      .map((line, index) => {
        const bad = this.errorLine && index === this.errorLine - 1
        return `<div class="ed-line${bad ? ' bad' : ''}">${line}</div>`
      })
      .join('')
    this.code.innerHTML = html
    this._renderGutter(lines.length, this._lineOf(this.input.selectionStart))
    // 行号列宽 = 位数 * 一个字符宽 + 两侧留白（给 CSS 的 padding 兜底）
    const digits = String(lines.length).length
    this.gutter.style.width = `calc(${digits}ch + 24px)`
    this._syncActiveLine()
  }

  _renderGutter(count, activeLine) {
    let html = ''
    for (let i = 1; i <= count; i += 1) {
      const bad = this.errorLine === i ? ' bad' : ''
      const active = activeLine + 1 === i ? ' active' : ''
      html += `<div class="ed-ln${active}${bad}">${i}</div>`
    }
    this.gutterInner.innerHTML = html
  }

  _syncActiveLine() {
    const line = this._lineOf(this.input.selectionStart)
    const metrics = this._metrics()
    this.activeLine.style.height = `${metrics.lineHeight}px`
    this.activeLine.style.transform = `translateY(${metrics.padTop + line * metrics.lineHeight - this.input.scrollTop}px)`
    const nodes = this.gutterInner.children
    for (let i = 0; i < nodes.length; i += 1) {
      nodes[i].classList.toggle('active', i === line)
    }
  }

  _syncScroll() {
    const { scrollTop, scrollLeft } = this.input
    this.code.style.transform = `translate(${-scrollLeft}px, ${-scrollTop}px)`
    this.gutterInner.style.transform = `translateY(${-scrollTop}px)`
    const metrics = this._metrics()
    const line = this._lineOf(this.input.selectionStart)
    this.activeLine.style.transform = `translateY(${metrics.padTop + line * metrics.lineHeight - scrollTop}px)`
  }

  _metrics() {
    if (!this._metricsCache || this._metricsCache.width !== this.input.clientWidth) {
      const style = getComputedStyle(this.input)
      this._metricsCache = {
        width: this.input.clientWidth,
        lineHeight: parseFloat(style.lineHeight) || 20,
        padTop: parseFloat(style.paddingTop) || 0,
      }
    }
    return this._metricsCache
  }

  _afterEdit() {
    this._trim()
    this._syncCursor()
    this._emitChange()
    if (this.frame) return
    this.frame = requestAnimationFrame(() => {
      this.frame = 0
      this._render()
    })
  }

  // --------------------------------------------------------------- 历史 ----

  _record(type) {
    if (this.restoring) return
    const now = performance.now()
    const s = this.input.selectionStart
    const e = this.input.selectionEnd
    const top = this.input.scrollTop
    const current = this.history[this.hIndex]
    const merge = type === 'type' && this.lastEditType === 'type' && now - this.lastEditAt < COALESCE_MS
    this.lastEditAt = now
    this.lastEditType = type

    if (merge && current) {
      current.text = this.text
      current.s = s
      current.e = e
      current.top = top
      return
    }
    this.history.splice(this.hIndex + 1)
    this.history.push({ text: this.text, s, e, top })
    if (this.history.length > HISTORY_LIMIT) this.history.shift()
    this.hIndex = this.history.length - 1
  }

  _trim() {
    // 单文件超过 500 步后丢弃最旧的快照，避免长时间编辑把内存拖垮。
    if (this.history.length <= HISTORY_LIMIT) return
    const drop = this.history.length - HISTORY_LIMIT
    this.history.splice(0, drop)
    this.hIndex = Math.max(0, this.hIndex - drop)
  }

  _restore(snapshot) {
    this.restoring = true
    this._setText(snapshot.text)
    this.input.value = snapshot.text
    this.input.setSelectionRange(snapshot.s, snapshot.e)
    this.input.scrollTop = snapshot.top
    this.restoring = false
    this.lastEditType = 'restore'
    this._render()
    this._syncScroll()
    this._syncCursor()
    this._emitChange()
  }

  _emitChange() {
    this.onChange({ dirty: this.dirty, lines: this.lineCount, chars: this.text.length })
  }

  // --------------------------------------------------------------- 光标 ----

  _syncCursor() {
    const s = this.input.selectionStart
    const e = this.input.selectionEnd
    this.onCursor({ line: this._lineOf(s) + 1, col: s - this._lineStart(this._lineOf(s) + 1) + 1, sel: e - s })
    this._syncActiveLine()
  }

  /** 唯一改文本的入口：顺手作废行号索引。
   *  绕开它直接赋值 `this.text` 会让行号/光标列算在旧索引上（曾经就是这么漂的）。 */
  _setText(text) {
    if (text === this.text) return
    this.text = text
    this._lineIndex = null
  }

  /** 行首偏移表。按需重建，一次 O(n)，之后每次定位 O(log n)。
   *  之前 `_lineOf` 是每调一次扫全文，而行号列每渲染一行就调一次 ⇒ O(n²)，
   *  4000 行文件每敲一键要扫 1600 万字符，手感直接塌。 */
  _index() {
    if (this._lineIndex) return this._lineIndex
    const starts = [0]
    for (let i = 0; i < this.text.length; i += 1) {
      if (this.text.charCodeAt(i) === 10) starts.push(i + 1)
    }
    this._lineIndex = starts
    return starts
  }

  _lineOf(offset) {
    const starts = this._index()
    const at = Math.max(0, Math.min(offset, this.text.length))
    let low = 0
    let high = starts.length - 1
    while (low < high) {
      const mid = (low + high + 1) >> 1
      if (starts[mid] <= at) low = mid
      else high = mid - 1
    }
    return low
  }

  _lineStart(line) {
    const starts = this._index()
    if (line <= 1) return 0
    return starts[Math.min(line - 1, starts.length - 1)]
  }

  _lineEnd(offset) {
    const at = this.text.indexOf('\n', offset)
    return at < 0 ? this.text.length : at
  }

  _ensureVisible(line) {
    const metrics = this._metrics()
    const top = line * metrics.lineHeight
    const height = this.input.clientHeight
    if (top < this.input.scrollTop) {
      this.input.scrollTop = Math.max(0, top - metrics.lineHeight)
    } else if (top + metrics.lineHeight > this.input.scrollTop + height) {
      this.input.scrollTop = top + metrics.lineHeight * 2 - height
    }
    this._syncScroll()
  }

  // --------------------------------------------------------------- 编辑 ----

  _replace(start, end, insert, caret) {
    const top = this.input.scrollTop
    const left = this.input.scrollLeft
    this._setText(this.text.slice(0, start) + insert + this.text.slice(end))
    this.input.value = this.text
    const at = caret == null ? start + insert.length : caret
    this.input.setSelectionRange(at, at)
    this.input.scrollTop = top
    this.input.scrollLeft = left
    return at
  }

  _indentUnit() {
    return INDENT
  }

  _selectedLines() {
    const s = this.input.selectionStart
    const e = this.input.selectionEnd
    const first = this._lineOf(s)
    const last = this._lineOf(e > s && this.text[e - 1] === '\n' ? e - 1 : e)
    return { first, last, from: this._lineStart(first + 1), to: this._lineEnd(this._lineStart(last + 1)) }
  }

  _indentSelection(outdent) {
    const { from, to } = this._selectedLines()
    const block = this.text.slice(from, to)
    const unit = this._indentUnit()
    let delta = 0
    let headDelta = 0
    const lines = block.split('\n').map((line, index) => {
      if (outdent) {
        const strip = line.startsWith(unit) ? unit.length : /^[ \t]/.test(line) ? 1 : 0
        if (index === 0) headDelta = -strip
        delta -= strip
        return line.slice(strip)
      }
      if (index === 0) headDelta = unit.length
      delta += unit.length
      return unit + line
    })
    const s = this.input.selectionStart
    const e = this.input.selectionEnd
    this._replace(from, to, lines.join('\n'), 0)
    this.input.setSelectionRange(Math.max(from, s + headDelta), Math.max(from, e + delta))
    this._record(outdent ? 'outdent' : 'indent')
    this._afterEdit()
  }

  _toggleComment() {
    const prefix = commentSyntaxOf(this.lang)
    if (!prefix) {
      this.onHint(`当前语言（${this.lang}）没有注释语法，Ctrl+/ 不适用`)
      return
    }
    const { first, last, from, to } = this._selectedLines()
    const caretBefore = this.input.selectionStart
    const column = caretBefore - from
    const block = this.text.slice(from, to)
    const lines = block.split('\n')
    const allCommented = lines.every((line) => line.trim() === '' || line.trimStart().startsWith(prefix))
    const changed = lines.map((line) => {
      if (line.trim() === '') return line
      if (allCommented) {
        const index = line.indexOf(prefix)
        const after = line.slice(index + prefix.length)
        return line.slice(0, index) + (after.startsWith(' ') ? after.slice(1) : after)
      }
      const indent = /^[ \t]*/.exec(line)[0]
      return `${indent}${prefix} ${line.slice(indent.length)}`
    })
    const shift = allCommented ? -2 : 3
    this._replace(from, to, changed.join('\n'), 0)
    if (first === last) {
      const caret = Math.max(from, Math.min(from + column + shift, this._lineEnd(from)))
      this.input.setSelectionRange(caret, caret)
    } else {
      const lastStart = this._lineStart(last + 1)
      this.input.setSelectionRange(from, this._lineEnd(lastStart))
    }
    this._record('comment')
    this._afterEdit()
  }

  _duplicate() {
    const s = this.input.selectionStart
    const e = this.input.selectionEnd
    if (e > s) {
      const chunk = this.text.slice(s, e)
      this._replace(e, e, chunk, e + chunk.length)
      this.input.setSelectionRange(s, e + chunk.length)
    } else {
      const start = this._lineStart(this._lineOf(s) + 1)
      const end = this._lineEnd(start)
      const chunk = this.text.slice(start, end)
      const offset = s - start
      if (end < this.text.length) {
        const at = end + 1
        this._replace(at, at, `${chunk}\n`, 0)
        const caret = at + offset
        this.input.setSelectionRange(caret, caret)
      } else {
        const at = this.text.length
        this._replace(at, at, `\n${chunk}`, 0)
        const caret = at + 1 + offset
        this.input.setSelectionRange(caret, caret)
      }
    }
    this._record('duplicate')
    this._afterEdit()
  }

  _deleteLines() {
    const { first, from, to } = this._selectedLines()
    let start = from
    let end = to
    if (end < this.text.length) {
      end += 1 // 连同换行一起删，否则会留下一行空洞
    } else if (start > 0) {
      start -= 1
    }
    this._replace(start, end, '', Math.min(start, this.text.length))
    this._record('delete-line')
    this._afterEdit()
  }

  _moveLines(direction) {
    const { first, last } = this._selectedLines()
    if (direction < 0 && first === 0) return
    const start = this._lineStart(first + 1)
    const end = this._lineEnd(this._lineStart(last + 1))
    const block = this.text.slice(start, end)
    const caretS = this.input.selectionStart
    const caretE = this.input.selectionEnd
    if (direction > 0) {
      if (end >= this.text.length) return
      const nextStart = end + 1
      const nextEnd = this._lineEnd(nextStart)
      const neighbour = this.text.slice(nextStart, nextEnd)
      const tail = nextEnd < this.text.length ? this.text.slice(nextEnd) : ''
      this._setText(this.text.slice(0, start) + neighbour + '\n' + block + tail)
      this.input.value = this.text
      const shift = neighbour.length + 1
      this.input.setSelectionRange(caretS + shift, caretE + shift)
    } else {
      const prevStart = this._lineStart(first)
      const neighbour = this.text.slice(prevStart, start - 1)
      const head = this.text.slice(0, prevStart)
      const tail = this.text.slice(end)
      this._setText(head + block + '\n' + neighbour + tail)
      this.input.value = this.text
      const shift = start - prevStart
      this.input.setSelectionRange(caretS - shift, caretE - shift)
    }
    this._record('move-line')
    this._afterEdit()
  }

  // --------------------------------------------------------------- 键盘 ----

  _onKeyDown(event) {
    if (event.isComposing || this.composing) return
    const mod = event.ctrlKey || event.metaKey
    const key = event.key

    if (mod && !event.altKey && !event.shiftKey && key.toLowerCase() === 'z') {
      event.preventDefault()
      this.undo()
      return
    }
    if (mod && ((!event.altKey && event.shiftKey && key.toLowerCase() === 'z') || key.toLowerCase() === 'y')) {
      event.preventDefault()
      this.redo()
      return
    }
    if (mod && key === '/') {
      event.preventDefault()
      this._toggleComment()
      return
    }
    if (mod && key.toLowerCase() === 'd') {
      event.preventDefault()
      this._duplicate()
      return
    }
    if (mod && event.shiftKey && key.toLowerCase() === 'k') {
      event.preventDefault()
      this._deleteLines()
      return
    }
    if (event.altKey && (key === 'ArrowUp' || key === 'ArrowDown')) {
      event.preventDefault()
      this._moveLines(key === 'ArrowUp' ? -1 : 1)
      return
    }
    if (key === 'Tab') {
      event.preventDefault()
      const multiline = this.input.selectionStart !== this.input.selectionEnd && this.text.slice(this.input.selectionStart, this.input.selectionEnd).includes('\n')
      if (multiline || event.shiftKey) {
        this._indentSelection(event.shiftKey)
        return
      }
      const unit = this._indentUnit()
      const col = this.input.selectionStart - this._lineStart(this._lineOf(this.input.selectionStart) + 1)
      const fill = unit.length - (col % unit.length)
      const s = this.input.selectionStart
      const e = this.input.selectionEnd
      this._replace(s, e, ' '.repeat(fill))
      this._record('tab')
      this._afterEdit()
      return
    }
    if (key === 'Enter') {
      event.preventDefault()
      this._newline()
      return
    }
    if (key === 'Backspace') {
      if (this._backspacePair()) {
        event.preventDefault()
      }
      return
    }
    // 光标右边已经有一个闭合符时，再打一次应当「穿过去」，而不是变出两个。
    // 这是最容易被一眼看穿的手感问题：`()` 里打 `)` 会变成 `())`。
    if (CLOSERS.has(key) && !mod && !event.altKey && this.input.selectionStart === this.input.selectionEnd) {
      const at = this.input.selectionStart
      if (this.text[at] === key) {
        event.preventDefault()
        this.input.setSelectionRange(at + 1, at + 1)
        this._syncCursor()
        return
      }
    }
    // Home 先落到本行第一个非空白字符，再按一次才回列 1（VSCode / Sublime 的习惯）
    if (key === 'Home' && !mod && !event.altKey && !event.shiftKey && this.input.selectionStart === this.input.selectionEnd) {
      const at = this.input.selectionStart
      const start = this._lineStart(this._lineOf(at) + 1)
      const indent = /^[ \t]*/.exec(this.text.slice(start, this._lineEnd(start)))[0].length
      if (at !== start + indent && start + indent < this._lineEnd(start)) {
        event.preventDefault()
        this.input.setSelectionRange(start + indent, start + indent)
        this._syncCursor()
        return
      }
    }
    if (PAIRS[key] && !mod && !event.altKey) {
      if (this._autoPair(key, event)) event.preventDefault()
    }
  }

  _newline() {
    const s = this.input.selectionStart
    const e = this.input.selectionEnd
    const lineStart = this._lineStart(this._lineOf(s) + 1)
    const indent = /^[ \t]*/.exec(this.text.slice(lineStart, s))[0]
    const unit = this._indentUnit()
    const prev = this.text[s - 1]
    const next = this.text[e]
    if (s === e && prev && PAIRS[prev] && next === PAIRS[prev]) {
      const insert = `\n${indent}${unit}\n${indent}`
      this._replace(s, e, insert, s + 1 + indent.length + unit.length)
    } else if (s === e && prev && '([{'.includes(prev)) {
      const insert = `\n${indent}${unit}`
      this._replace(s, e, insert)
    } else {
      this._replace(s, e, `\n${indent}`)
    }
    this._record('newline')
    this._afterEdit()
  }

  _backspacePair() {
    const s = this.input.selectionStart
    if (s !== this.input.selectionEnd || s === 0) return false
    const prev = this.text[s - 1]
    const next = this.text[s]
    if (PAIRS[prev] && next === PAIRS[prev]) {
      this._replace(s - 1, s + 1, '', s - 1)
      this._record('backspace')
      this._afterEdit()
      return true
    }
    // 行首只有缩进时，退格一次删掉一级缩进（编辑器习惯：不让人一格一格点）
    const lineStart = this._lineStart(this._lineOf(s) + 1)
    const head = this.text.slice(lineStart, s)
    const unit = this._indentUnit()
    if (head.length > 0 && /^[ \t]+$/.test(head) && head.length % unit.length === 0) {
      this._replace(s - unit.length, s, '', s - unit.length)
      this._record('backspace')
      this._afterEdit()
      return true
    }
    return false
  }

  _autoPair(key, event) {
    const s = this.input.selectionStart
    const e = this.input.selectionEnd
    const closer = PAIRS[key]
    const selected = this.text.slice(s, e)

    if (s !== e) {
      this._replace(s, e, `${key}${selected}${closer}`, s + 1 + selected.length + 1)
      this.input.setSelectionRange(s + 1, s + 1 + selected.length)
      this._record('pair')
      this._afterEdit()
      return true
    }
    if (this.text[s] === closer) {
      // 已经存在闭合符：跳过它而不是再打一个
      this.input.setSelectionRange(s + 1, s + 1)
      this._syncCursor()
      return true
    }
    const quote = key === '"' || key === "'" || key === '`'
    const prev = this.text[s - 1] || ''
    const next = this.text[s] || ''
    if (quote && /[\w]/.test(prev) && /[\w]/.test(next)) return false
    if (quote && /[\w]/.test(prev) && !next) return false
    void event
    this._replace(s, s, `${key}${closer}`, s + 1)
    this._record('pair')
    this._afterEdit()
    return true
  }
}
