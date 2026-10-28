/**
 * 轻量着色器：把整段文本切成「逐行」的 HTML 片段。
 *
 * 设计约束（都是为了和透明 textarea 严丝合缝地叠在一起）：
 * - 一个 token 可能跨行（三引号字符串、块注释），所以按行切开发 span ——
 *   保证**每一行都是自闭合的 HTML**，这样给某一行套错误底色时不会撕裂标签。
 * - 只做词法着色，不做语法解析：编辑器的权威永远是 textarea 里的原文，
 *   着色层出错最多是颜色不对，不会影响输入、选区、撤销。
 * - 超过 MAX_HIGHLIGHT 字符直接返回纯文本行，宁可不着色也不要把窗口卡住。
 */

const MAX_HIGHLIGHT = 240_000;

const KEYWORDS = {
  python: new Set(
    ('False None True and as assert async await break class continue def del elif else except ' +
      'finally for from global if import in is lambda nonlocal not or pass raise return try ' +
      'while with yield match case').split(' '),
  ),
  javascript: new Set(
    ('await break case catch class const continue debugger default delete do else export ' +
      'extends finally for from function if import in instanceof let new of return static ' +
      'super switch this throw try typeof undefined var void while with yield true false null')
      .split(' '),
  ),
  yaml: new Set('true false null yes no on off'.split(' ')),
};

function escapeHtml(text) {
  let out = ''
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    if (ch === '&') out += '&amp;'
    else if (ch === '<') out += '&lt;'
    else if (ch === '>') out += '&gt;'
    else out += ch
  }
  return out
}

/** 行缓冲：把任意切片的文本按行摊开，每行自带完整 span。 */
class LineBuffer {
  constructor() {
    this.lines = ['']
  }

  push(text, cls) {
    if (!text) return
    const parts = text.split('\n')
    for (let i = 0; i < parts.length; i += 1) {
      if (i > 0) this.lines.push('')
      const part = parts[i]
      if (!part) continue
      const html = escapeHtml(part)
      this.lines[this.lines.length - 1] += cls ? `<span class="t-${cls}">${html}</span>` : html
    }
  }
}

const RULES = {
  json: [
    { re: /"(?:[^"\\]|\\.)*"(?=\s*:)/y, cls: 'key' },
    { re: /"(?:[^"\\]|\\.)*"/y, cls: 'str' },
    { re: /-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/y, cls: 'num' },
    { re: /\b(?:true|false|null)\b/y, cls: 'kw' },
    { re: /[{}\[\],:]/y, cls: 'punc' },
  ],
  javascript: [
    { re: /\/\/[^\n]*/y, cls: 'com' },
    { re: /\/\*[\s\S]*?\*\//y, cls: 'com' },
    { re: /`(?:[^`\\]|\\.)*`/y, cls: 'str' },
    { re: /"(?:[^"\\]|\\.)*"/y, cls: 'str' },
    { re: /'(?:[^'\\]|\\.)*'/y, cls: 'str' },
    { re: /\b\d+(?:\.\d+)?\b/y, cls: 'num' },
    { re: /[A-Za-z_$][\w$]*(?=\s*\()/y, cls: 'fn' },
    { re: /[A-Za-z_$][\w$]*/y, cls: 'word' },
    { re: /[{}()[\];,.]/y, cls: 'punc' },
  ],
  python: [
    { re: /#[^\n]*/y, cls: 'com' },
    { re: /[rbfu]{0,2}"""(?:[^"\\]|\\.|"(?!""))*"""/y, cls: 'str' },
    { re: /[rbfu]{0,2}'''(?:[^'\\]|\\.|'(?!''))*'''/y, cls: 'str' },
    { re: /[rbfu]{0,2}"(?:[^"\\\n]|\\.)*"/y, cls: 'str' },
    { re: /[rbfu]{0,2}'(?:[^'\\\n]|\\.)*'/y, cls: 'str' },
    { re: /@[A-Za-z_][\w.]*/y, cls: 'deco' },
    { re: /\b\d+(?:\.\d+)?\b/y, cls: 'num' },
    { re: /[A-Za-z_]\w*(?=\s*\()/y, cls: 'fn' },
    { re: /[A-Za-z_]\w*/y, cls: 'word' },
    { re: /[{}()[\];,.:=+\-*/<>!%&|^~]/y, cls: 'punc' },
  ],
  yaml: [
    { re: /#[^\n]*/y, cls: 'com' },
    { re: /^[ \t-]*[\w.$-]+(?=\s*:)/my, cls: 'key' },
    { re: /"(?:[^"\\]|\\.)*"/y, cls: 'str' },
    { re: /'(?:[^']|'')*'/y, cls: 'str' },
    { re: /\b\d+(?:\.\d+)?\b/y, cls: 'num' },
    { re: /[A-Za-z_][\w.-]*/y, cls: 'word' },
  ],
}

const MD_RULES = [
  { re: /^#{1,6} [^\n]*/my, cls: 'mdh' },
  { re: /^[ \t]*>[^\n]*/my, cls: 'mdq' },
  { re: /^[ \t]*(?:[-*+]|\d+\.)[ \t]/my, cls: 'mdl' },
  { re: /^[ \t]*```[^\n]*/my, cls: 'mdf' },
  { re: /`[^`\n]+`/y, cls: 'mdc' },
  { re: /\*\*[^*\n]+\*\*/y, cls: 'mdb' },
  { re: /\[[^\]\n]*\]\([^)\n]*\)/y, cls: 'mdk' },
  { re: /^[ \t]*---[ \t]*$/my, cls: 'mdr' },
]

function scan(buffer, text, rules, keywordSet) {
  let index = 0
  while (index < text.length) {
    let matched = false
    for (const rule of rules) {
      rule.re.lastIndex = index
      const hit = rule.re.exec(text)
      if (!hit || hit.index !== index) continue
      const value = hit[0]
      if (rule.cls === 'word') {
        buffer.push(value, keywordSet && keywordSet.has(value) ? 'kw' : undefined)
      } else {
        buffer.push(value, rule.cls)
      }
      index += value.length
      matched = true
      break
    }
    if (matched) continue
    // 没有规则命中：把这一小段普通文本一次吞掉（逐字符 push 会把行缓冲拆得过碎）
    let next = index + 1
    while (next < text.length) {
      let stop = false
      for (const rule of rules) {
        rule.re.lastIndex = next
        const hit = rule.re.exec(text)
        if (hit && hit.index === next && hit[0].length) {
          stop = true
          break
        }
      }
      if (stop) break
      next += 1
    }
    buffer.push(text.slice(index, next), undefined)
    index = next
  }
}

/**
 * @param {string} text 原文（不含行号，保留原始换行）
 * @param {string} lang json | python | javascript | markdown | yaml | plain
 * @returns {string[]} 逐行 HTML
 */
export function highlightLines(text, lang) {
  const buffer = new LineBuffer()
  if (text.length > MAX_HIGHLIGHT || lang === 'plain' || !lang) {
    buffer.push(text, undefined)
    return buffer.lines
  }
  if (lang === 'markdown') {
    scan(buffer, text, MD_RULES, null)
    return buffer.lines
  }
  const rules = RULES[lang] || RULES.json
  scan(buffer, text, rules, KEYWORDS[lang] || null)
  return buffer.lines
}

export function languageOf(path) {
  const name = String(path || '').toLowerCase()
  const dot = name.lastIndexOf('.')
  const ext = dot >= 0 ? name.slice(dot) : ''
  if (ext === '.json' || ext === '.jsonc' || ext === '.json5' || ext === '.lock') return 'json'
  if (ext === '.py' || ext === '.pyi') return 'python'
  if (ext === '.js' || ext === '.mjs' || ext === '.cjs' || ext === '.ts') return 'javascript'
  if (ext === '.md' || ext === '.markdown') return 'markdown'
  if (ext === '.yml' || ext === '.yaml') return 'yaml'
  return 'plain'
}

/** 注释语法（Ctrl+/）。JSON 没有注释，返回 null 而不是塞一个假前缀。 */
export function commentSyntaxOf(lang) {
  if (lang === 'python' || lang === 'yaml') return '#'
  if (lang === 'javascript') return '//'
  return null
}
