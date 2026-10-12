// 校验扩展执行通道的「地址前缀补全」纯函数区（M53）。
// 做法与 check_input_helpers.mjs 一致：从 background.js 按锚点切片，求值后断言矩阵。
// 背景：`browser.getText` 这一轮补了影刀「获取元素信息」的两处参数缺口——「获取元素属性」
// （任意属性名）与「智能识别并补充地址前缀(http:// 或 https://)」。补前缀是**纯字符串语义**
// （五种形态各有归处），只在浏览器里跑；Python 侧读源码证明不了「`/next` 会不会被拼成
// `https:///next`」，所以按仓库惯例用 node 切片把它钉死。
// 用法：node scripts/check_url_prefix_helpers.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const source = readFileSync(join(root, "extension", "background.js"), "utf8");

try {
  new Function(source);
} catch (err) {
  console.error(`FAIL: background.js 语法错误 → ${err.message}`);
  process.exit(1);
}

const START = "// [url-prefix-helpers:start]";
const END = "// [url-prefix-helpers:end]";
const start = source.indexOf(START);
const end = source.indexOf(END);
if (start < 0 || end < 0 || end <= start) {
  console.error(`FAIL: 未能定位纯函数区（锚点 ${START} / ${END}）`);
  process.exit(1);
}
const slice = source.slice(start, end);

const { completeUrlPrefix } = new Function(
  `${slice}\nreturn { completeUrlPrefix };`,
)();

let failed = 0;
const check = (label, actual, expected) => {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failed += 1;
    console.error(`FAIL | ${label}: 期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`);
  } else {
    console.log(`PASS | ${label}`);
  }
};

// ---- completeUrlPrefix(raw, resolved, pageProtocol) ----
// ① 已有协议头 → 原样
check("已有 https 原样", completeUrlPrefix("https://a.com/x", undefined, "https:"), "https://a.com/x");
check("已有 http 原样", completeUrlPrefix("http://a.com", undefined, "https:"), "http://a.com");
check("非 web 协议（mailto）原样", completeUrlPrefix("mailto:a@b.com", undefined, "https:"), "mailto:a@b.com");
check("非 web 协议（data）原样", completeUrlPrefix("data:text/plain,x", undefined, "https:"), "data:text/plain,x");
// ② 空值 → 空串（不猜测）
check("空串 → 空串", completeUrlPrefix("", "https://h/x", "https:"), "");
check("纯空白 → 空串", completeUrlPrefix("   ", "https://h/x", "https:"), "");
check("null → 空串", completeUrlPrefix(null, "https://h/x", "https:"), "");
check("undefined → 空串", completeUrlPrefix(undefined, "https://h/x", "https:"), "");
// ③ 协议相对 → 补「页面协议 + :」
check(
  "协议相对 + https 页 → https://",
  completeUrlPrefix("//cdn.a.com/x.png", undefined, "https:"),
  "https://cdn.a.com/x.png",
);
check(
  "协议相对 + http 页 → http://",
  completeUrlPrefix("//cdn.a.com/x.png", undefined, "http:"),
  "http://cdn.a.com/x.png",
);
// ④ 相对引用 → 用浏览器解析后的绝对地址（绝不自己拼 //）
check("根相对 → 用解析值", completeUrlPrefix("/next", "https://h/next", "https:"), "https://h/next");
check("点相对 → 用解析值", completeUrlPrefix("./a.html", "https://h/d/a.html", "https:"), "https://h/d/a.html");
check("上级相对 → 用解析值", completeUrlPrefix("../a.html", "https://h/a.html", "https:"), "https://h/a.html");
check("片段 → 用解析值", completeUrlPrefix("#top", "https://h/p#top", "https:"), "https://h/p#top");
check("查询串 → 用解析值", completeUrlPrefix("?q=1", "https://h/p?q=1", "https:"), "https://h/p?q=1");
check(
  "相对引用拿不到解析值 → 原样（不拼成 https:///next）",
  completeUrlPrefix("/next", "", "https:"),
  "/next",
);
// ⑤ 裸域名/裸文本地址 → 补「页面协议 + //」
check("裸域名 + https 页", completeUrlPrefix("www.baidu.com", undefined, "https:"), "https://www.baidu.com");
check("裸域名 + http 页", completeUrlPrefix("www.baidu.com", undefined, "http:"), "http://www.baidu.com");
check("裸域名带路径", completeUrlPrefix("a.com/p?q=1", undefined, "https:"), "https://a.com/p?q=1");
check("非 http 页（file）一律补 https", completeUrlPrefix("www.a.com", undefined, "file:"), "https://www.a.com");
check("页面协议缺失 → 补 https", completeUrlPrefix("www.a.com", undefined, undefined), "https://www.a.com");
// 首尾空白一律先 trimming
check("首尾空白先 trim", completeUrlPrefix("  www.a.com  ", undefined, "https:"), "https://www.a.com");
check("已有协议也没被 trim 破坏", completeUrlPrefix("  http://a.com  ", undefined, "https:"), "http://a.com");

// ---- 反漂移：接线必须真的接上（纯函数对 ≠ 用上了） ----
const wiring = [
  [
    "只对 infoType=href 生效",
    /info === "href" && args\.completeUrlPrefix/,
    "补前缀会扩到其它 infoType（维护者定案：严格对标影刀「获取网页链接地址」）",
  ],
  [
    "三个入参按 (raw, resolved, pageProtocol) 传入",
    /completeUrlPrefix\(result, el\.href, location\.protocol\)/,
    "解析值/页面协议没传进去 → 相对引用与协议选择会退化成猜测",
  ],
  [
    "attribute 分支读 attributeName",
    /info === "attribute"[\s\S]{0,200}?el\.getAttribute\(String\(args\.attributeName \|\| ""\)\)/,
    "「获取元素属性」没接上 attributeName",
  ],
];
for (const [label, pattern, why] of wiring) {
  if (pattern.test(source)) {
    console.log(`PASS | ${label}`);
  } else {
    failed += 1;
    console.error(`FAIL | ${label} —— ${why}`);
  }
}

// 协议头判据必须与 host 侧同口径（browser.py::_SCHEME_RE）：两边分叉会让
// 「https:// 已带协议」在扩展侧被二次补成 https://https://…
const jsScheme = source.match(/\^\[a-zA-Z\]\[a-zA-Z0-9\+\.\-\]\*:/);
let pyScheme = null;
try {
  const py = readFileSync(join(root, "src", "rpa_core", "executors", "browser.py"), "utf8");
  const m = py.match(/_SCHEME_RE\s*=\s*re\.compile\(\s*r"([^"]+)"\s*\)/);
  pyScheme = m ? m[1] : null;
} catch (err) {
  pyScheme = null;
}
if (!jsScheme) {
  failed += 1;
  console.error("FAIL | 扩展侧未找到协议头正则（应为 ^[a-zA-Z][a-zA-Z0-9+.-]*: 同口径）");
} else if (!pyScheme) {
  failed += 1;
  console.error("FAIL | 未能从 browser.py 解析 _SCHEME_RE：跨端同口径判据无法成立");
} else if (jsScheme[0] !== pyScheme) {
  failed += 1;
  console.error(`FAIL | 协议头正则跨端分叉：扩展 ${jsScheme[0]} vs host ${pyScheme}`);
} else {
  console.log("PASS | 协议头正则与 host 侧 _SCHEME_RE 同口径");
}

if (failed) {
  console.error(`\n地址前缀补全纯函数校验失败：${failed} 项`);
  process.exit(1);
}
console.log("\n地址前缀补全纯函数校验全部通过");
