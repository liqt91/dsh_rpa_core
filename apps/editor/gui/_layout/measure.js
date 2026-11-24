// 画布布局度量：只读 DOM，不改样式。由 _layout_shots/probe.mjs 通过 CDP 注入执行。
export function measure() {
  const box = (el) => (el ? el.getBoundingClientRect() : null)
  const px = (v) => Math.round(v * 10) / 10
  const cards = [...document.querySelectorAll('#cards .card')]
  const rows = cards.map((c) => {
    const name = c.querySelector('.name')
    const sum = c.querySelector('.sum')
    const no = c.querySelector('.no')
    const cs = getComputedStyle(c)
    return {
      label: name?.textContent ?? null,
      group: c.classList.contains('group'),
      left: px(box(c).left),
      width: px(box(c).width),
      padLeft: cs.paddingLeft,
      marginLeft: cs.marginLeft,
      accent: getComputedStyle(c, '::before').backgroundColor,
      h: px(box(c).height),
      overflowX: c.scrollWidth - c.clientWidth,
      nameOverflow: name ? name.scrollWidth - name.clientWidth : 0,
      nameH: name ? px(box(name).height) : null,
      sumOverflow: sum ? sum.scrollWidth - sum.clientWidth : 0,
      noOverflow: no ? no.scrollWidth - no.clientWidth : null,
      badgeRightGap: c.querySelector('.badge')
        ? px(box(c).right - box(c.querySelector('.badge')).right)
        : null,
    }
  })
  const mid = box(document.querySelector('#mid'))
  const right = box(document.querySelector('#right'))
  const form = document.querySelector('#paramForm')
  const bar = box(document.querySelector('.formBar'))
  const ast = box(document.querySelector('.astBox'))
  const elem = box(document.querySelector('.elemBox'))
  return {
    viewport: [innerWidth, innerHeight],
    docScrollW: document.documentElement.scrollWidth,
    docClientW: document.documentElement.clientWidth,
    cols: {
      left: px(box(document.querySelector('#left')).width),
      mid: px(mid.width),
      right: px(right.width),
    },
    cards: rows,
    cardCount: cards.length,
    distinctLefts: [...new Set(rows.map((x) => x.left))].sort((a, b) => a - b),
    budget: {
      midTitleH: px(box(document.querySelector('.flowTitle')).height),
      midScroll: (() => {
        const c = document.querySelector('#cards')
        return c ? c.scrollHeight - c.clientHeight : null
      })(),
      runBoxH: px(box(document.querySelector('#runBox')).height),
    },
    rightPanel: {
      h: px(right.height),
      formH: form ? px(box(form).height) : null,
      formOverflow: form ? form.scrollHeight - form.clientHeight : null,
      barTop: bar ? px(bar.top - right.top) : null,
      astTop: ast ? px(ast.top - right.top) : null,
      astH: ast ? px(ast.height) : null,
      emptyBelowBar: bar && ast ? px(ast.top - bar.top) : null,
      elemTop: elem ? px(elem.top - right.top) : null,
      gapBetweenElemAndBottom: elem ? px(right.bottom - elem.bottom) : null,
    },
    a11y: {
      cardTabindex: document.querySelectorAll('#cards .card[tabindex]').length,
      cardsTotal: cards.length,
      hiddenButFocusable: [...document.querySelectorAll('#cards .del')].filter(
        (b) => getComputedStyle(b).opacity === '0',
      ).length,
      cardCursor: getComputedStyle(cards[0] ?? document.body).cursor,
      disabledCursor: getComputedStyle(document.querySelector('#stopBtn')).cursor,
      minButtonH: Math.min(...[...document.querySelectorAll('#cards button, #bar button')].map((b) => px(box(b).height))),
      contrastSamples: ['#8b929e', '#64707d'].map((c) => c),
    },
    typography: {
      body: getComputedStyle(document.body).fontSize,
      cardName: cards[0] ? getComputedStyle(cards[0].querySelector('.name')).fontSize : null,
      cardSum: cards[0]?.querySelector('.sum') ? getComputedStyle(cards[0].querySelector('.sum')).fontSize : null,
    },
    leftPane: {
      cmdRows: [...document.querySelectorAll('.cmd')].slice(0, 3).map((c) => c.textContent),
      cmdIdElements: document.querySelectorAll('.cmd .cid').length,
      cmdCountLabel: document.querySelector('#cmdCount')?.textContent,
      nsGroups: document.querySelectorAll('.nsGroup').length,
    },
    gripDraggable: [...document.querySelectorAll('.grip')].slice(0, 3).map((g) => ({
      draggable: g.closest('.card')?.draggable ?? null,
      hasGrip: true,
    })),
  }
}
