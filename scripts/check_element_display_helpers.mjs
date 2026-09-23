// 校验编辑器「元素确认框只读区块」的纯函数区（app.js 的 element-display-helpers）。
// 做法与 check_capture_helpers.mjs 一致：按首尾锚点切片，用真实捕获载荷求值后断言。
//
// 为什么必须单独用 node 验：这些函数**只在浏览器里跑**。Python 侧只能读源码文本
// （测试断言得到「写了这行字」），断言不到「渲染出来的到底是哪几行」。而这一区的
// 唯一职责就是「把已捕获的数据如实显示」——显示错了，Python 侧一个字也看不见。
//
// 用法：node scripts/check_element_display_helpers.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const APP_PATH = join(here, "..", "src", "rpa_core", "devserver", "static", "app.js");
const source = readFileSync(APP_PATH, "utf8");

// 0) 整文件语法校验（new Function 只编译不执行；body 传 undefined 亦不会跑）
try {
  new Function(source);
} catch (err) {
  console.error(`FAIL: app.js 语法错误 → ${err.message}`);
  process.exit(1);
}

const START = "// [element-display-helpers:start]";
const END = "// [element-display-helpers:end]";
const start = source.indexOf(START);
const end = source.indexOf(END);
if (start < 0 || end < 0 || end <= start) {
  console.error(`FAIL: 未能从 app.js 定位展示纯函数区（锚点 ${START} / ${END}）`);
  process.exit(1);
}
const slice = source.slice(start + START.length, end);

const {
  elementDisplayText, elementMetadataLines, elementSemanticLines, elementCandidateLines,
  clipElementText, ELEMENT_DISPLAY_CLIP,
} = new Function(`${slice}
return { elementDisplayText, elementMetadataLines, elementSemanticLines, elementCandidateLines,
  clipElementText, ELEMENT_DISPLAY_CLIP };`)();

// ---- 断言 -------------------------------------------------------------------
let failed = 0;
const check = (label, got, expected) => {
  const ok = JSON.stringify(got) === JSON.stringify(expected);
  if (!ok) failed += 1;
  console.log(
    `${ok ? "PASS" : "FAIL"} | ${label}${ok ? "" : ` → got ${JSON.stringify(got)} want ${JSON.stringify(expected)}`}`,
  );
};
const has = (label, text, needle) => {
  const ok = String(text).includes(needle);
  if (!ok) failed += 1;
  console.log(`${ok ? "PASS" : "FAIL"} | ${label}${ok ? "" : ` → 缺 ${JSON.stringify(needle)} in ${JSON.stringify(text)}`}`);
};
const lacks = (label, text, needle) => {
  const ok = !String(text).includes(needle);
  if (!ok) failed += 1;
  console.log(`${ok ? "PASS" : "FAIL"} | ${label}${ok ? "" : ` → 不该出现 ${JSON.stringify(needle)}`}`);
};

// 真实载荷：与 tests/contract/test_gui_panels.py 的 _captured_browser_descriptor 同款
// （content.js buildDescriptor 的产出形状）——两端判据喂同一份数据。
const browserDescriptor = () => ({
  kind: "browser",
  selector: {
    css: "#sb_form_q",
    candidates: [
      { kind: "id", selector: "#sb_form_q", matchedCount: 1 },
      { kind: "attribute", selector: 'input[name="q"]', matchedCount: 3 },
    ],
  },
  verifyCount: 1,
  metadata: {
    tag: "textarea",
    id: "sb_form_q",
    classes: ["search", "box"],
    text: "",
    rect: { x: 10, y: 20, width: 300, height: 40 },
    role: "searchbox",
    accessibleName: "搜索",
    placeholder: "请输入搜索内容",
    label: "搜索框",
    containerText: "主页 搜索 更多",
    url: "https://www.bing.com/",
    title: "Bing",
  },
});

// 1) browser：metadata 行序与内容
check("browser metadata 行", elementMetadataLines(browserDescriptor()), [
  "tag: textarea",
  "id: sb_form_q",
  "classes: search box",
  "rect: 300×40 @ (10,20)",
]);

// 2) browser：语义特征与页面指纹（此前只入库不展示）
check("browser 语义特征行", elementSemanticLines(browserDescriptor()), [
  "role: searchbox",
  "accessibleName: 搜索",
  "placeholder: 请输入搜索内容",
  "label: 搜索框",
  "containerText: 主页 搜索 更多",
  "url: https://www.bing.com/",
  "title: Bing",
]);

// 3) 候选：条数 + kind + 实测命中数 + 不唯一标记
check("候选行（含不唯一标记）", elementCandidateLines(browserDescriptor()), [
  "备选定位 2 条（主选择器失效时按序回退）",
  "  1. [id] #sb_form_q · 命中 1",
  '  2. [attribute] input[name="q"] · 命中 3（不唯一）',
]);

// 4) 完整只读区块：metadata + 语义特征 + 候选，顺序固定
check("完整区块行序", elementDisplayText(browserDescriptor()), [
  "tag: textarea",
  "id: sb_form_q",
  "classes: search box",
  "rect: 300×40 @ (10,20)",
  "role: searchbox",
  "accessibleName: 搜索",
  "placeholder: 请输入搜索内容",
  "label: 搜索框",
  "containerText: 主页 搜索 更多",
  "url: https://www.bing.com/",
  "title: Bing",
  "备选定位 2 条（主选择器失效时按序回退）",
  "  1. [id] #sb_form_q · 命中 1",
  '  2. [attribute] input[name="q"] · 命中 3（不唯一）',
].join("\n"));

// 5) desktop：className / name 必须显示（win32 定位无窗口文本控件的唯一手段、
//    classNameRe 正则的输入），且不带 browser 专有的语义特征行
const desktopDescriptor = () => ({
  kind: "desktop",
  selector: { locator: { backend: "win32", classNameRe: "WindowsForms10\\.LISTBOX" } },
  verifyCount: 1,
  metadata: {
    tag: "ListBox",
    controlType: "List",
    automationId: "listBox1",
    name: "listBox1",
    className: "WindowsForms10.LISTBOX.app.0.34f5582_r8_ad1",
    windowTitle: "RPA Core Desktop Demo",
    windowHandle: 6225970,
    point: { x: 120, y: 240 },
  },
});
const desktopText = elementDisplayText(desktopDescriptor());
has("desktop 显示 className", desktopText, "className: WindowsForms10.LISTBOX.app.0.34f5582_r8_ad1");
has("desktop 显示 name", desktopText, "name: listBox1");
has("desktop 显示 controlType", desktopText, "controlType: List");
has("desktop 显示 window", desktopText, "window: RPA Core Desktop Demo");
check("desktop 不带 browser 语义特征", elementSemanticLines(desktopDescriptor()), []);
lacks("不显示 windowHandle（运行期值）", desktopText, "windowHandle");
lacks("不显示 point（运行期值）", desktopText, "6225970");

// 6) 命中数缺席 / 非法：如实写「命中未实测」，不编数
const noCount = browserDescriptor();
noCount.selector.candidates = [
  { kind: "css", selector: "input#q" },
  { kind: "css", selector: "input.q2", matchedCount: "1" },
  { kind: "css", selector: "input.q3", matchedCount: 1.5 },
];
check("命中数缺席/非法 → 命中未实测", elementCandidateLines(noCount), [
  "备选定位 3 条（主选择器失效时按序回退）",
  "  1. [css] input#q · 命中未实测",
  "  2. [css] input.q2 · 命中未实测",
  "  3. [css] input.q3 · 命中未实测",
]);

// 7) 无候选 / 坏形状：不崩、不出现空标题
const noCandidates = browserDescriptor();
delete noCandidates.selector.candidates;
check("无候选 → 空（不出现空标题）", elementCandidateLines(noCandidates), []);
check("候选非数组 → 空", elementCandidateLines({ selector: { candidates: "x" }, kind: "browser" }), []);
check("候选含非对象项 → 跳过", elementCandidateLines({
  selector: { candidates: [null, 7, { kind: "id", selector: "#a", matchedCount: 1 }] },
}), ["备选定位 1 条（主选择器失效时按序回退）", "  1. [id] #a · 命中 1"]);
check("selector 不是对象 → 不崩", elementCandidateLines({ kind: "browser", selector: "..." }), []);

// 8) 截断：URL / 容器文本可长达 200 字，展示层必须截断（否则撑爆对话框）
check("截断长度上限", ELEMENT_DISPLAY_CLIP, 80);
check("超长值截断并加省略号", clipElementText("a".repeat(100)), `${"a".repeat(80)}…`);
check("刚好到上限不截断", clipElementText("b".repeat(80)), "b".repeat(80));
check("压平换行/制表", clipElementText("a\n  b\tc"), "a b c");
const longUrl = browserDescriptor();
longUrl.metadata.url = `https://example.com/${"x".repeat(200)}`;
has("超长 url 在完整区块里也截断", elementDisplayText(longUrl), `${"x".repeat(60)}…`);

// 9) 空壳：给不出任何事实时写「(无 metadata)」，不留白也不报错
check("空 metadata → (无 metadata)", elementDisplayText({ kind: "browser" }), "(无 metadata)");
check("坏形状不崩", elementDisplayText({ kind: "desktop", metadata: null, selector: null }), "(无 metadata)");

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);
