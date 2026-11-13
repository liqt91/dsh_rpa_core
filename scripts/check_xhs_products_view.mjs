// 小红书工作台「商品库」渲染层判据（进默认门禁）。
//
// 流程现在逐个打开帖子抓 赞/藏/评 + 逐条评论，数据层存得住不等于页面上看得见。
// 只读源码（grep 字段名）证明不了「真的渲染出来了」——一个把 it.likes 写成
// it.likeCount 的改动源码里照样有 likes 字样。这里把 renderProducts 从 index.html
// 切出来，用最小 DOM shim **真跑一遍**，再做 Python to_json 键 ↔ 前端消费字段的
// 双向对账（任一侧漂移都红）。
import fs from 'node:fs'
import vm from 'node:vm'
import path from 'node:path'

const ROOT = path.resolve(import.meta.dirname, '..')
const HTML = path.join(ROOT, 'apps/xhs-workbench/static/index.html')
const SNAPSHOTS = path.join(ROOT, 'apps/xhs-workbench/src/snapshots.py')

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

const html = fs.readFileSync(HTML, 'utf8')
const py = fs.readFileSync(SNAPSHOTS, 'utf8')

// --- 切片：esc 一行 + renderProducts 整个函数体 --------------------------------
const escLine = (html.match(/^\s*const esc = .*$/m) || [])[0] || ''
const START = 'function renderProducts(p) {'
const start = html.indexOf(START)
const nextFn = html.indexOf('\n    function ', start + START.length)
const fnSrc = start >= 0 && nextFn > start ? html.slice(start, nextFn).trim() : ''

function render(payload) {
  assert(escLine, 'index.html 里找不到 esc() 定义')
  assert(fnSrc.includes('#products-body'), 'renderProducts 切片没切到函数体')
  const nodes = {}
  const $ = (sel) => nodes[sel] || (nodes[sel] = { innerHTML: '', textContent: '' })
  vm.runInNewContext(`${escLine}\n${fnSrc}\nrenderProducts(payload);`, {
    $, payload, console,
  }, { timeout: 2000 })
  return nodes['#products-body'].innerHTML
}

check('切片与接线哨兵：真的能跑出一行表格', () => {
  const out = render({ items: [{ href: 'https://xhs/1', title: '标题', text: '正文' }] })
  assert(out.includes('<table>'), '没渲染出 table')
  assert(out.includes('标题'), '标题没进 HTML')
  assert(out.includes('href="https://xhs/1"'), '链接没进 HTML')
})

check('互动数据三列：赞 / 藏 / 评 都要出现在渲染结果里', () => {
  const out = render({
    items: [{
      href: 'https://xhs/1', title: '标题', text: '正文',
      likes: '1.2万', collects: '345', comments: '88', commentList: [],
    }],
  })
  assert(out.includes('互动数据'), '表头缺「互动数据」列')
  assert(out.includes('赞 1.2万'), '点赞数没渲染')
  assert(out.includes('藏 345'), '收藏数没渲染')
  assert(out.includes('评 88'), '评论数没渲染')
})

check('逐条评论：条数、作者、正文都要在展开区里', () => {
  const out = render({
    items: [{
      href: 'https://xhs/1', title: '标题', text: '正文',
      likes: '', collects: '', comments: '2',
      commentList: [{ author: '甲', text: '第一条' }, { author: '', text: '第二条' }],
    }],
  })
  assert(out.includes('评论 2 条'), '评论条数没渲染')
  assert(out.includes('甲：第一条'), '作者前缀丢了')
  assert(out.includes('第二条'), '无作者评论丢了')
  assert(out.includes('<details'), '评论没有可展开容器')
})

check('老数据兼容：字段缺失不出现 undefined / NaN，也不硬塞空展开区', () => {
  const out = render({ items: [{ href: 'https://xhs/1', title: '旧', text: 't' }] })
  assert(!/undefined|NaN/.test(out), `脏输出：${out}`)
  assert(!out.includes('<details'), '没有评论却渲染了展开区')
})

check('转义：帖子正文与评论里的 HTML 不能注入', () => {
  const out = render({
    items: [{
      href: 'https://xhs/1', title: '<img src=x onerror=alert(1)>', text: '<script>x</script>',
      likes: '', collects: '', comments: '1',
      commentList: [{ author: '<b>甲</b>', text: '<i>坏</i>' }],
    }],
  })
  assert(!out.includes('<img src=x'), '标题没转义')
  assert(!out.includes('<script>'), '正文没转义')
  assert(!out.includes('<b>甲</b>') && !out.includes('<i>坏</i>'), '评论没转义')
  assert(out.includes('&lt;img'), 'esc 没生效')
})

// --- 双向对账：Python to_json 的键 ↔ 前端 it.<key> ----------------------------
const toJsonStart = py.indexOf('    def to_json(self) -> dict[str, Any]:')
// 只切到下一个**任意缩进**的顶层定义：往下多带一个 _comment_list 就会把
// 评论里的 "author"/"text" 键也抓进来，对账凭空多出两个字段。
const boundaries = ['\ndef ', '\nclass ', '\n@'].map((mark) => py.indexOf(mark, toJsonStart + 10))
const toJsonEnd = Math.min(...boundaries.filter((i) => i > 0))
const toJsonSrc = py.slice(toJsonStart, toJsonEnd)
const pythonKeys = new Set([...toJsonSrc.matchAll(/"(\w+)":/g)].map((m) => m[1]))
const frontKeys = new Set([...fnSrc.matchAll(/\bit\.(\w+)\b/g)].map((m) => m[1]))

check('双向对账：Python 导出的每个字段前端都消费，前端用的每个字段后端都导出', () => {
  assert(toJsonStart >= 0, 'snapshots.py 里没找到 Product.to_json')
  assert(pythonKeys.size >= 7, `Python 导出字段太少：${[...pythonKeys]}`)
  const missingOnFront = [...pythonKeys].filter((k) => !frontKeys.has(k))
  const missingOnBack = [...frontKeys].filter((k) => !pythonKeys.has(k))
  assert(missingOnFront.length === 0, `前端漏渲染：${missingOnFront}`)
  assert(missingOnBack.length === 0, `前端读了后端不导出的字段：${missingOnBack}`)
})

check('元验证：字段名漂移时判据本身要能报红', () => {
  // 先证明基线是干净的：源码已经漂移时 replace 会落空，还拿脏串自测就会
  // 得出「能报红」的恒绿结论（负向注入 V1 实测踩到）。
  assert(/\bit\.likes\b/.test(fnSrc), '基线 fnSrc 里没有 it.likes，无法做元验证')
  assert(!/\bit\.likeCount\b/.test(fnSrc), '基线 fnSrc 已经漂移')
  // 把前端的 it.likes 改成 it.likeCount，对账必须立刻发现两侧不一致。
  const drifted = fnSrc.replace(/\bit\.likes\b/g, 'it.likeCount')
  const driftedKeys = new Set([...drifted.matchAll(/\bit\.(\w+)\b/g)].map((m) => m[1]))
  assert(!driftedKeys.has('likes') && driftedKeys.has('likeCount'), '注入没生效')
  assert([...pythonKeys].some((k) => !driftedKeys.has(k)), '漂移后对账竟然还能过')
})

const failed = results.filter((r) => !r.ok)
for (const r of results) {
  console.log(`${r.ok ? 'PASS' : 'FAIL'} ${r.name}${r.ok ? '' : ` — ${r.detail}`}`)
}
console.log(`XHS PRODUCTS VIEW CHECK ${failed.length ? 'FAILED' : 'PASSED'} (${results.length - failed.length}/${results.length})`)
process.exit(failed.length ? 1 : 0)
