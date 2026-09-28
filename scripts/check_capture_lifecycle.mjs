// 校验 content.js 的**实例生命周期**（M42）：可接管守卫 + 僵尸实例自愈 + 失败路径收框。
//
// 为什么必须有这条门禁：这几个语义**只在浏览器里跑**，Python 侧读源码最多证明
// 「写了 `if (previous.alive())`」这行字，证明不了「活实例真的被拦下」「僵尸实例真的
// 被拆干净」。而它们正是维护者 2026-09-28 报障的现场：
//   「不会在已经打开的网页上生效」—— 扩展装载/重载后，已打开的标签页拿不到新脚本，
//     background 现在会对这类标签页补注入；补注入是否**幂等**（不重复安装）、是否**能
//     接管僵尸**，只能在这里钉住。
//   「捕获后网页上红框还在」—— 僵尸实例收不到 capture_disarm，而失败路径原先「保留红框」。
// 做法与 check_capture_helpers.mjs 一致：桩 window/document/chrome，整文件求值后按场景断言。
//
// 用法：node scripts/check_capture_lifecycle.mjs   （退出码非 0 = 有失败）
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "extension", "content.js"), "utf8");

const INSTANCE_KEY = "__rpaCaptureInstance";
// 期望值**独立写死**（不从被测源码里读）：判据若从被测代码取常量，改错了也照样绿。
const CURSOR_STYLE_ID = "rpa-capture-cursor";

let failed = 0;
const check = (label, got, expected) => {
  const ok = JSON.stringify(got) === JSON.stringify(expected);
  if (!ok) failed += 1;
  console.log(
    `${ok ? "PASS" : "FAIL"} | ${label}${ok ? "" : ` → got ${JSON.stringify(got)} want ${JSON.stringify(expected)}`}`,
  );
};

// 0) 整文件语法校验
try {
  new Function(source);
} catch (err) {
  console.error(`FAIL: content.js 语法错误 → ${err.message}`);
  process.exit(1);
}

// ---------------------------------------------------------------- 桩环境
// 事件面：每个 target 自己维护 type → Set(handler)。之所以要真的记账而不是「留个空壳」，
// 是因为**僵尸实例必须被真正摘掉监听器**——只靠 onMove 早退会把「监听器还在抢手势」
// 这个缺陷盖住。
const makeEmitter = (label) => ({
  label,
  handlers: new Map(),
  addEventListener(type, handler) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type).add(handler);
  },
  removeEventListener(type, handler) {
    const set = this.handlers.get(type);
    if (set) set.delete(handler);
  },
});

const makeWorld = () => {
  const html = makeEmitter("html");
  html.children = [];
  html.appendChild = (child) => { html.children.push(child); return child; };
  const documentEmitter = makeEmitter("document");
  const windowEmitter = makeEmitter("window");

  const makeNode = () => {
    const el = {
      textContent: "",
      style: {},
      remove() {
        const i = html.children.indexOf(el);
        if (i >= 0) html.children.splice(i, 1);
      },
      getBoundingClientRect: () => ({ x: 0, y: 0, left: 0, top: 0, width: 10, height: 10 }),
      getAttribute: () => null,
      hasAttribute: () => false,
    };
    return el;
  };

  const hoverTarget = {
    // nodeType 必须有：pathFor / cssSelectorFor 的上溯循环判 `node.nodeType === 1`，
    // 缺了它循环一次都不跑、直接返回空（此前没断言过 css 内容，所以一直没暴露）。
    nodeType: 1,
    tagName: "BUTTON",
    id: "",
    className: "",
    type: undefined,
    isContentEditable: false,
    labels: [],
    childNodes: [],
    textContent: "hi",
    children: [],
    parentElement: null,
    getAttribute: () => null,
    hasAttribute: () => false,
    closest: () => null,
    getBoundingClientRect: () => ({ x: 0, y: 0, left: 0, top: 0, width: 10, height: 10 }),
  };

  const document = {
    title: "t",
    documentElement: html,
    createElement: makeNode,
    addEventListener: (t, h) => documentEmitter.addEventListener(t, h),
    removeEventListener: (t, h) => documentEmitter.removeEventListener(t, h),
    querySelectorAll: () => [],
    getElementById: () => null,
    elementsFromPoint: () => [hoverTarget],
  };
  const window = {
    innerWidth: 1280,
    innerHeight: 800,
    addEventListener: (t, h) => windowEmitter.addEventListener(t, h),
    removeEventListener: (t, h) => windowEmitter.removeEventListener(t, h),
  };

  const emitters = [html, documentEmitter, windowEmitter];
  return {
    window,
    document,
    html,
    documentEmitter,
    // 「页面上还剩几个覆盖层节点」：box 与 hint 都挂在 documentElement 下
    overlays: () => html.children.length,
    // 捕获光标样式节点（按下 Ctrl/⌘ 时注入的 <style>）：是否残留、是否叠加看它
    cursorStyles: () => html.children.filter((n) => n.id === CURSOR_STYLE_ID).length,
    cursorStyleText: () => {
      const node = html.children.find((n) => n.id === CURSOR_STYLE_ID);
      return node ? node.textContent : "";
    },
    // 净监听器数（注册 - 摘除），teardown 是否真的拆干净看它
    listeners: () => emitters.reduce(
      (sum, e) => sum + [...e.handlers.values()].reduce((n, s) => n + s.size, 0), 0,
    ),
    fire: (emitter, type, event) => {
      for (const fn of [...(emitter.handlers.get(type) || [])]) fn({ type, ...event });
    },
    move: (x = 5, y = 5) => {
      for (const fn of [...(documentEmitter.handlers.get("mousemove") || [])]) {
        fn({ type: "mousemove", clientX: x, clientY: y });
      }
    },
  };
};

// chrome 桩：`id` 为空即模拟「扩展被重载 ⇒ 上下文失效」。sendMessage 在失效后
// **同步抛**（真机行为：Extension context invalidated），这正是 content.js 里
// `try/catch` 与 `!chrome.runtime.id` 两条失败分支的输入。
const makeChrome = () => {
  const messageListeners = new Set();
  const sent = [];
  const chrome = {
    runtime: {
      id: "rpa-ext-id",
      onMessage: {
        addListener: (fn) => messageListeners.add(fn),
        removeListener: (fn) => messageListeners.delete(fn),
      },
      sendMessage: (msg) => {
        if (!chrome.runtime.id) throw new Error("Extension context invalidated.");
        sent.push(msg);
        if (msg && msg.type === "rpa-capture-state") return Promise.resolve({ armed: false });
        return Promise.resolve({ ok: true });
      },
    },
  };
  return { chrome, messageListeners, sent };
};

// 平台只影响**提示文案**（macOS 上手势是 ⌘ 而不是 Ctrl）：给 Mac 用户提示 Ctrl 等于
// 提示一个不会生效的手势。文案与手势判定分开钉——文案是字符串，这里真求值一次。
const install = (world, state, userAgent = "Mozilla/5.0 (Windows NT 10.0)") => new Function(
  "window", "document", "chrome", "navigator", "CSS", "location", source,
)(
  world.window, world.document, state.chrome,
  { userAgent }, { escape: (v) => String(v) },
  { href: "https://example.test/" },
);

const arm = (state, armed = true) => {
  for (const fn of [...state.messageListeners]) {
    fn({ type: "rpa-capture-arm", armed });
  }
};

const gesture = (world, type = "click", extra = {}) => {
  world.fire(world.documentEmitter, type, {
    clientX: 5, clientY: 5, ctrlKey: true, metaKey: false, button: 0,
    preventDefault() {}, stopPropagation() {}, ...extra,
  });
};

const aliveOf = (world) => Boolean(
  world.window[INSTANCE_KEY] && world.window[INSTANCE_KEY].alive(),
);

// 单实例应有的净监听器总数。**三处断言共用它**：新增一个监听只需改这里一处，
// 而「接管后不叠加」仍然被钉住（数字自身不漂）。
// 组成：3 × onLeave（mouseleave / blur / scroll）+ 5 × 手势
//（mousemove / click / mousedown / contextmenu / keydown）+ 1 × keyup（光标还原）。
const EXPECTED_LISTENERS = 9;

// ---------------------------------------------------------------- 场景 1：首次安装
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  check("S1 首次安装：净监听器就位（3 × onLeave + 5 × 手势 + 1 × keyup）",
    world.listeners(), EXPECTED_LISTENERS);
  check("S1 首次安装：runtime.onMessage 监听就位", state.messageListeners.size, 1);
  check("S1 首次安装：登记本实例", typeof world.window[INSTANCE_KEY], "object");
  check("S1 首次安装：登记时是活的", aliveOf(world), true);
  check("S1 首次安装：启动即查当前捕获态", state.sent, [{ type: "rpa-capture-state" }]);
  check("S1 首次安装：未 arm 时不画框", world.overlays(), 0);
}

// ---------------------------------------------------------------- 场景 2：重复注入幂等
// background 补注入会与 onUpdated 的补发、后续多次 arm 撞车；活实例必须拦下新脚本，
// 否则同一个页面会有两个实例各自画框、各自回传。
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  const first = world.window[INSTANCE_KEY];
  install(world, state);
  check("S2 重复注入（同上下文）：不重装，监听器数不变",
    world.listeners(), EXPECTED_LISTENERS);
  check("S2 重复注入（同上下文）：实例对象未被替换", world.window[INSTANCE_KEY] === first, true);
  check("S2 重复注入（同上下文）：runtime 监听未叠加", state.messageListeners.size, 1);
}

// ---------------------------------------------------------------- 场景 3：僵尸实例自愈
// 扩展重载后本实例与扩展断链：它既捕获不到，也收不到 disarm。若它继续画框，红框就
// 永久赖在页面上（维护者报障）。所以鼠标一动必须先把自己那一层收掉。
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  world.move();
  check("S3 捕获态下鼠标移动：画出红框 + 提示条", world.overlays(), 2);
  state.chrome.runtime.id = undefined;   // 扩展被重载
  check("S3 扩展重载后：实例自认已死", aliveOf(world), false);
  world.move(40, 40);
  check("S3 扩展重载后鼠标移动：不再留框（覆盖层收干净）", world.overlays(), 0);
}

// ---------------------------------------------------------------- 场景 4：僵尸被接管
// 补注入的新脚本必须能**接管**僵尸：旧监听器要真被摘掉（否则旧实例继续抢手势），
// 旧红框要真被清掉，新实例要能重新工作。
{
  const world = makeWorld();
  const dead = makeChrome();
  install(world, dead);
  arm(dead, true);
  world.move();
  check("S4 接管前：僵尸实例有框", world.overlays(), 2);

  dead.chrome.runtime.id = undefined;   // 旧实例变成僵尸
  const fresh = makeChrome();
  install(world, fresh);                // background 补注入
  check("S4 接管：旧监听器被摘干净（不叠加）", world.listeners(), EXPECTED_LISTENERS);
  check("S4 接管：旧红框被清掉", world.overlays(), 0);
  check("S4 接管：新实例已登记且是活的", aliveOf(world), true);
  check("S4 接管：新实例查了一次捕获态", fresh.sent, [{ type: "rpa-capture-state" }]);

  arm(fresh, true);
  world.move();
  check("S4 接管后：重新可用（能画框）", world.overlays(), 2);
}

// ---------------------------------------------------------------- 场景 5：失败路径收框
// 「捕获不到时红框还留着」是报障的另一面：失败提示要有，但红框不能赖着不走。
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  world.move();
  check("S5 捕获前：有框", world.overlays(), 2);

  state.chrome.runtime.id = undefined;   // 点下去的瞬间通道已失效
  gesture(world, "click");
  check("S5 通道失效时点捕获：红框被收掉（只剩提示条）", world.overlays(), 1);
  check("S5 通道失效时点捕获：提示条说明原因",
    world.html.children[0].textContent.includes("扩展已重载"), true);
  check("S5 通道失效时点捕获：未把内容上报出去", state.sent.length, 1);
}

// ---------------------------------------------------------------- 场景 6：捕获手势的两条路径
// 这是原先只在 Python 侧「读源码证明写了 addEventListener(...)」的那半，搬到这里真求值。
// 语义要求来自真机：macOS 在**系统层**把 Control+Click 改写成「次要点击」，浏览器只派发
// contextmenu（且 button 未必是 2，常见 0）——只挂 click 或只判 button===2 的实现在 Mac 上
// 必然「红框跟着鼠标走，怎么点都捕获不到」。
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  gesture(world, "contextmenu", { ctrlKey: false, metaKey: false, button: 0 });
  check("S6 macOS 路径：contextmenu（button=0、无修饰键）也能捕获",
    state.sent.filter((m) => m.type === "rpa-capture-result").length, 1);
}
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  gesture(world, "mousedown", { ctrlKey: false, metaKey: false, button: 2 });
  check("S7 右键路径：mousedown(button=2) 也能捕获",
    state.sent.filter((m) => m.type === "rpa-capture-result").length, 1);
}
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  gesture(world, "click", { ctrlKey: true, metaKey: false });
  check("S8 Windows/Linux 路径：Ctrl+左键能捕获",
    state.sent.filter((m) => m.type === "rpa-capture-result").length, 1);
}
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  gesture(world, "click", { ctrlKey: false, metaKey: false });
  check("S9 无修饰键的普通单击不算捕获（只回显诊断）",
    state.sent.filter((m) => m.type === "rpa-capture-result").length, 0);
}

// ---------------------------------------------------------------- 场景 10：手势提示按平台走
// 给 Mac 用户提示「Ctrl + 单击」等于提示一个不会生效的手势（见 S6）。
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state, "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)");
  arm(state, true);
  world.move();
  const texts = world.html.children.map((c) => c.textContent).filter(Boolean).join(" | ");
  check("S10 macOS 提示条用 ⌘（不能写死 Ctrl）", texts.includes("⌘"), true);
}
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  world.move();
  const texts = world.html.children.map((c) => c.textContent).filter(Boolean).join(" | ");
  check("S10 Windows 提示条用 Ctrl", texts.includes("Ctrl"), true);
}

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
// --------------------------------------------- 场景 11：捕获光标（按 Ctrl 未点击即变蓝）
// 语义只在浏览器里成立（改的是页面的 cursor），Python 读源码只能证明「写了这行字」。
// 要点是**可逆**与**不叠加**：残留一个 <style> 会让光标赖在整个站点的每个元素上。
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  check("S11 未按修饰键：没有光标样式", world.cursorStyles(), 0);
  world.fire(world.documentEmitter, "keydown", { key: "Control" });
  check("S11 按下 Ctrl：注入光标样式", world.cursorStyles(), 1);
  const css = world.cursorStyleText();
  check("S11 光标是蓝色箭头", css.includes("2F6BFF"), true);
  check("S11 带 !important 压过站点自己的 cursor 规则",
    css.includes("!important") && css.includes("* { cursor:"), true);
  world.fire(world.documentEmitter, "keydown", { key: "Control" });
  check("S11 按住 Ctrl 连发 keydown：不叠加", world.cursorStyles(), 1);
  world.fire(world.documentEmitter, "keyup", { key: "Control" });
  check("S11 松开 Ctrl：光标还原", world.cursorStyles(), 0);
}
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  world.fire(world.documentEmitter, "keydown", { key: "Control" });
  check("S11 非捕获态按 Ctrl：不注入（不打扰正常浏览）", world.cursorStyles(), 0);
}
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  world.fire(world.documentEmitter, "keydown", { key: "Meta" });
  check("S11 macOS ⌘ 与 Ctrl 同口径（也变蓝）", world.cursorStyles(), 1);
}
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  world.fire(world.documentEmitter, "keydown", { key: "Control" });
  arm(state, false);
  check("S11 按住 Ctrl 时撤防：光标跟着走（不赖在页面上）", world.cursorStyles(), 0);
}
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  world.fire(world.documentEmitter, "keydown", { key: "Control" });
  world.fire(world.documentEmitter, "keydown", { key: "Escape" });
  check("S11 按住 Ctrl 时按 Esc：光标还原", world.cursorStyles(), 0);
}
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  world.fire(world.documentEmitter, "keydown", { key: "Control" });
  gesture(world, "click", { ctrlKey: true, metaKey: false });
  check("S11 按住 Ctrl 完成捕获：光标还原", world.cursorStyles(), 0);
}
{
  // 僵尸接管：旧实例若把 <style> 留在页面上，新实例的接管路径必须把它清掉。
  // （接管只调得到 teardown，不是新实例自己重扫一遍页面。）
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  world.fire(world.documentEmitter, "keydown", { key: "Control" });
  state.chrome.runtime.id = "";
  install(world, state);
  check("S11 接管僵尸实例：残留光标被清掉", world.cursorStyles(), 0);
}

// --------------------------------------------- 场景 12：祖先链回传（节点树的原料）
// 影刀的节点树就是「根 → 目标」的逐级路径。我们的 css 本来就走这条链，但从没回传过
// —— 用户看不到路径、也没法逐级增删。pathFor 是唯一的片段生成处，css 由它 join 而来，
// 所以两者**必然同源**：树上显示的路径与真正下发执行的选择器是同一条。
{
  const world = makeWorld();
  const state = makeChrome();
  install(world, state);
  arm(state, true);
  gesture(world, "click", { ctrlKey: true, metaKey: false });
  const msg = state.sent.find((m) => m.type === "rpa-capture-result");
  const selector = (msg && msg.descriptor && msg.descriptor.selector) || {};
  const path = selector.path;
  check("S12 捕获回传 selector.path（祖先链）",
    Array.isArray(path) && path.length >= 1, true);
  check("S12 path 每级带 tag 与 fragment",
    Array.isArray(path)
      && path.every((e) => typeof e.tag === "string" && typeof e.fragment === "string"), true);
  check("S12 path 末级 fragment 与主 css 同源",
    Array.isArray(path) && path.length > 0
      && path[path.length - 1].fragment === selector.css, true);
}

process.exit(failed ? 1 : 0);
