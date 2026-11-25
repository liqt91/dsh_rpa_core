// 画布布局的不变量断言。判据来自 _layout_shots/report.json（probe.mjs 采集的真实渲染值）。
// 用法：node assert_layout.mjs
// 任一条不满足即 exit 1 —— 这些值在改样式前是红的（见 _layout_shots/report_before.json）。
import { readFileSync } from 'node:fs'

const report = JSON.parse(readFileSync('_layout_shots/report.json', 'utf8'))
const byName = Object.fromEntries(report.map((r) => [r.name, r]))

const checks = []
const ok = (name, pass, detail) => checks.push({ name, pass, detail })

function must(name) {
  const r = byName[name]
  if (!r) throw new Error(`缺少用例 ${name}，先跑 probe.mjs`)
  return r
}

const deep1280 = must('after_deep_1280').report
const deep1024 = must('after_deep_1024').report
const deep960 = must('after_deep_960').report
const struct = must('after_struct_1280').report

// 1. 层级必须真的画出来：depth 0..3 应产生 4 条不同的左边缘
for (const [label, r] of [
  ['1280', deep1280],
  ['1024', deep1024],
  ['960', deep960],
]) {
  ok(`缩进层数 @${label}`, r.distinctLefts.length === 4, `distinctLefts=${JSON.stringify(r.distinctLefts)}`)
  const steps = r.cards.map((c, i) => (i ? c.left - r.cards[i - 1 < 0 ? 0 : 0].left : 0))
  void steps
  // 相邻缩进差必须是 16 的倍数（0 / 16 / 32 / 48）
  const gaps = new Set(r.distinctLefts.map((x) => x - r.distinctLefts[0]))
  ok(
    `缩进步长 @${label}`,
    [...gaps].every((g) => g % 16 === 0) && gaps.size === 4,
    `offsets=${JSON.stringify([...gaps])}`,
  )
}

// 2. 行高只有两档：节点 38、分支标签 22
const heights = new Set(deep1280.cards.map((c) => c.h))
ok('行高两档', heights.size === 2 && heights.has(38) && heights.has(22), `heights=${JSON.stringify([...heights])}`)

// 3. 不许有横向溢出（名字/摘要靠省略号收，不靠把卡片撑破）
for (const [label, r] of [
  ['1280', deep1280],
  ['1024', deep1024],
  ['960', deep960],
]) {
  const bad = r.cards.filter((c) => c.overflowX > 0)
  ok(`无横向溢出 @${label}`, bad.length === 0, `overflowing=${JSON.stringify(bad.map((b) => b.label))}`)
  const cut = r.cards.filter((c) => c.nameOverflow > 0)
  ok(`节点名不被截断 @${label}`, cut.length === 0, `truncated=${JSON.stringify(cut.map((c) => c.label))}`)
}

// 4. 1280x777（默认窗口比例 0.6667×0.72 在 1920 屏上就是这尺寸）13 行不滚动
ok('13 行装得下 @1280', deep1280.budget.midScroll === 0, `midScroll=${deep1280.budget.midScroll}`)

// 5. 流程标题单行（1024 宽下不再折成两行）
ok(
  '流程标题单行',
  [deep1280, deep1024, deep960].every((r) => r.budget.midTitleH < 44),
  `titleH=${[deep1280, deep1024, deep960].map((r) => r.budget.midTitleH)}`,
)

// 6. 键盘可达：每张非组行卡都能聚焦；组行不是可点节点
const deepCards = deep1280.cards
const groupCount = deepCards.filter((c) => c.group).length
ok(
  '卡片键盘可达',
  deep1280.a11y.cardTabindex === deepCards.length - groupCount && deep1280.a11y.cardTabindex > 0,
  `tabindex=${deep1280.a11y.cardTabindex} nodes=${deepCards.length - groupCount}`,
)
const kb = must('after_keyboard').extra[0]
ok('Enter 选中且焦点留在原卡', kb.focused === kb.selected && kb.selected === 't_inner', JSON.stringify(kb))

// 7. 重绘不跳回顶部
const sc = must('after_short_1280').extra[0]
ok('选中后滚动位置保持', sc.before > 0 && sc.after === sc.before, JSON.stringify(sc))
const old = must('after_old_mechanism').extra[0]
ok('对照组：清空子节点确实会把 scrollTop 归零', old.before > 0 && old.after === 0, JSON.stringify(old))

// 8. 点击目标与指针语义
ok('删除按钮 ≥24px', deep1280.a11y.minButtonH >= 24, `minButtonH=${deep1280.a11y.minButtonH}`)
ok('禁用按钮不是 wait 光标', deep1280.a11y.disabledCursor === 'not-allowed', deep1280.a11y.disabledCursor)

// 9. 右栏不再被折叠的 AST 撑满
ok(
  '折叠的 AST 不吃余高',
  deep1280.rightPanel.astH < 60 && deep1280.rightPanel.formH > 300,
  `astH=${deep1280.rightPanel.astH} formH=${deep1280.rightPanel.formH}`,
)

// 10. 短流程（struct，9 行）同样成立
ok('短流程缩进 2 级', new Set(struct.distinctLefts).size === 2, JSON.stringify(struct.distinctLefts))

let failed = 0
for (const c of checks) {
  if (!c.pass) failed++
  console.log(`${c.pass ? 'PASS' : 'FAIL'}  ${c.name}  ${c.detail}`)
}
console.log(failed === 0 ? `\n全部通过：${checks.length} 条` : `\n失败 ${failed} / ${checks.length}`)
process.exit(failed === 0 ? 0 : 1)
