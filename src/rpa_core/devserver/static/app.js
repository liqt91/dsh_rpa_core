"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  catalog: [],
  workflow: null,
  selected: null,
  multi: [],
  undo: [],
  redo: [],
  clipboard: null,
  dirty: false,
  elementsSeen: null, // 最近一次渲染的元素名集合（捕获自动刷新差集用）
  collapsedGroups: new Set(), // 指令面板已收起的分组名（默认全展开，点击收起）
  extChannel: null, // 最近一次 /api/ext/status 结果 {online, host}
  paramGroupsOpen: new Map(), // 属性面板参数分组的展开状态（key: `${nodeId}|${分组名}`；未记录时按分组缺省/是否已填值判定）
};
let dragState = null; // {type:"new", command} | {type:"new", flow} | {type:"move", path}
let pendingDrop = null; // {containerPath, key, index}
let fxPopupCleanup = null; // fx 变量浮层关闭函数（挂 body 的 fixed 弹层需在面板重渲染前主动关闭）
const NAME_PATTERN = /^[A-Za-z][A-Za-z0-9_-]*$/;
const REFERENCE_HINT = "支持引用：${inputs.x} / ${steps.节点.outputs.键} / ${loop.item} / ${error.code}";
const UNDO_LIMIT = 50;

// ---------------------------------------------------------------------------
// 树模型层：路径 = [{key, index}...]，从 workflow.root 出发；
// key ∈ children | then | else | catch，index 为该分支列表内下标。
// ---------------------------------------------------------------------------

const CONTAINER_LISTS = {
  sequence: ["children"],
  forEach: ["children"],
  if: ["then", "else"],
  try: ["children", "catch"],
};
const BRANCH_LABELS = { children: "主体", then: "满足时", else: "否则", catch: "出错时" };
const FLOW_ITEMS = [
  { type: "sequence", label: "sequence 顺序容器" },
  { type: "if", label: "if 条件分支" },
  { type: "forEach", label: "forEach 循环" },
  { type: "try", label: "try 异常捕获" },
  { type: "return", label: "return 返回" },
];

function listOf(node, key) {
  if (key === "else") return node["else"];
  return node[key];
}

function pathEquals(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  return a.every((step, i) => step.key === b[i].key && step.index === b[i].index);
}

function pathStartsWith(path, prefix) {
  if (path.length < prefix.length) return false;
  return prefix.every((step, i) => step.key === path[i].key && step.index === path[i].index);
}

function findNode(path) {
  let node = state.workflow.root;
  for (const step of path) {
    const list = listOf(node, step.key);
    node = list ? list[step.index] : undefined;
    if (!node) return null;
  }
  return node;
}

function findList(containerPath, key) {
  return listOf(findNode(containerPath), key);
}

function insertNode(containerPath, key, index, node) {
  findList(containerPath, key).splice(index, 0, node);
}

function removeSubtree(path) {
  const last = path[path.length - 1];
  return findList(path.slice(0, -1), last.key).splice(last.index, 1)[0];
}

// 移动子树到目标列表；禁止落入自身子树；同列表后移自动修正下标。
// 返回移动后的新路径，位置未变化或非法时返回 null。
function moveSubtree(srcPath, dstContainerPath, dstKey, dstIndex) {
  if (pathStartsWith(dstContainerPath, srcPath)) return null;
  const node = findNode(srcPath);
  const srcLast = srcPath[srcPath.length - 1];
  const srcContainerPath = srcPath.slice(0, -1);
  const sameList = pathEquals(srcContainerPath, dstContainerPath) && srcLast.key === dstKey;
  let insertAt = dstIndex;
  if (sameList && dstIndex > srcLast.index) insertAt = dstIndex - 1;
  if (sameList && insertAt === srcLast.index) return null;
  removeSubtree(srcPath);
  // 目标容器路径若经过源节点的兄弟列表且在其后，删除后下标前移一位。
  if (
    dstContainerPath.length > srcContainerPath.length &&
    pathEquals(dstContainerPath.slice(0, srcContainerPath.length), srcContainerPath)
  ) {
    const step = dstContainerPath[srcContainerPath.length];
    if (step.key === srcLast.key && step.index > srcLast.index) {
      dstContainerPath = dstContainerPath.slice();
      dstContainerPath[srcContainerPath.length] = { key: step.key, index: step.index - 1 };
    }
  }
  insertNode(dstContainerPath, dstKey, insertAt, node);
  return [...dstContainerPath, { key: dstKey, index: insertAt }];
}

function collectIds(node, set) {
  set.add(node.id);
  for (const key of CONTAINER_LISTS[node.type] || []) {
    for (const child of listOf(node, key)) collectIds(child, set);
  }
  return set;
}

// 全树唯一 id：新建/粘贴生成时必须查全树，不得只查兄弟层。
function uniqueId(base) {
  let id = String(base).replace(/[^A-Za-z0-9_-]/g, "");
  if (!/^[A-Za-z]/.test(id)) id = `n${id || "node"}`;
  const ids = collectIds(state.workflow.root, new Set());
  let candidate = id;
  let n = 2;
  while (ids.has(candidate)) { candidate = `${id}${n}`; n += 1; }
  return candidate;
}

function findPathOf(target) {
  const walk = (node, path) => {
    if (node === target) return path;
    for (const key of CONTAINER_LISTS[node.type] || []) {
      const list = listOf(node, key);
      for (let i = 0; i < list.length; i += 1) {
        const found = walk(list[i], [...path, { key, index: i }]);
        if (found) return found;
      }
    }
    return null;
  };
  return walk(state.workflow.root, []);
}

// ---------------------------------------------------------------------------
// 快照式撤销/重做：每次变更前压栈（workflow + 选中态），上限 50 步。
// ---------------------------------------------------------------------------

function takeSnapshot() {
  return {
    workflow: structuredClone(state.workflow),
    selected: state.selected ? structuredClone(state.selected) : null,
    multi: structuredClone(state.multi),
  };
}

function pushUndo(preSnapshot) {
  state.undo.push(preSnapshot || takeSnapshot());
  if (state.undo.length > UNDO_LIMIT) state.undo.shift();
  state.redo = [];
}

function applySnapshot(snap) {
  state.workflow = snap.workflow;
  state.selected = snap.selected;
  state.multi = snap.multi;
  markDirty();
  render();
}

function undo() {
  if (!state.undo.length) return;
  state.redo.push(takeSnapshot());
  applySnapshot(state.undo.pop());
}

function redo() {
  if (!state.redo.length) return;
  state.undo.push(takeSnapshot());
  applySnapshot(state.redo.pop());
}

// ---------------------------------------------------------------------------
// 选中集（state.multi）：多选以主选中 state.selected 为锚点。
// ---------------------------------------------------------------------------

function pathIn(pathList, path) {
  return pathList.some((p) => pathEquals(p, path));
}

function selectSingle(path) {
  state.selected = path;
  state.multi = [path];
}

function toggleMulti(path) {
  if (pathIn(state.multi, path)) {
    state.multi = state.multi.filter((p) => !pathEquals(p, path));
    if (state.selected && pathEquals(state.selected, path)) {
      state.selected = state.multi.length ? state.multi[state.multi.length - 1] : null;
    }
  } else {
    state.multi.push(path);
    state.selected = path;
  }
  if (!state.multi.length) state.selected = null;
}

function rangeSelect(path) {
  const anchor = state.selected;
  if (!anchor) return selectSingle(path);
  const aLast = anchor[anchor.length - 1];
  const pLast = path[path.length - 1];
  const sameList =
    anchor.length === path.length &&
    pathEquals(anchor.slice(0, -1), path.slice(0, -1)) &&
    aLast.key === pLast.key;
  if (!sameList) return selectSingle(path);
  const [lo, hi] = [Math.min(aLast.index, pLast.index), Math.max(aLast.index, pLast.index)];
  const containerPath = path.slice(0, -1);
  state.multi = [];
  for (let index = lo; index <= hi; index += 1) {
    state.multi.push([...containerPath, { key: pLast.key, index }]);
  }
  state.selected = path;
}

// 渲染前清理失效路径（节点被删/移动后下标漂移）。
function normalizeSelection() {
  state.multi = state.multi.filter((p) => findNode(p));
  if (state.selected && !findNode(state.selected)) state.selected = null;
  if (state.selected && !pathIn(state.multi, state.selected)) state.multi.push(state.selected);
  if (!state.selected && state.multi.length) state.selected = state.multi[state.multi.length - 1];
}

// 结构变更前后用节点对象身份重寻选中集（比按下标修正更稳）。
function captureSelectionNodes() {
  return {
    primary: state.selected ? findNode(state.selected) : null,
    others: state.multi.map((p) => findNode(p)).filter(Boolean),
  };
}

function restoreSelectionPaths(sel) {
  state.multi = sel.others.map((node) => findPathOf(node)).filter(Boolean);
  const primaryPath = sel.primary ? findPathOf(sel.primary) : null;
  state.selected = primaryPath || state.multi[state.multi.length - 1] || null;
  if (state.selected && !pathIn(state.multi, state.selected)) state.multi.push(state.selected);
}

// ---------------------------------------------------------------------------
// 子树复制：内部剪贴板格式 {version: 1, nodes: [...]}，粘贴时全树 id 重映射。
// ---------------------------------------------------------------------------

function remapSubtreeIds(node) {
  const base = String(node.id).replace(/\d+$/, "");
  node.id = uniqueId(base);
  for (const key of CONTAINER_LISTS[node.type] || []) {
    for (const child of listOf(node, key)) remapSubtreeIds(child);
  }
}

// 收集整棵树已占用的变量名（output_aliases 别名 + data.setVar 的 varName 字面量）。
function collectUsedVarNames() {
  const used = new Set();
  const visit = (node) => {
    if (!node) return;
    if (node.type === "action") {
      for (const alias of Object.values(node.output_aliases || {})) {
        if (alias) used.add(alias);
      }
      const manifest = manifestOf(node.command);
      const varWrite = manifest && manifest["x-var-write"];
      if (varWrite && varWrite.field) {
        const target = node["with"] ? node["with"][varWrite.field] : undefined;
        if (typeof target === "string" && target && !target.startsWith("${")) used.add(target);
      }
    }
    for (const key of CONTAINER_LISTS[node.type] || []) {
      for (const child of listOf(node, key) || []) visit(child);
    }
  };
  if (state.workflow && state.workflow.root) visit(state.workflow.root);
  return used;
}

// 粘贴时重命名撞车别名：旧名 → 新名映射，并同步改写子树内对旧名的引用。
// 别名是扁平作用域，重复声明会在编译期被拦截（Duplicate alias），
// 这里在粘贴阶段就消除冲突，避免用户复制节点后必然撞车。
function remapSubtreeAliases(node, sharedUsed) {
  const used = sharedUsed || collectUsedVarNames();
  const rename = new Map(); // 旧别名 -> 新别名

  const makeUnique = (base) => {
    let candidate = `${base}_copy`;
    let n = 2;
    while (used.has(candidate)) {
      candidate = `${base}_copy${n}`;
      n += 1;
    }
    used.add(candidate);
    return candidate;
  };

  // 第一遍：登记需要重命名的别名
  const collect = (n) => {
    if (!n) return;
    if (n.type === "action") {
      for (const [field, alias] of Object.entries(n.output_aliases || {})) {
        if (alias && used.has(alias)) {
          const fresh = makeUnique(alias);
          rename.set(alias, fresh);
          n.output_aliases[field] = fresh;
        }
      }
    }
    for (const key of CONTAINER_LISTS[n.type] || []) {
      for (const child of listOf(n, key) || []) collect(child);
    }
  };
  collect(node);

  // 第二遍：改写子树内引用了被重命名别名的 ${...}
  if (rename.size) {
    const rewriteRefs = (value) => {
      if (typeof value === "string") {
        return value.replace(/\$\{([A-Za-z_]\w*)((?:\.\w+)*)\}/g, (whole, root, rest) =>
          rename.has(root) ? `\${${rename.get(root)}${rest}}` : whole
        );
      }
      if (Array.isArray(value)) return value.map(rewriteRefs);
      if (value && typeof value === "object") {
        for (const key of Object.keys(value)) value[key] = rewriteRefs(value[key]);
        return value;
      }
      return value;
    };
    const rewriteNode = (n) => {
      if (!n) return;
      if (n.type === "action") {
        n["with"] = rewriteRefs(n["with"] || {});
      }
      for (const key of CONTAINER_LISTS[n.type] || []) {
        for (const child of listOf(n, key) || []) rewriteNode(child);
      }
    };
    rewriteNode(node);
  }
  return rename;
}

function copySelection() {
  if (!state.multi.length) return;
  const nodes = state.multi.map((p) => structuredClone(findNode(p)));
  state.clipboard = { version: 1, nodes };
  showCompileMessage(`已复制 ${nodes.length} 个节点`, true);
}

function pasteClipboard() {
  if (!state.clipboard || !state.clipboard.nodes.length) return;
  const pre = takeSnapshot();
  let containerPath = [];
  let key = "children";
  let index = state.workflow.root.children.length;
  if (state.selected) {
    const last = state.selected[state.selected.length - 1];
    containerPath = state.selected.slice(0, -1);
    key = last.key;
    index = last.index + 1;
  }
  // 别名规划需在插入前统一完成：多个待粘贴节点共享同一份已占用集合，
  // 否则逐个插入会让后一个节点误判前一个刚写入的别名是否冲突。
  const usedVars = collectUsedVarNames();
  const allRenamed = [];
  let lastPath = null;
  for (const source of state.clipboard.nodes) {
    const node = structuredClone(source);
    remapSubtreeIds(node);
    const renamed = remapSubtreeAliases(node, usedVars);
    for (const [from, to] of renamed) allRenamed.push(`${from} → ${to}`);
    insertNode(containerPath, key, index, node);
    lastPath = [...containerPath, { key, index }];
    index += 1;
  }
  pushUndo(pre);
  selectSingle(lastPath);
  markDirty();
  render();
  if (allRenamed.length) {
    // 明确告知用户别名被重命名，避免"复制后变量名悄悄变了"的困惑
    showCompileMessage(`已粘贴；为避免重名，变量已重命名：${allRenamed.join("、")}`, true);
  }
}

// ---------------------------------------------------------------------------
// 节点构造
// ---------------------------------------------------------------------------

function withDefaults(manifest) {
  const props = (manifest && manifest.input_schema && manifest.input_schema.properties) || {};
  const result = {};
  for (const [key, schema] of Object.entries(props)) {
    if (schema && schema.default !== undefined) result[key] = schema.default;
  }
  return result;
}

function newActionNode(command) {
  return {
    type: "action",
    id: uniqueId(command.split(".").pop()),
    command,
    with: withDefaults(manifestOf(command)),
  };
}

function newFlowNode(type) {
  const id = uniqueId(type);
  if (type === "sequence") return { type: "sequence", id, children: [] };
  if (type === "if") return { type: "if", id, condition: { op: "truthy", left: "" }, then: [], else: [] };
  if (type === "forEach") return { type: "forEach", id, items: [], item_var: "item", children: [] };
  if (type === "try") return { type: "try", id, children: [], catch: [], error_var: "error" };
  return { type: "return", id, value: null };
}

// ---------------------------------------------------------------------------
// 基础工具
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const res = await fetch(path, options);
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) throw new Error(data.message || data.error || `HTTP ${res.status}`);
  return data;
}

const manifestOf = (command) => state.catalog.find((c) => c.id === command);

// ---------------------------------------------------------------------------
// 变量补全：属性表单输入 ${ 时提示可引用路径（inputs / 各节点 outputs / loop / error）
// ---------------------------------------------------------------------------

// 收集流程中所有用户变量（别名 + 变量写入命令的 varName 字面量）。
// 返回 [{name, source}]，source 用于下拉分组展示（如「用户变量」）。
function collectUserVariables() {
  const found = new Map(); // name -> source 描述
  const visit = (node) => {
    if (!node) return;
    if (node.type === "action") {
      for (const alias of Object.values(node.output_aliases || {})) {
        if (alias && !found.has(alias)) found.set(alias, "输出别名");
      }
      const manifest = manifestOf(node.command);
      const varWrite = manifest && manifest["x-var-write"];
      if (varWrite && varWrite.field) {
        const target = node["with"] ? node["with"][varWrite.field] : undefined;
        if (typeof target === "string" && target && !target.startsWith("${") && !found.has(target)) {
          found.set(target, "变量赋值");
        }
      }
    }
    for (const key of CONTAINER_LISTS[node.type] || []) {
      for (const child of listOf(node, key) || []) visit(child);
    }
  };
  if (state.workflow && state.workflow.root) visit(state.workflow.root);
  return [...found.entries()].map(([name, source]) => ({ name, source }));
}

function computeReferencePaths() {
  const paths = [];
  if (!state.workflow) return paths;
  for (const key of Object.keys(state.workflow.inputs || {})) {
    paths.push(`\${inputs.${key}}`);
  }
  // 用户变量优先展示：别名/赋值变量是用户可读的主路径，直接 ${name} 即可引用
  for (const v of collectUserVariables()) {
    paths.push(`\${${v.name}}`);
  }
  const walk = (node) => {
    if (!node) return;
    if (node.type === "action") {
      const manifest = manifestOf(node.command);
      const outputs = (manifest && manifest.output_schema
        && manifest.output_schema.properties) || {};
      for (const key of Object.keys(outputs)) {
        paths.push(`\${steps.${node.id}.outputs.${key}}`);
      }
    }
    for (const key of CONTAINER_LISTS[node.type] || []) {
      for (const child of listOf(node, key) || []) walk(child);
    }
    if (node.type === "forEach") paths.push(`\${loop.${node.item_var || "item"}}`);
    if (node.type === "try") paths.push(`\${${node.error_var || "error"}.code}`);
  };
  walk(state.workflow.root);
  return paths;
}

// 给输入框挂 ${ 触发的引用补全下拉
function attachRefCompletion(input) {
  let dropdown = null;
  const closeDropdown = () => { if (dropdown) { dropdown.remove(); dropdown = null; } };
  input.addEventListener("input", () => {
    const cursor = input.selectionStart || 0;
    const before = input.value.slice(0, cursor);
    const match = before.match(/\$\{([\w.]*)$/);
    if (!match) { closeDropdown(); return; }
    const partial = match[1];
    const candidates = computeReferencePaths().filter((p) =>
      p.toLowerCase().includes(partial.toLowerCase()));
    if (!candidates.length) { closeDropdown(); return; }
    if (!dropdown) {
      dropdown = document.createElement("div");
      dropdown.className = "ref-completion";
      input.parentElement.style.position = "relative";
      input.parentElement.appendChild(dropdown);
    }
    dropdown.textContent = "";
    for (const path of candidates.slice(0, 8)) {
      const item = document.createElement("div");
      item.className = "ref-item";
      item.textContent = path;
      item.addEventListener("mousedown", (e) => {
        e.preventDefault();
        const after = input.value.slice(cursor);
        input.value = before.slice(0, before.length - match[0].length) + path + after;
        input.dispatchEvent(new Event("input"));
        closeDropdown();
      });
      dropdown.appendChild(item);
    }
  });
  input.addEventListener("blur", () => setTimeout(closeDropdown, 150));
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDropdown(); });
}

function markDirty() {
  state.dirty = true;
  $("dirty-badge").classList.remove("hidden");
  $("valid-badge").classList.add("hidden");
}

function clearDirty() {
  state.dirty = false;
  $("dirty-badge").classList.add("hidden");
}

function newWorkflow() {
  state.workflow = {
    schema_version: "1.0",
    id: "draft",
    name: "未命名工作流",
    inputs: {},
    root: { type: "sequence", id: "root", children: [] },
  };
  state.selected = null;
  state.multi = [];
  state.undo = [];
  state.redo = [];
  $("file-name").value = "";
  clearDirty();
  render();
  loadElements();
}

async function refreshOpenList(selectedValue) {
  const data = await api("GET", "/api/workflows");
  const select = $("open-select");
  select.textContent = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "打开…";
  select.appendChild(placeholder);
  for (const name of data.workflows) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  }
  select.value = selectedValue || "";
}

// ---------------------------------------------------------------------------
// 命令面板：控制流分组 + 按 manifest id 首段前缀分组（中文显示名 + 英文副标）
// ---------------------------------------------------------------------------

const I18N = window.RPA_I18N;
const ICONS = window.RPA_ICONS;
const commandName = (id) => (I18N && I18N.commands[id]) || id;
const kindName = (kind) => (I18N && I18N.kinds[kind]) || kind;
const flowName = (type) => (I18N && I18N.flow[type]) || type;
const GROUP_ICONS = { browser: "browser", data: "data", desktop: "desktop", desktop_win32: "window", flow: "branch" };
const KIND_ICON = { action: "play", query: "browser", transform: "transform", lifecycle: "window" };
const FLOW_ICON = { sequence: "play", if: "branch", forEach: "loop", try: "shield", return: "back" };

// 语义类别（参考隔壁仿影刀指令展示）：按"想做什么"分组，替代机械的 id 前缀分组。
// 条目仍按 kind 分色；类别 header 用类别图标 + 分类色。未匹配命令兜底按 id 前缀分组。
const CATEGORY_COLORS = { blue: "#0969da", green: "#1a7f37", purple: "#8250df", cyan: "#0a7ea4", orange: "#bc4c00" };
const SEMANTIC_GROUPS = [
  {
    label: "页面导航", icon: "browser", color: "blue",
    member: (id) => ["browser.navigate", "browser.close"].includes(id),
  },
  {
    label: "元素操作", icon: "play", color: "blue",
    member: (id) => ["browser.click", "browser.input", "browser.getText",
                    "browser.queryAll", "browser.waitFor", "browser.hover",
                    "browser.executeScript", "browser.screenshot", "browser.select"].includes(id),
  },
  {
    label: "数据处理", icon: "data", color: "green",
    member: (id) => ["data.writeJson", "data.writeText", "data.format", "data.limit"].includes(id),
  },
  {
    label: "桌面会话", icon: "window", color: "purple",
    member: (id) => ["desktop.attachWindow", "desktop.closeSession",
                    "desktop.win32.attachWindow", "desktop.win32.closeSession"].includes(id),
  },
  {
    label: "桌面窗口", icon: "window", color: "orange",
    member: (id) => ["desktop.activateWindow", "desktop.setWindowState",
                    "desktop.setWindowVisible", "desktop.moveWindow",
                    "desktop.resizeWindow", "desktop.getWindowTitle",
                    "desktop.win32.activateWindow", "desktop.win32.setWindowState",
                    "desktop.win32.setWindowVisible", "desktop.win32.moveWindow",
                    "desktop.win32.resizeWindow", "desktop.win32.getWindowTitle"].includes(id),
  },
  {
    label: "桌面控件", icon: "desktop", color: "cyan",
    member: (id) => id.startsWith("desktop.")
                && !id.endsWith("attachWindow") && !id.endsWith("closeSession")
                && !["desktop.activateWindow", "desktop.setWindowState",
                     "desktop.setWindowVisible", "desktop.moveWindow",
                     "desktop.resizeWindow", "desktop.getWindowTitle",
                     "desktop.win32.activateWindow", "desktop.win32.setWindowState",
                     "desktop.win32.setWindowVisible", "desktop.win32.moveWindow",
                     "desktop.win32.resizeWindow", "desktop.win32.getWindowTitle"].includes(id),
  },
];

function iconSvg(name) {
  const span = document.createElement("span");
  span.className = "icon";
  span.innerHTML = ICONS ? ICONS.get(name) : "";
  return span;
}

function paletteGroup(name, items, list, icon, color, forceExpand) {
  if (!items.length) return;
  const key = name;
  const collapsed = !forceExpand && state.collapsedGroups.has(key);
  const header = document.createElement("li");
  header.className = "palette-group";
  header.dataset.group = key;
  if (color && CATEGORY_COLORS[color]) header.style.color = CATEGORY_COLORS[color];

  const caret = iconSvg("caret");
  caret.className = "palette-caret";
  header.appendChild(caret);

  if (icon) header.appendChild(iconSvg(icon));
  const text = document.createElement("span");
  text.textContent = name;
  header.appendChild(text);

  const children = document.createElement("div");
  children.className = "palette-children";
  if (collapsed) children.classList.add("hidden");
  for (const item of items) children.appendChild(item);

  header.addEventListener("click", () => {
    if (state.collapsedGroups.has(key)) {
      state.collapsedGroups.delete(key);
      children.classList.remove("hidden");
      caret.classList.add("open");
    } else {
      state.collapsedGroups.add(key);
      children.classList.add("hidden");
      caret.classList.remove("open");
    }
  });
  if (!collapsed) caret.classList.add("open");

  list.appendChild(header);
  list.appendChild(children);
}

function flowPaletteItem(flow, filter) {
  const zh = flowName(flow.type);
  if (filter && !zh.toLowerCase().includes(filter) && !flow.type.includes(filter) && !flow.label.toLowerCase().includes(filter)) return null;
  const li = document.createElement("li");
  li.dataset.flow = flow.type;
  li.className = "palette-item";
  const iconWrap = iconSvg(FLOW_ICON[flow.type] || "play");
  iconWrap.classList.add("icon-block", "kind-flow");
  const texts = document.createElement("div");
  texts.className = "item-texts";
  const label = document.createElement("span");
  label.className = "item-title";
  label.textContent = zh;
  const sub = document.createElement("span");
  sub.className = "item-sub";
  sub.textContent = flow.type;
  texts.appendChild(label);
  texts.appendChild(sub);
  li.appendChild(iconWrap);
  li.appendChild(texts);
  const kind = document.createElement("span");
  kind.className = "kind";
  kind.textContent = "控制流";
  li.appendChild(kind);
  li.draggable = true;
  li.addEventListener("dragstart", (e) => {
    dragState = { type: "new", flow: flow.type };
    e.dataTransfer.effectAllowed = "copy";
    e.dataTransfer.setData("text/plain", flow.type);
  });
  li.addEventListener("dragend", handleDragEnd);
  li.addEventListener("click", () => appendFlow(flow.type));
  return li;
}

function commandPaletteItem(manifest, filter) {
  const zh = commandName(manifest.id);
  if (filter && !manifest.id.toLowerCase().includes(filter) && !zh.toLowerCase().includes(filter)) return null;
  const li = document.createElement("li");
  li.dataset.command = manifest.id;
  li.className = "palette-item";
  const iconWrap = iconSvg(KIND_ICON[manifest.kind] || "data");
  iconWrap.classList.add("icon-block", `kind-${manifest.kind}`);
  const texts = document.createElement("div");
  texts.className = "item-texts";
  const label = document.createElement("span");
  label.className = "item-title";
  label.textContent = zh;
  const sub = document.createElement("span");
  sub.className = "item-sub";
  sub.textContent = manifest.id;
  texts.appendChild(label);
  texts.appendChild(sub);
  li.appendChild(iconWrap);
  li.appendChild(texts);
  const kind = document.createElement("span");
  kind.className = "kind";
  kind.textContent = kindName(manifest.kind);
  kind.title = I18N && I18N.glossary[manifest.kind];
  li.appendChild(kind);
  li.draggable = true;
  li.addEventListener("dragstart", (e) => {
    dragState = { type: "new", command: manifest.id };
    e.dataTransfer.effectAllowed = "copy";
    e.dataTransfer.setData("text/plain", manifest.id);
  });
  li.addEventListener("dragend", handleDragEnd);
  li.addEventListener("click", () => appendAction(manifest.id));
  return li;
}

function renderPalette() {
  const filter = $("palette-filter").value.trim().toLowerCase();
  const list = $("palette-list");
  list.textContent = "";
  const forceExpand = !!filter; // 搜索时强制所有分组展开（参考隔壁）
  const flowItems = FLOW_ITEMS.map((f) => flowPaletteItem(f, filter)).filter(Boolean);
  paletteGroup("控制流", flowItems, list, "branch", null, forceExpand);

  // 语义类别分组（参考隔壁）：控制流之后按"想做什么"分组；未匹配的兜底按 id 前缀。
  const assigned = new Set();
  for (const group of SEMANTIC_GROUPS) {
    const items = [];
    for (const manifest of state.catalog) {
      if (assigned.has(manifest.id)) continue;
      if (!group.member(manifest.id)) continue;
      const item = commandPaletteItem(manifest, filter);
      if (item) items.push(item);
      assigned.add(manifest.id);
    }
    paletteGroup(group.label, items, list, group.icon, group.color, forceExpand);
  }
  // 兜底：未归入语义类别的命令按 id 前缀分组
  const groups = new Map();
  for (const manifest of state.catalog) {
    if (assigned.has(manifest.id)) continue;
    const prefix = manifest.id.split(".")[0];
    if (!groups.has(prefix)) groups.set(prefix, []);
    const item = commandPaletteItem(manifest, filter);
    if (item) groups.get(prefix).push(item);
  }
  for (const [prefix, items] of groups) paletteGroup(prefix, items, list, GROUP_ICONS[prefix], null, forceExpand);
}

// ---------------------------------------------------------------------------
// 画布操作（全部走树模型层）
// ---------------------------------------------------------------------------

function rootEndDrop() {
  return { containerPath: [], key: "children", index: state.workflow.root.children.length };
}

function appendAction(command) {
  const drop = rootEndDrop();
  pushUndo();
  insertNode(drop.containerPath, drop.key, drop.index, newActionNode(command));
  selectSingle([...drop.containerPath, { key: drop.key, index: drop.index }]);
  markDirty();
  render();
}

function appendFlow(type) {
  const drop = rootEndDrop();
  pushUndo();
  insertNode(drop.containerPath, drop.key, drop.index, newFlowNode(type));
  selectSingle([...drop.containerPath, { key: drop.key, index: drop.index }]);
  markDirty();
  render();
}

function moveNode(path, delta) {
  const last = path[path.length - 1];
  const containerPath = path.slice(0, -1);
  const list = findList(containerPath, last.key);
  const target = last.index + delta;
  if (target < 0 || target >= list.length) return;
  const sel = captureSelectionNodes();
  pushUndo();
  const [node] = list.splice(last.index, 1);
  list.splice(target, 0, node);
  restoreSelectionPaths(sel);
  markDirty();
  render();
}

function removeNode(path) {
  const node = findNode(path);
  if (CONTAINER_LISTS[node.type] && !confirm("删除容器将连同其全部子树一起删除，确认？")) return;
  const sel = captureSelectionNodes();
  pushUndo();
  removeSubtree(path);
  sel.primary = sel.primary === node || !sel.primary || subtreeContains(node, sel.primary) ? null : sel.primary;
  sel.others = sel.others.filter((n) => n !== node && !subtreeContains(node, n));
  restoreSelectionPaths(sel);
  markDirty();
  render();
}

function subtreeContains(container, node) {
  for (const key of CONTAINER_LISTS[container.type] || []) {
    for (const child of listOf(container, key)) {
      if (child === node || subtreeContains(child, node)) return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// 批量操作（多选）：同列表 + 连续性校验，块移动/批量删除。
// ---------------------------------------------------------------------------

function batchGroup() {
  const paths = state.multi.length > 1 ? state.multi : state.selected ? [state.selected] : [];
  if (!paths.length) return null;
  const last = paths[0][paths[0].length - 1];
  const containerPath = paths[0].slice(0, -1);
  for (const p of paths) {
    const pLast = p[p.length - 1];
    if (pLast.key !== last.key) return null;
    if (!pathEquals(p.slice(0, -1), containerPath)) return null;
  }
  const indices = paths.map((p) => p[p.length - 1].index).sort((a, b) => a - b);
  const contiguous = indices.every((v, i) => i === 0 || v === indices[i - 1] + 1);
  return { containerPath, key: last.key, indices, contiguous };
}

function batchCanMove(delta) {
  const group = batchGroup();
  if (!group || !group.contiguous) return false;
  const list = findList(group.containerPath, group.key);
  if (!list) return false;
  const start = group.indices[0];
  const end = group.indices[group.indices.length - 1];
  return delta < 0 ? start > 0 : end < list.length - 1;
}

function batchMove(delta) {
  if (!batchCanMove(delta)) return;
  const group = batchGroup();
  const list = findList(group.containerPath, group.key);
  const start = group.indices[0];
  const count = group.indices.length;
  pushUndo();
  const block = list.splice(start, count);
  list.splice(start + delta, 0, ...block);
  const containerPath = group.containerPath;
  const key = group.key;
  state.multi = [];
  for (let i = 0; i < count; i += 1) {
    state.multi.push([...containerPath, { key, index: start + delta + i }]);
  }
  state.selected = state.multi[state.multi.length - 1];
  markDirty();
  render();
}

function batchDelete() {
  const paths = state.multi.length ? state.multi : state.selected ? [state.selected] : [];
  if (!paths.length) return;
  const hasContainer = paths.some((p) => CONTAINER_LISTS[findNode(p).type]);
  if (hasContainer && !confirm("删除选中项包含容器，其全部子树将连带删除，确认？")) return;
  pushUndo();
  const ordered = [...paths].sort((a, b) => b.length - a.length || b[b.length - 1].index - a[a.length - 1].index);
  for (const path of ordered) {
    if (!findNode(path)) continue;
    removeSubtree(path);
  }
  state.selected = null;
  state.multi = [];
  markDirty();
  render();
}

// ---------------------------------------------------------------------------
// DnD v2：子树拖拽 + 行上/下半区插入线 + 空容器落点 + 边缘自动滚动
// ---------------------------------------------------------------------------

const AUTOSCROLL_EDGE = 48;
const AUTOSCROLL_STEP = 10;
let autoScrollY = null;

function clearDropMarkers() {
  for (const el of document.querySelectorAll("#canvas li[data-node]")) {
    el.classList.remove("drop-above", "drop-below");
  }
  for (const el of document.querySelectorAll("#canvas li.drop-empty")) {
    el.classList.remove("drop-into");
  }
}

function handleDragEnd() {
  dragState = null;
  pendingDrop = null;
  autoScrollY = null;
  clearDropMarkers();
}

// 移动拖拽时，目标容器不得位于被拖子树内部。
function dropAllowed(containerPath) {
  if (!dragState) return false;
  if (dragState.type === "move" && pathStartsWith(containerPath, dragState.path)) return false;
  return true;
}

function handleCanvasDragOver(e) {
  if (!dragState) return;
  e.preventDefault();
  autoScrollY = e.clientY;
  pendingDrop = null;
  clearDropMarkers();

  const empty = e.target.closest("#canvas li.drop-empty");
  if (empty) {
    const containerPath = JSON.parse(empty.dataset.containerPath);
    const key = empty.dataset.listKey;
    if (dropAllowed(containerPath)) {
      pendingDrop = { containerPath, key, index: 0 };
      empty.classList.add("drop-into");
      e.dataTransfer.dropEffect = dragState.type === "new" ? "copy" : "move";
    } else {
      e.dataTransfer.dropEffect = "none";
    }
    return;
  }

  const li = e.target.closest("#canvas li[data-node]");
  if (li) {
    // 落点判定一律用节点自身行（row）的矩形，li 内边距/分支标签区域也算该节点的上/下半区
    const row = li.querySelector(":scope > .row");
    const path = JSON.parse(li.dataset.path);
    const last = path[path.length - 1];
    const containerPath = path.slice(0, -1);
    if (dropAllowed(containerPath)) {
      const rect = row.getBoundingClientRect();
      const before = e.clientY < rect.top + rect.height / 2;
      pendingDrop = { containerPath, key: last.key, index: before ? last.index : last.index + 1 };
      li.classList.add(before ? "drop-above" : "drop-below");
      e.dataTransfer.dropEffect = dragState.type === "new" ? "copy" : "move";
    } else {
      e.dataTransfer.dropEffect = "none";
    }
    return;
  }

  const drop = rootEndDrop();
  if (dropAllowed(drop.containerPath)) {
    pendingDrop = drop;
    e.dataTransfer.dropEffect = dragState.type === "new" ? "copy" : "move";
  } else {
    e.dataTransfer.dropEffect = "none";
  }
}

function handleCanvasDrop(e) {
  if (!dragState || !pendingDrop) return;
  e.preventDefault();
  const { containerPath, key, index } = pendingDrop;
  const pre = takeSnapshot();
  if (dragState.type === "new") {
    const node = dragState.flow ? newFlowNode(dragState.flow) : newActionNode(dragState.command);
    insertNode(containerPath, key, index, node);
    selectSingle([...containerPath, { key, index }]);
    pushUndo(pre);
  } else {
    const sel = captureSelectionNodes();
    const moved = moveSubtree(dragState.path, containerPath, key, index);
    if (moved) {
      pushUndo(pre);
      restoreSelectionPaths(sel);
      selectSingle(moved);
    }
  }
  markDirty();
  render();
  handleDragEnd();
}

function autoScrollTick() {
  const wrap = $("canvas-wrap");
  if (!dragState || autoScrollY === null) return;
  const rect = wrap.getBoundingClientRect();
  if (autoScrollY < rect.top + AUTOSCROLL_EDGE) wrap.scrollTop -= AUTOSCROLL_STEP;
  else if (autoScrollY > rect.bottom - AUTOSCROLL_EDGE) wrap.scrollTop += AUTOSCROLL_STEP;
  requestAnimationFrame(autoScrollTick);
}

// ---------------------------------------------------------------------------
// 树形渲染
// ---------------------------------------------------------------------------

function summarize(node) {
  if (node.type === "action") {
    const withValue = node["with"] || {};
    return Object.keys(withValue).length ? JSON.stringify(withValue) : "";
  }
  if (node.type === "if") {
    const c = node.condition || {};
    const op = (I18N && I18N.ops[c.op]) || c.op || "";
    return [c.left, op, c.right].filter((v) => v !== undefined && v !== null && v !== "").join(" ");
  }
  if (node.type === "forEach") return `${node.item_var || "item"} ∈ ${JSON.stringify(node.items)}`;
  if (node.type === "try") return `error → ${node.error_var || "error"}`;
  if (node.type === "return") return JSON.stringify(node.value);
  return `${(node.children || []).length} 个子节点`;
}

function renderOps(path) {
  const ops = document.createElement("span");
  ops.className = "ops";
  const up = document.createElement("button");
  up.textContent = "↑";
  up.title = "上移";
  const down = document.createElement("button");
  down.textContent = "↓";
  down.title = "下移";
  const del = document.createElement("button");
  del.textContent = "✕";
  del.title = "删除";
  up.addEventListener("click", (e) => { e.stopPropagation(); moveNode(path, -1); });
  down.addEventListener("click", (e) => { e.stopPropagation(); moveNode(path, 1); });
  del.addEventListener("click", (e) => { e.stopPropagation(); removeNode(path); });
  ops.appendChild(up);
  ops.appendChild(down);
  ops.appendChild(del);
  return ops;
}

function renderNode(node, path, index) {
  const li = document.createElement("li");
  li.dataset.node = node.id;
  li.dataset.path = JSON.stringify(path);
  li.classList.add(`type-${node.type}`);
  if (pathIn(state.multi, path)) li.classList.add("multi-selected");
  if (state.selected && pathEquals(state.selected, path)) li.classList.add("selected");

  const row = document.createElement("div");
  row.className = "row";

  const grip = document.createElement("span");
  grip.className = "grip";
  grip.textContent = "⠿";
  grip.title = "拖拽移动子树";

  const idx = document.createElement("span");
  idx.className = "idx";
  idx.textContent = String(index + 1);

  row.appendChild(grip);
  row.appendChild(idx);
  if (node.type === "action") {
    const manifest = manifestOf(node.command);
    const iconWrap = iconSvg(KIND_ICON[(manifest && manifest.kind) || "action"] || "play");
    iconWrap.classList.add("icon-block", `kind-${(manifest && manifest.kind) || "action"}`);
    row.appendChild(iconWrap);
    const texts = document.createElement("div");
    texts.className = "item-texts";
    const title = document.createElement("span");
    title.className = "item-title";
    title.textContent = commandName(node.command);
    const sub = document.createElement("span");
    sub.className = "item-sub";
    sub.textContent = node.command;
    texts.appendChild(title);
    texts.appendChild(sub);
    row.appendChild(texts);
  } else {
    const iconWrap = iconSvg(FLOW_ICON[node.type] || "play");
    iconWrap.classList.add("icon-block", "kind-flow");
    row.appendChild(iconWrap);
    const texts = document.createElement("div");
    texts.className = "item-texts";
    const title = document.createElement("span");
    title.className = "item-title";
    title.textContent = flowName(node.type);
    const sub = document.createElement("span");
    sub.className = "item-sub";
    sub.textContent = node.type;
    texts.appendChild(title);
    texts.appendChild(sub);
    row.appendChild(texts);
  }
  const args = document.createElement("span");
  args.className = "args";
  args.textContent = summarize(node);
  row.appendChild(args);
  row.appendChild(renderOps(path));
  li.appendChild(row);

  li.draggable = true;
  li.addEventListener("dragstart", (e) => {
    e.stopPropagation();
    dragState = { type: "move", path };
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", node.id);
    li.classList.add("dragging");
  });
  li.addEventListener("dragend", () => {
    li.classList.remove("dragging");
    handleDragEnd();
  });
  li.addEventListener("click", (e) => {
    e.stopPropagation();
    if (e.ctrlKey || e.metaKey) toggleMulti(path);
    else if (e.shiftKey) rangeSelect(path);
    else selectSingle(path);
    render();
  });

  const lists = CONTAINER_LISTS[node.type];
  if (lists) {
    for (const key of lists) {
      if (lists.length > 1) {
        const label = document.createElement("div");
        label.className = "branch-label";
        const branchKey = `${node.type}:${key}`;
        label.textContent =
          (I18N && I18N.branchLabels[branchKey]) || BRANCH_LABELS[key] || key;
        li.appendChild(label);
      }
      const sub = document.createElement("ol");
      sub.className = "children";
      renderChildren(sub, path, key);
      li.appendChild(sub);
    }
  }
  return li;
}

function renderChildren(listEl, containerPath, key) {
  const depth = containerPath.length;
  listEl.classList.add(`depth-${depth % 6}`);
  const list = findList(containerPath, key);
  list.forEach((node, index) => {
    listEl.appendChild(renderNode(node, [...containerPath, { key, index }], index));
  });
  if (!list.length) {
    const empty = document.createElement("li");
    empty.className = "drop-empty";
    empty.dataset.containerPath = JSON.stringify(containerPath);
    empty.dataset.listKey = key;
    empty.textContent = "空分支 · 拖入节点";
    listEl.appendChild(empty);
  }
}

function render() {
  normalizeSelection();
  renderCanvas();
  renderProps();
  renderToolbar();
}

function renderToolbar() {
  $("btn-undo").disabled = !state.undo.length;
  $("btn-redo").disabled = !state.redo.length;
  const bar = $("multi-bar");
  const hasSelection = state.multi.length >= 1;
  bar.classList.toggle("hidden", !hasSelection);
  if (hasSelection) {
    $("multi-count").textContent = `${state.multi.length} 个已选`;
    $("btn-up").disabled = !batchCanMove(-1);
    $("btn-down").disabled = !batchCanMove(1);
    $("btn-copy").disabled = false;
    $("btn-delete").disabled = false;
  }
}

function renderCanvas() {
  const list = $("canvas");
  list.textContent = "";
  renderChildren(list, [], "children");
  if (!state.workflow.root.children.length) {
    const empty = list.querySelector("li.drop-empty");
    if (empty) {
      empty.classList.add("hint");
      empty.textContent = "画布为空：点击左侧命令追加节点，或拖拽到此处";
    }
  }
}

// ---------------------------------------------------------------------------
// 属性面板（控制流属性表单属切片 2，本切片仅 action 全表单 + 其余 id 只读信息）
// ---------------------------------------------------------------------------

function renderProps() {
  // fx 变量浮层挂在 body 上，面板重渲染前必须主动关闭（否则残留悬浮）
  if (fxPopupCleanup) fxPopupCleanup();
  const body = $("props-body");
  body.textContent = "";
  if (state.multi.length > 1) {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = `已选中 ${state.multi.length} 个节点（Ctrl 多选 / Shift 范围）。批量操作请用顶部工具栏。`;
    body.appendChild(hint);
    return;
  }
  const node = state.selected ? findNode(state.selected) : null;
  if (!node) {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = "未选中节点";
    body.appendChild(hint);
    return;
  }
  if (node.type !== "action") {
    renderControlProps(node, body);
    return;
  }
  body.appendChild(numberField("超时（秒，可选）", node.timeout_seconds, (v) => {
    if (v === null) delete node.timeout_seconds; else node.timeout_seconds = v;
    markDirty();
  }));
  body.appendChild(numberField("重试次数", node.retry_count ?? 0, (v) => {
    if (v === null || v === 0) delete node.retry_count; else node.retry_count = v;
    markDirty();
  }));
  const manifest = manifestOf(node.command);
  const schema = manifest ? manifest.input_schema : {};
  const properties = schema.properties || {};
  const keys = Object.keys(properties);
  // 分区标题：输入参数（喂给命令的值）与输出参数（命令产出的值，存变量）分开展示
  const sectionHeader = (text, cls) => {
    const h = document.createElement("div");
    h.className = `props-section ${cls || ""}`.trim();
    h.textContent = text;
    return h;
  };
  body.appendChild(sectionHeader("输入参数", "props-section-in"));
  if (!keys.length) {
    body.appendChild(jsonField("参数（with，JSON）", node["with"], (v) => {
      if (v === null) node["with"] = {}; else node["with"] = v;
      markDirty();
      renderCanvas();
    }));
  } else {
    const required = new Set(schema.required || []);
    const groups = Array.isArray(schema["x-param-groups"]) ? schema["x-param-groups"] : null;
    if (groups && groups.length) {
      // 对标影刀「常规/高级」分区：按 manifest x-param-groups 分组渲染，
      // 当前通道不生效的字段自动归入底部折叠区（切换通道后重新渲染归位）。
      renderGroupedFields(body, node, schema, properties, required, groups);
    } else {
      for (const key of keys) {
        body.appendChild(schemaField(node, key, properties[key], required.has(key)));
      }
    }
    // 通道解析预览：浏览器指令缺省走 extension 还是 playwright（与执行器同语义）
    const transportInput = body.querySelector('[data-field="transport"]');
    if (transportInput) {
      const fieldEl = transportInput.closest(".field");
      if (fieldEl) fieldEl.insertAdjacentElement("afterend", channelPreviewField(node));
      for (const key of ["transport", "channel"]) {
        const input = body.querySelector(`[data-field="${key}"]`);
        if (input) input.addEventListener("change", () => renderProps());
      }
    }
  }
  // 字段联动：x-depends 声明哪些字段依赖其他字段的特定值
  applyDependencies(body, node, schema);
  // 输出别名：按 manifest x-outputs 逐输出字段命名（label 来自 manifest，如「保存网页对象到」），
  // 后续节点通过 ${别名} 引用该输出整值、${别名}.字段 引用子字段。
  const xOutputs = manifest && manifest["x-outputs"] ? manifest["x-outputs"] : null;
  // hidden 输出（如 resourceType/最终网址）不给别名框：运行值仍可 ${...} 手动引用，但不打扰用户
  const visibleOutputs = xOutputs
    ? Object.entries(xOutputs).filter(([, meta]) => !(meta && meta.hidden))
    : [];
  if (visibleOutputs.length) {
    body.appendChild(sectionHeader("输出参数（保存到变量，供后续指令引用）", "props-section-out"));
    for (const [outField, meta] of visibleOutputs) {
      const labelText = (meta && meta.label) || outField;
      const wrap = document.createElement("div");
      wrap.className = "field";
      const lbl = document.createElement("label");
      lbl.textContent = labelText;
      const inp = document.createElement("input");
      inp.type = "text";
      inp.dataset.field = labelText;
      inp.value = (node.output_aliases || {})[outField] || "";
      inp.placeholder = "可选，如 web_page1";
      inp.title = `为输出 ${outField} 命名，后续节点可通过 \${别名} 引用`;
      inp.addEventListener("input", () => {
        const v = inp.value.trim();
        const aliases = { ...(node.output_aliases || {}) };
        if (v) aliases[outField] = v; else delete aliases[outField];
        node.output_aliases = Object.keys(aliases).length ? aliases : undefined;
        markDirty();
      });
      inp.addEventListener("blur", () => {
        const v = inp.value.trim();
        inp.style.borderColor = v && !/^[A-Za-z_]\w*$/.test(v) ? "var(--err)" : "";
      });
      wrap.appendChild(lbl);
      wrap.appendChild(inp);
      body.appendChild(wrap);
    }
  }
  // 字段联动：监听所有 input 变化，重新评估依赖关系
  body.addEventListener("input", () => applyDependencies(body, node, schema));
  body.addEventListener("change", () => applyDependencies(body, node, schema));
}

// ---------------------------------------------------------------------------
// 参数分组（对标影刀「常规/高级」分区）
// ---------------------------------------------------------------------------

// 分组规划：纯函数（无 DOM），判定各字段的归属分组与展开缺省——
// node 侧测试与前端渲染共用同一套规则（scripts/check_param_groups.mjs）。
// 规则：
// - x-depends 未满足的字段不进其声明分组，归入底部「其他通道参数」折叠区（通道切换后重渲染自动归位）
// - 未被 x-param-groups 声明的字段归入「其他」
// - collapsed 组缺省收起，但组内任一字段已填值时自动展开（用户填过的东西不藏起来）
function paramGroupPlan(schema, properties, withArgs, groups) {
  const deps = schema["x-depends"] || {};
  const active = [];
  const inactive = [];
  for (const key of Object.keys(properties)) {
    const cond = deps[key];
    if (!cond) { active.push(key); continue; }
    const ctrlKey = Object.keys(cond)[0];
    const isActive = (withArgs ? withArgs[ctrlKey] : undefined) === cond[ctrlKey];
    (isActive ? active : inactive).push(key);
  }
  const claimed = new Set();
  const plan = [];
  for (const group of groups) {
    const fields = (group.fields || []).filter(
      (k) => properties[k] && active.includes(k) && !claimed.has(k)
    );
    if (!fields.length) continue;
    for (const k of fields) claimed.add(k);
    const hasValue = fields.some((k) => {
      const v = withArgs ? withArgs[k] : undefined;
      return v !== undefined && v !== null && v !== "";
    });
    plan.push({ label: group.label || "参数", fields, open: group.collapsed ? hasValue : true });
  }
  const rest = active.filter((k) => !claimed.has(k));
  if (rest.length) plan.push({ label: "其他", fields: rest, open: true });
  if (inactive.length) {
    plan.push({ label: "__inactive__", fields: inactive, open: false });
  }
  return plan;
}

function renderGroupedFields(body, node, schema, properties, required, groups) {
  const plan = paramGroupPlan(schema, properties, node["with"] || {}, groups);
  for (const part of plan) {
    body.appendChild(paramGroupSection(node, part, properties, required));
  }
}

function paramGroupSection(node, part, properties, required) {
  const wrap = document.createElement("div");
  wrap.className = "props-group";
  if (part.label === "__inactive__") wrap.classList.add("props-group-inactive");
  const key = `${node.id}|${part.label}`;
  const stored = state.paramGroupsOpen.get(key);
  const open = stored === undefined ? part.open : stored;
  if (!open) wrap.classList.add("collapsed");

  const head = document.createElement("div");
  head.className = "props-group-head";
  const caret = document.createElement("span");
  caret.className = "props-group-caret";
  caret.textContent = "▾";
  const title = document.createElement("span");
  title.className = "props-group-title";
  title.textContent = part.label === "__inactive__"
    ? `其他通道参数（${part.fields.length}）`
    : part.label;
  const count = document.createElement("span");
  count.className = "props-group-count";
  count.textContent = String(part.fields.length);
  head.append(caret, title, count);
  if (part.label === "__inactive__") {
    head.title = "这些参数属于其他执行通道，当前通道下不生效；切换通道后自动归位到所属分组";
  }

  const bodyEl = document.createElement("div");
  bodyEl.className = "props-group-body";
  for (const k of part.fields) {
    bodyEl.appendChild(schemaField(node, k, properties[k], required.has(k)));
  }
  head.addEventListener("click", () => {
    const nowCollapsed = wrap.classList.toggle("collapsed");
    state.paramGroupsOpen.set(key, !nowCollapsed);
  });
  wrap.append(head, bodyEl);
  return wrap;
}

// 字段联动：根据 x-depends 声明，禁用/启用依赖字段
// x-depends 格式: { "headless": {"transport": "playwright"}, ... }
// 含义: headless 仅当 transport === "playwright" 时启用
// 展示策略：不隐藏、只禁用——参数"凭空消失"会让用户以为指令没有这个开关，
// 禁用 + 说明能让用户看懂"它属于另一条通道"。
function applyDependencies(body, node, schema) {
  const deps = schema["x-depends"];
  if (!deps) return;
  for (const [field, condition] of Object.entries(deps)) {
    const ctrlKey = Object.keys(condition)[0];
    const requiredValue = condition[ctrlKey];
    const ctrlValue = node["with"] ? node["with"][ctrlKey] : undefined;
    const active = ctrlValue === requiredValue;
    // 找到该字段的 wrapper（data-field 属性匹配）
    const wrap = body.querySelector(`[data-field="${field}"]`);
    if (!wrap) continue;
    const fieldWrap = wrap.closest(".field");
    if (!fieldWrap) continue;
    fieldWrap.classList.toggle("field-inactive", !active);
    for (const el of fieldWrap.querySelectorAll("input, select, textarea")) {
      el.disabled = !active;
      el.title = active
        ? ""
        : `仅当 ${ctrlKey}=${requiredValue} 时生效（当前 ${ctrlKey}=${ctrlValue === undefined ? "缺省" : ctrlValue}）`;
    }
    let note = fieldWrap.querySelector(".dep-note");
    if (!active) {
      if (!note) {
        note = document.createElement("span");
        note.className = "field-hint dep-note";
        fieldWrap.appendChild(note);
      }
      note.textContent = `仅 ${ctrlKey}=${requiredValue} 时生效（当前 ${ctrlKey}=${ctrlValue === undefined ? "缺省" : ctrlValue}）`;
    } else if (note) {
      note.remove();
    }
  }
}

// ---------------------------------------------------------------------------
// 自研扩展执行通道状态（一等公民）：浏览器指令缺省走哪条通道、哪个浏览器
// ---------------------------------------------------------------------------

const HOST_BROWSER_LABELS = {
  msedge: "Edge",
  chrome: "Chrome",
  chromium: "Chromium",
  brave: "Brave",
  opera: "Opera",
  vivaldi: "Vivaldi",
  firefox: "Firefox",
  safari: "Safari",
  unknown: "未知浏览器",
};

const CHROMIUM_HOSTS = ["chrome", "msedge", "brave", "opera", "vivaldi", "chromium"];

function hostBrowserLabel(name) {
  if (!name) return "";
  return HOST_BROWSER_LABELS[name] || name;
}

function channelHostBrowser(status) {
  return status && status.host ? status.host.browser : null;
}

function channelMatchesHost(channel, status) {
  // 语义与后端 extension_exec.channel_matches_host 保持一致（两侧必须同步改）
  const expected = String(channel || "").toLowerCase();
  if (!expected) return true; // channel 缺省 = 跟随宿主
  const actual = String(channelHostBrowser(status) || "").toLowerCase();
  if (!actual) return false; // 宿主未上报：无法确认，不假装成功
  if (expected === actual) return true;
  if (expected === "chromium") return CHROMIUM_HOSTS.includes(actual);
  return false;
}

// 通道解析预览：与执行器 _use_extension 同语义——显式 transport 优先；
// 缺省时扩展在线且 channel 不与宿主冲突 → extension，否则 playwright。
function resolveChannelPreview(node) {
  const args = (node && node["with"]) || {};
  if (args.transport) {
    if (args.transport === "extension" && args.channel && !channelMatchesHost(args.channel, state.extChannel)) {
      const host = hostBrowserLabel(channelHostBrowser(state.extChannel)) || "未上报";
      return {
        channel: "extension(将失败)",
        reason: `扩展宿主为 ${host}，与 channel=${args.channel} 不符；请把插件装到 ${args.channel}，或改用 transport=playwright`,
      };
    }
    return { channel: args.transport, reason: "已在参数中显式指定" };
  }
  const status = state.extChannel;
  if (!status || !status.online) {
    return { channel: "playwright", reason: "自研插件未在线（未安装/未重载/devserver 未运行）" };
  }
  const hostName = hostBrowserLabel(channelHostBrowser(status));
  if (args.channel && !channelMatchesHost(args.channel, status)) {
    return {
      channel: "playwright",
      reason: `插件宿主为 ${hostName || "未知"}，无法满足 channel=${args.channel}，按 channel 启动独立浏览器`,
    };
  }
  return { channel: "extension", reason: `自研插件在线（宿主 ${hostName || "未知，请重载插件"}）` };
}

function channelPreviewField(node) {
  const row = document.createElement("div");
  row.className = "field channel-preview";
  const hint = document.createElement("span");
  hint.className = "field-hint";
  const resolved = resolveChannelPreview(node);
  hint.textContent = resolved.channel === "extension"
    ? `当前将走自研插件：${resolved.reason}`
    : resolved.channel === "extension(将失败)"
      ? `通道冲突：${resolved.reason}`
      : `当前将走 ${resolved.channel}：${resolved.reason}`;
  if (resolved.channel === "extension(将失败)") hint.classList.add("bad");
  row.appendChild(hint);
  return row;
}

async function refreshExtChannel() {
  const previous = state.extChannel;
  let status = { online: false, host: null };
  try {
    status = await api("GET", "/api/ext/status");
  } catch { /* devserver 未运行或通道异常：按离线展示 */ }
  state.extChannel = status;
  const badge = $("ext-channel-badge");
  if (badge) {
    const hostName = hostBrowserLabel(channelHostBrowser(status));
    const authFailures = Number(status.authFailures || 0);
    if (status.online) {
      badge.textContent = `扩展通道：在线${hostName ? `（${hostName}）` : ""}`;
      badge.className = "ext-badge ext-channel-badge ok";
      badge.title = `自研插件在线，浏览器指令缺省走 extension 通道；宿主浏览器：${hostName || "未上报（请在扩展页重载插件）"}`;
    } else if (authFailures) {
      // 最隐蔽的一种：插件装了、也在轮询，但 token 与本机 devserver 不匹配
      badge.textContent = "扩展通道：配对失败";
      badge.className = "ext-badge ext-channel-badge off";
      badge.title = `${authFailures} 次连接被拒（token 不匹配）。处置：删除 workflows/.capture-extension-token 后重启 devserver（扩展会自动重新配对），或点左侧「⇲ 插件」重新配对`;
    } else {
      badge.textContent = "扩展通道：离线";
      badge.className = "ext-badge ext-channel-badge off";
      badge.title = "自研插件未在线：浏览器指令缺省回退 playwright。点左侧「⇲ 插件」安装/重载";
    }
  }
  // 状态真正变化且当前选中的是浏览器指令 → 刷新通道预览（避免定时重渲染打断输入）
  const changed = !previous
    || previous.online !== status.online
    || Number(previous.authFailures || 0) !== Number(status.authFailures || 0)
    || channelHostBrowser(previous) !== channelHostBrowser(status);
  if (changed && state.selected) {
    const node = findNode(state.selected);
    if (node && node.type === "action" && String(node.command || "").startsWith("browser.")) {
      renderProps();
    }
  }
}

// ---------------------------------------------------------------------------
// 控制节点属性表单（切片 2）：if 条件 / forEach 循环 / try 捕获 / return 值
// ---------------------------------------------------------------------------

const CONDITION_OPS = ["eq", "ne", "gt", "gte", "lt", "lte", "contains", "truthy"];

function controlHint(text) {
  const p = document.createElement("p");
  p.className = "hint";
  p.textContent = text;
  return p;
}

function renderControlProps(node, body) {
  if (node.type === "sequence") {
    body.appendChild(controlHint("顺序容器：子节点按顺序执行。"));
    return;
  }
  if (node.type === "if") {
    if (!node.condition) node.condition = { op: "truthy", left: "" };
    const condition = node.condition;
    body.appendChild(selectField(
      "条件操作符",
      { enum: CONDITION_OPS },
      true,
      condition.op,
      (v) => { condition.op = v || "truthy"; },
    ));
    body.appendChild(literalField("左值（left）", condition.left, (v) => {
      condition.left = v;
      markDirty();
      renderCanvas();
    }, true));
    body.appendChild(literalField("右值（right，truthy 时留空）", condition.right, (v) => {
      if (v === null || v === "") delete condition.right; else condition.right = v;
      markDirty();
      renderCanvas();
    }));
    return;
  }
  if (node.type === "forEach") {
    body.appendChild(jsonField("items（数组或引用）", node.items, (v) => {
      node.items = v === null ? [] : v;
      markDirty();
      renderCanvas();
    }, true));
    body.appendChild(textField("循环变量名（item_var）", node.item_var || "item", (v) => {
      if (NAME_PATTERN.test(v)) node.item_var = v;
      markDirty();
      renderCanvas();
    }, true));
    return;
  }
  if (node.type === "try") {
    body.appendChild(textField("错误变量名（error_var）", node.error_var || "error", (v) => {
      if (NAME_PATTERN.test(v)) node.error_var = v;
      markDirty();
      renderCanvas();
    }, true));
    return;
  }
  if (node.type === "return") {
    body.appendChild(jsonField("返回值（value）", node.value, (v) => {
      node.value = v;
      markDirty();
      renderCanvas();
    }));
    body.appendChild(controlHint("return 节点会终止整个工作流并返回该值。"));
  }
}

function setWith(node, key, value) {
  if (!node["with"]) node["with"] = {};
  if (value === null || value === undefined || value === "") delete node["with"][key];
  else node["with"][key] = value;
  markDirty();
  renderCanvas();
}

// 采集画布中所有「创建会话」的 lifecycle 节点（output 含 sessionId），供 session 引用下拉用。
// filterResourceType: 可选，传入时只返回 output_schema 含该 resourceType 的节点。
function collectSessionNodes(filterResourceType) {
  const out = [];
  const visit = (container) => {
    const lists = [];
    if (Array.isArray(container.children)) lists.push(container.children);
    if (Array.isArray(container.then)) lists.push(container.then);
    if (Array.isArray(container.else)) lists.push(container.else);
    if (Array.isArray(container.catch)) lists.push(container.catch);
    for (const list of lists) {
      for (const n of list) {
        if (n.type === "action") {
          const m = manifestOf(n.command);
          const outProps = m && m.output_schema ? (m.output_schema.properties || {}) : {};
          if (outProps.sessionId) {
            // 按 resourceType 过滤：只显示匹配类型的生产者
            if (filterResourceType) {
              const rt = outProps.resourceType;
              if (!rt || rt.const !== filterResourceType) continue;
            }
            out.push({ id: n.id, command: n.command, zh: commandName(n.command), outputName: (n.output_aliases || {}).sessionId || null });
          }
        } else {
          visit(n);
        }
      }
    }
  };
  if (state.workflow && state.workflow.root) visit(state.workflow.root);
  return out;
}

function schemaField(node, key, propSchema, required) {
  const value = node["with"] ? node["with"][key] : undefined;
  const label = fieldLabel(key, node.command);
  let field;
  if (propSchema.enum) {
    field = selectField(label, propSchema, required, value, (v) => setWith(node, key, v), key);
  } else if (propSchema.type === "boolean") {
    field = checkboxField(label, required, Boolean(value), (v) => setWith(node, key, v), key);
  } else if (propSchema.type === "integer" || propSchema.type === "number") {
    field = numberField(label, value, (v) => setWith(node, key, v), required, key);
  } else if (propSchema.type === "array" || propSchema.type === "object") {
    field = jsonField(label, value, (v) => setWith(node, key, v), required, key);
  } else {
    const fxOpts = {};
    if (propSchema["x-fx"]) fxOpts.supportFx = true;
    if (propSchema["x-python"]) fxOpts.supportPython = true;
    if (fxOpts.supportFx || fxOpts.supportPython) {
      fxOpts.node = node;
      fxOpts.fieldKey = key;
    }
    field = textField(label, value === undefined ? "" : String(value), (v) => setWith(node, key, v), required, key, fxOpts.supportFx || fxOpts.supportPython ? fxOpts : undefined);
  }
  if (key === "varName" && node.command === "data.setVar") {
    // 变量写入目标：可选已有变量（重赋值）或新建变量名。
    // 与 output_aliases 解耦——这里就是「赋值给哪个变量」的语义。
    const pickWrap = document.createElement("div");
    pickWrap.className = "var-target-row";
    const sel = document.createElement("select");
    sel.className = "var-target-pick";
    const existing = collectUserVariables();
    sel.innerHTML = '<option value="">— 选择已有变量（或直接输入新名） —</option>';
    for (const v of existing) {
      const opt = document.createElement("option");
      opt.value = v.name;
      opt.textContent = `${v.name}（${v.source}）`;
      sel.appendChild(opt);
    }
    sel.addEventListener("change", () => {
      if (!sel.value) return;
      setWith(node, key, sel.value);
      const inputEl = field.querySelector('input[data-field]');
      if (inputEl) inputEl.value = sel.value;
      markDirty();
      showCompileMessage(`变量「${sel.value}」将被重赋值（覆盖旧值）`, true);
    });
    pickWrap.appendChild(sel);
    const hint = document.createElement("span");
    hint.className = "field-hint";
    hint.textContent = "填新名=定义变量，选已有=覆盖赋值";
    pickWrap.appendChild(hint);
    field.appendChild(pickWrap);
  }
  if (key === "sessionId") {
    // 会话引用字段：绑定创建会话的节点输出即可，无需手填。
    // 按当前命令的 resources 确定资源类型过滤：browser.session→webPage, desktop.session→windowHandle
    const curManifest = manifestOf(node.command);
    const curResources = curManifest && curManifest.resources ? curManifest.resources : [];
    let filterRT = null;
    if (curResources.includes("browser.session")) filterRT = "webPage";
    else if (curResources.includes("desktop.session")) filterRT = "windowHandle";

    const pickWrap = document.createElement("div");
    pickWrap.className = "session-pick-row";
    const sel = document.createElement("select");
    sel.className = "session-pick";
    const sessionNodes = collectSessionNodes(filterRT);
    if (sessionNodes.length === 0) {
      sel.innerHTML = '<option value="">— 无匹配的会话节点 —</option>';
    } else {
      sel.innerHTML = '<option value="">— 引用创建会话的节点 —</option>';
    }
    for (const s of sessionNodes) {
      const opt = document.createElement("option");
      opt.value = s.id;
      if (s.outputName) {
        // 已为 sessionId 输出起别名：label 显示变量名，选中时引用 ${别名}（别名即 sessionId 整值）
        opt.textContent = "${" + s.outputName + "}（" + s.zh + "）";
      } else {
        opt.textContent = `${s.zh}（${s.id}）`;
      }
      sel.appendChild(opt);
    }
    sel.title = "该命令的运行值绑定到「创建会话」节点的输出 sessionId，无需手填";
    const hint = document.createElement("span");
    hint.className = "field-hint";
    hint.textContent = "引用会话输出，无需手填";
    pickWrap.appendChild(sel);
    pickWrap.appendChild(hint);
    sel.addEventListener("change", () => {
      if (!sel.value) return;
      const picked = sessionNodes.find((s) => s.id === sel.value);
      const ref = picked && picked.outputName
        ? `\${${picked.outputName}}`
        : `\${steps.${sel.value}.outputs.sessionId}`;
      setWith(node, key, ref);
      const inputEl = field.querySelector('input[data-field]');
      if (inputEl) {
        inputEl.value = ref;
        inputEl.title = `引用会话输出：${ref}`;
      }
      markDirty();
      showCompileMessage(`已绑定会话：${ref}`, true);
    });
    const inputEl = field.querySelector('input[data-field]');
    if (inputEl && typeof value === "string" && value.startsWith("${")) {
      hint.textContent = "已绑定会话引用";
      hint.className = "field-hint ok";
    }
    field.appendChild(pickWrap);
  }
  if (key === "selector") {
    // 切片 D：从元素库选主元素（浏览器元素 → 填 css），带 kind 徽标；与「捕获」并列
    const pickWrap = document.createElement("div");
    pickWrap.className = "element-pick-row";
    const pick = document.createElement("select");
    pick.className = "element-pick";
    pick.innerHTML = '<option value="">从元素库选…</option>';
    for (const name of state.elementsSeen || []) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      pick.appendChild(opt);
    }
    pick.title = "选择流程元素库中的浏览器元素，填充其 css selector";
    const badge = document.createElement("span");
    badge.className = "element-pick-badge hidden";
    pickWrap.appendChild(pick);
    pickWrap.appendChild(badge);
    pick.addEventListener("change", async () => {
      const name = pick.value;
      if (!name) { badge.classList.add("hidden"); return; }
      const flow = requireFlow();
      if (!flow) return;
      try {
        const element = await api("GET", `${elementsBasePath(flow)}/${encodeURIComponent(name)}`);
        if (element.kind !== "browser" || !element.selector || !element.selector.css) {
          showCompileMessage(`元素「${name}」不是浏览器 css 元素，无法填入 selector`, false);
          pick.value = "";
          return;
        }
        setWith(node, key, element.selector.css);
        const inputEl = field.querySelector('input[data-field]');
        if (inputEl) inputEl.value = element.selector.css;
        badge.textContent = `${elementKindLabel(element.kind)} · ${name}`;
        badge.className = "element-pick-badge ok";
        markDirty();
        showCompileMessage(`已填入元素「${name}」的 selector`, true);
      } catch (err) {
        showCompileMessage(String(err.message || err), false);
        pick.value = "";
      }
    });
    // 兜底：字段已有非空值时反向标徽章（值来自元素库的常见 css）
    if (typeof value === "string" && value.trim()) {
      badge.textContent = `css · ${value}`;
      badge.className = "element-pick-badge";
    }
    field.appendChild(pickWrap);
    const captureBtn = document.createElement("button");
    captureBtn.type = "button";
    captureBtn.className = "capture-btn";
    captureBtn.textContent = "捕获";
    captureBtn.title = "打开浏览器，在页面里点击元素捕获 selector";
    captureBtn.addEventListener("click", () => captureIntoField(node, key));
    field.appendChild(captureBtn);
  }
  return field;
}

const fieldLabel = (key, command) =>
  (command && I18N && I18N.commandFields && I18N.commandFields[command] && I18N.commandFields[command][key])
  || (I18N && I18N.fields[key]) || key;

function wrapField(labelText, required, input, hint, rawKey) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  input.dataset.field = rawKey || labelText;
  const label = document.createElement("label");
  label.textContent = labelText;
  if (required) {
    const star = document.createElement("span");
    star.className = "req";
    star.textContent = " *";
    label.appendChild(star);
  }
  if (rawKey && rawKey !== labelText) {
    const keySpan = document.createElement("span");
    keySpan.className = "field-key";
    keySpan.textContent = ` ${rawKey}`;
    label.appendChild(keySpan);
  }
  if (hint) input.title = hint;
  wrap.appendChild(label);
  wrap.appendChild(input);
  return wrap;
}

function textField(labelText, value, onChange, required, rawKey, opts) {
  const input = document.createElement("input");
  input.type = "text";
  input.value = value;
  input.placeholder = "${...}";
  attachRefCompletion(input);
  input.addEventListener("focus", () => pushUndo());
  input.addEventListener("input", () => {
    input.style.borderColor = "";
    onChange(input.value.trim());
  });
  const wrap = wrapField(labelText, required, input, REFERENCE_HINT, rawKey);
  // fx / Python 开关
  if (opts && (opts.supportFx || opts.supportPython)) {
    const node = opts.node;
    const fieldKey = opts.fieldKey;
    const modes = node._exprModes || (node._exprModes = {});
    // 创建输入行容器
    const row = document.createElement("div");
    row.className = "expr-input-row";
    // 移除原始 input，插入到 row 中
    input.remove();
    if (opts.supportPython) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "expr-btn py-btn" + (modes[fieldKey] === "python" ? " active" : "");
      btn.textContent = "Py";
      btn.title = "Python 表达式模式：输入 Python 表达式";
      btn.addEventListener("click", () => {
        pushUndo();
        const prev = node._exprModes[fieldKey];
        if (prev === "python") {
          delete node._exprModes[fieldKey];
          btn.classList.remove("active");
          input.classList.remove("py-mode");
          input.placeholder = "${...}";
        } else {
          // 如果 fx 模式激活，先清理
          if (prev === "fx") {
            restoreTextField(wrap, input);
            const fxBtn = row.querySelector(".fx-btn");
            if (fxBtn) fxBtn.classList.remove("active");
          }
          node._exprModes[fieldKey] = "python";
          btn.classList.add("active");
          input.classList.add("py-mode");
          input.placeholder = "输入 Python 表达式";
        }
        markDirty();
      });
      row.appendChild(btn);
    }
    row.appendChild(input);
    if (opts.supportFx) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "expr-btn fx-btn" + (modes[fieldKey] === "fx" ? " active" : "");
      btn.textContent = "fx";
      btn.title = "变量引用模式：从下拉框选择流程变量";
      btn.addEventListener("click", () => {
        pushUndo();
        const prev = node._exprModes[fieldKey];
        if (prev === "fx") {
          delete node._exprModes[fieldKey];
          btn.classList.remove("active");
          restoreTextField(wrap, input);
        } else {
          // 如果 Python 模式激活，先清理
          if (prev === "python") {
            input.classList.remove("py-mode");
            input.placeholder = "${...}";
            const pyBtn = row.querySelector(".py-btn");
            if (pyBtn) pyBtn.classList.remove("active");
          }
          node._exprModes[fieldKey] = "fx";
          btn.classList.add("active");
          replaceWithVarSelect(wrap, input, node, fieldKey, onChange);
        }
        markDirty();
      });
      row.appendChild(btn);
    }
    // 将 row 插入到 wrap 中（替换原来的 input 位置）
    wrap.appendChild(row);
    // 恢复已保存的表达式模式
    const savedMode = modes[fieldKey];
    if (savedMode === "fx" && opts.supportFx) {
      // 延迟执行，确保 DOM 已完全构建
      setTimeout(() => replaceWithVarSelect(wrap, input, node, fieldKey, onChange), 0);
    } else if (savedMode === "python" && opts.supportPython) {
      input.classList.add("py-mode");
      input.placeholder = "输入 Python 表达式";
    }
  }
  return wrap;
}

// fx 模式：标签化变量编辑器。
// 用户在文本中通过下拉插入变量，插入后渲染为 [变量名] 标签（chip）；
// 底层仍存 ${变量名} 字符串，与编译/运行时完全兼容，零迁移。
function replaceWithVarSelect(wrap, origInput, node, fieldKey, onChange) {
  // 先清理已有的编辑器（防止重复创建）
  if (wrap._fxEditor) {
    wrap._fxEditor.remove();
    delete wrap._fxEditor;
  }
  // 离开文本输入框（避免占位/挤占按钮位置）；保留引用，restoreTextField 时插回。
  // 这里不能仅 display:none：隐藏的 input 仍占据 flex 槽位，
  // 会让 row 变成 [Py, hidden-input, fx, editor, picker]——fx 视觉上
  // 跳到 input 前面，并且浏览器对 hidden 元素的尺寸计算也容易触发 select 拉高。
  wrap._fxHiddenInput = origInput;
  origInput.remove();

  const editor = document.createElement("div");
  editor.className = "fx-tag-editor";
  editor.dataset.field = origInput.dataset.field;
  editor.setAttribute("contenteditable", "true");
  editor.spellcheck = false;

  // 底层值 <-> 标签视图互转：${name} → [name] chip，其余文本原样
  const toView = (raw) => {
    editor.textContent = "";
    if (typeof raw !== "string" || !raw) return;
    const re = /\$\{([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\}/g;
    let last = 0;
    let m;
    while ((m = re.exec(raw)) !== null) {
      if (m.index > last) {
        editor.appendChild(document.createTextNode(raw.slice(last, m.index)));
      }
      editor.appendChild(makeVarChip(m[1]));
      last = re.lastIndex;
    }
    if (last < raw.length) {
      editor.appendChild(document.createTextNode(raw.slice(last)));
    }
  };

  // 从视图读回底层字符串：chip → ${name}，文本原样拼接
  const fromView = () => {
    let out = "";
    for (const child of editor.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        out += child.textContent;
      } else if (child.classList && child.classList.contains("fx-var-chip")) {
        out += `\${${child.dataset.varName}}`;
      } else {
        out += child.textContent || "";
      }
    }
    return out;
  };

  const commit = () => {
    const raw = fromView();
    setWith(node, fieldKey, raw || null);
    markDirty();
    if (typeof onChange === "function") onChange(raw);
  };

  editor.addEventListener("input", commit);
  editor.addEventListener("blur", commit);

  // 插入变量 chip 到光标处（或末尾）
  const insertVar = (name) => {
    const chip = makeVarChip(name);
    const sel = window.getSelection();
    let range = null;
    if (sel && sel.rangeCount && editor.contains(sel.anchorNode)) {
      range = sel.getRangeAt(0);
    }
    if (range) {
      range.deleteContents();
      range.insertNode(chip);
      range.setStartAfter(chip);
      range.collapse(true);
      sel.removeAllRanges();
      sel.addRange(range);
    } else {
      editor.appendChild(chip);
    }
    commit();
  };

  // 变量插入浮层：点击编辑器弹出，挂 document.body + position:fixed——
  // 不受 #props overflow-y:auto 裁剪（否则弹层被面板截断/位置歪）。
  // 用 mousedown + preventDefault 保证编辑器不失去焦点、光标位置不丢。
  const ac = new AbortController();
  let varPopup = null;
  const closeVarPopup = () => {
    ac.abort(); // 连带移除 scroll/resize 监听
    if (varPopup) { varPopup.remove(); varPopup = null; }
    if (fxPopupCleanup === closeVarPopup) fxPopupCleanup = null;
  };

  const openVarPopup = () => {
    if (varPopup) return;
    const userVars = collectUserVariables();
    const userNames = new Set(userVars.map((v) => v.name));
    varPopup = document.createElement("div");
    varPopup.className = "ref-completion fx-var-popup";
    const addGroup = (label, items) => {
      if (!items.length) return;
      const head = document.createElement("div");
      head.className = "fx-var-popup-group";
      head.textContent = label;
      varPopup.appendChild(head);
      for (const it of items) {
        const item = document.createElement("div");
        item.className = "ref-item";
        item.textContent = it.label;
        item.addEventListener("mousedown", (e) => {
          e.preventDefault();
          insertVar(it.name);
          closeVarPopup();
        });
        varPopup.appendChild(item);
      }
    };
    addGroup("用户变量", userVars.map((v) => ({ name: v.name, label: `${v.name}（${v.source}）` })));
    addGroup("内置作用域", computeReferencePaths()
      .map((p) => p.replace(/^\$\{|\}$/g, ""))
      .filter((bare) => bare.includes(".") || !userNames.has(bare))
      .map((bare) => ({ name: bare, label: bare })));
    // mousedown 落在浮层上（含滚动条）也不让编辑器 blur
    varPopup.addEventListener("mousedown", (e) => e.preventDefault());
    document.body.appendChild(varPopup);
    // 锚定编辑器正下方；右缘/下缘出视口时回收
    const r = editor.getBoundingClientRect();
    const pw = varPopup.offsetWidth;
    const ph = varPopup.offsetHeight;
    let left = r.left;
    let top = r.bottom + 2;
    if (left + pw > window.innerWidth - 8) left = window.innerWidth - 8 - pw;
    if (top + ph > window.innerHeight - 8) top = Math.max(8, r.top - ph - 2);
    varPopup.style.left = `${Math.round(left)}px`;
    varPopup.style.top = `${Math.round(top)}px`;
    // 面板滚动/窗口变化时关闭（fixed 定位不跟随锚点）；
    // 浮层自身内部滚动（列表超过 max-height）不算，不能误关。
    window.addEventListener("scroll", (e) => {
      if (varPopup && e.target instanceof Node && varPopup.contains(e.target)) return;
      closeVarPopup();
    }, { capture: true, signal: ac.signal });
    window.addEventListener("resize", closeVarPopup, { signal: ac.signal });
    fxPopupCleanup = closeVarPopup;
  };

  editor.addEventListener("click", () => openVarPopup());
  editor.addEventListener("blur", () => setTimeout(closeVarPopup, 150));
  editor.addEventListener("keydown", (e) => { if (e.key === "Escape") closeVarPopup(); });

  // 点击 chip 可删除
  editor.addEventListener("click", (e) => {
    const chip = e.target.closest && e.target.closest(".fx-var-chip");
    if (chip && (e.altKey || e.metaKey)) {
      e.preventDefault();
      chip.remove();
      commit();
    }
  });

  toView(node["with"] ? node["with"][fieldKey] : undefined);
  const row = wrap.querySelector(".expr-input-row");
  const holder = row || wrap;
  // 找到 fx-btn，把 editor 插到 fx-btn 之前，让 row 最终顺序为
  // [Py, editor, fx]，fx 按钮保持在最右端。
  const fxBtn = holder.querySelector(".fx-btn");
  if (fxBtn) {
    holder.insertBefore(editor, fxBtn);
  } else {
    holder.appendChild(editor);
  }
  wrap._fxEditor = editor;
  wrap._closeFxPopup = closeVarPopup;
}

// 变量标签（chip）：点击选中、Alt+点击删除
function makeVarChip(name) {
  const chip = document.createElement("span");
  chip.className = "fx-var-chip";
  chip.dataset.varName = name;
  chip.setAttribute("contenteditable", "false");
  chip.textContent = `[${name}]`;
  chip.title = `变量 ${name}（Alt+点击删除）`;
  return chip;
}

// 恢复文本输入（从 fx 模式退回）
function restoreTextField(wrap, origInput) {
  if (wrap._fxEditor) {
    // 浮层变量下拉一起关闭
    if (typeof wrap._closeFxPopup === "function") wrap._closeFxPopup();
    wrap._fxEditor.remove();
    delete wrap._fxEditor;
    delete wrap._closeFxPopup;
  }
  // 解除 detach：把 input 重新插入到 row 中的 fx-btn 之前。
  // 这样 row 顺序回到 [Py, input, fx]，按钮位置稳定。
  if (wrap._fxHiddenInput === origInput) {
    delete wrap._fxHiddenInput;
    const row = wrap.querySelector(".expr-input-row");
    if (row && !row.contains(origInput)) {
      const fxBtn = row.querySelector(".fx-btn");
      if (fxBtn) {
        row.insertBefore(origInput, fxBtn);
      } else {
        row.appendChild(origInput);
      }
    }
  }
  origInput.style.display = "";
}

// 条件左值/右值：${...} 引用保持字符串，其余按 JSON 字面量解析（数组/对象/数字/布尔）。
function literalField(labelText, value, onChange, required, rawKey) {
  const input = document.createElement("input");
  input.type = "text";
  input.value = typeof value === "string" || value === undefined ? value ?? "" : JSON.stringify(value);
  input.placeholder = "${steps.x.outputs.y} 或字面量";
  attachRefCompletion(input);
  input.addEventListener("focus", () => pushUndo());
  input.addEventListener("change", () => {
    input.style.borderColor = "";
    const raw = input.value.trim();
    if (raw === "") { onChange(""); return; }
    if (raw.startsWith("${")) { onChange(raw); return; }
    if (/^[[{0-9tfn"-]/.test(raw)) {
      try { onChange(JSON.parse(raw)); return; } catch { onChange(raw); return; }
    }
    onChange(raw);
  });
  return wrapField(labelText, required, input, "引用 ${...} 或 JSON 字面量", rawKey);
}

function numberField(labelText, value, onChange, required, rawKey) {
  const input = document.createElement("input");
  input.type = "number";
  input.step = "any";
  input.value = value === undefined || value === null ? "" : String(value);
  input.addEventListener("change", () => {
    pushUndo();
    input.style.borderColor = "";
    if (input.value === "") { onChange(null); return; }
    const parsed = Number(input.value);
    if (Number.isNaN(parsed)) { input.style.borderColor = "var(--bad)"; return; }
    onChange(parsed);
  });
  return wrapField(labelText, required, input, undefined, rawKey);
}

function checkboxField(labelText, required, value, onChange, rawKey) {
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = value;
  input.addEventListener("change", () => { pushUndo(); onChange(input.checked); });
  return wrapField(labelText, required, input, undefined, rawKey);
}

function selectField(labelText, propSchema, required, value, onChange, rawKey) {
  const input = document.createElement("select");
  const empty = document.createElement("option");
  empty.value = "";
  // 空选项文案按字段语义区分：transport/channel 的"不填"有明确缺省行为，说清楚而不是"未设置"
  const emptyLabels = {
    transport: "（缺省：自研插件优先）",
    channel: "（缺省：跟随扩展宿主）",
  };
  empty.textContent = emptyLabels[rawKey] || "（未设置）";
  input.appendChild(empty);
  for (const option of propSchema.enum) {
    const element = document.createElement("option");
    element.value = String(option);
    // 选项文案优先级：manifest x-enum-labels（指令自带，随 catalog 下发）> i18n ops > 原值
    const labels = propSchema["x-enum-labels"];
    element.textContent = (labels && labels[option]) || (I18N && I18N.ops[option]) || String(option);
    input.appendChild(element);
  }
  input.value = value === undefined ? "" : String(value);
  input.addEventListener("change", () => {
    pushUndo();
    onChange(input.value === "" ? null : input.value);
  });
  return wrapField(labelText, required, input, undefined, rawKey);
}

function jsonField(labelText, value, onChange, required, rawKey) {
  const input = document.createElement("textarea");
  input.value = value === undefined || value === null ? "" : JSON.stringify(value, null, 2);
  input.placeholder = "{}";
  input.addEventListener("change", () => {
    pushUndo();
    input.style.borderColor = "";
    if (input.value.trim() === "") { onChange(null); return; }
    try {
      onChange(JSON.parse(input.value));
    } catch {
      input.style.borderColor = "var(--bad)";
    }
  });
  return wrapField(labelText, required, input, "JSON 对象；值支持 ${...} 引用", rawKey);
}

// ---------------------------------------------------------------------------
// 编译回显与打开/保存
// ---------------------------------------------------------------------------

function showCompileMessage(text, ok) {
  const panel = $("compile-panel");
  panel.textContent = "";
  const line = document.createElement("span");
  line.className = ok ? "ok" : "bad";
  line.textContent = text;
  panel.appendChild(line);
}

function renderCompileResult(data) {
  const panel = $("compile-panel");
  panel.textContent = "";
  const badge = $("valid-badge");
  badge.classList.remove("hidden");
  if (data.valid) {
    badge.textContent = "✓ valid";
    badge.className = "ok";
    const line = document.createElement("span");
    line.className = "ok";
    line.textContent = `编译通过 · catalog ${String(data.catalog_digest).slice(0, 12)}`;
    panel.appendChild(line);
  } else {
    badge.textContent = "✗ invalid";
    badge.className = "bad";
    for (const error of data.errors) {
      const line = document.createElement("div");
      line.className = "error-line bad";
      line.textContent = (error.path ? `${error.path}: ` : "") + error.message;
      panel.appendChild(line);
    }
  }
}

async function openWorkflow(name) {
  try {
    const doc = await api("GET", `/api/workflows/${encodeURIComponent(name)}`);
    if (!doc.root || doc.root.type !== "sequence") {
      showCompileMessage("画布要求 sequence 根的工作流", false);
      return;
    }
    state.workflow = doc;
    state.selected = null;
    state.multi = [];
    state.undo = [];
    state.redo = [];
    $("file-name").value = name;
    clearDirty();
    render();
    loadElements();
    showCompileMessage(`已打开 ${name}`, true);
  } catch (err) {
    showCompileMessage(String(err.message || err), false);
  }
}

async function saveWorkflow() {
  const name = $("file-name").value.trim();
  if (!NAME_PATTERN.test(name)) {
    showCompileMessage(`非法文件名：${name || "（为空）"}（限字母开头，字母/数字/_/-）`, false);
    return;
  }
  try {
    await api("PUT", `/api/workflows/${encodeURIComponent(name)}`, state.workflow);
  } catch (err) {
    showCompileMessage(String(err.message || err), false);
    return;
  }
  clearDirty();
  await refreshOpenList(name);
  loadElements();
  showCompileMessage(`已保存 workflows/${name}/workflow.json`, true);
}

async function compileWorkflow() {
  try {
    renderCompileResult(await api("POST", "/api/compile", { workflow: state.workflow }));
  } catch (err) {
    showCompileMessage(String(err.message || err), false);
  }
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

function renderGlossary() {
  const list = $("glossary-list");
  if (!list || !I18N) return;
  list.textContent = "";
  for (const [term, explanation] of Object.entries(I18N.glossary)) {
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = explanation;
    list.appendChild(dt);
    list.appendChild(dd);
  }
}

// ---------------------------------------------------------------------------
// 元素库面板：列表 / 插入到选中节点 / 删除 / 回验
// ---------------------------------------------------------------------------

function elementKindLabel(kind) {
  return kind === "browser" ? "网页" : kind === "desktop" ? "桌面" : kind;
}

function elementSummary(element) {
  if (element.kind === "browser") return element.selector && element.selector.css;
  if (element.kind === "desktop") {
    const locator = element.selector && element.selector.locator;
    if (locator) {
      return [locator.automationId, locator.controlType, locator.name]
        .filter(Boolean)
        .join(" · ");
    }
  }
  return JSON.stringify(element.selector || {});
}

function currentFlow() {
  const name = $("file-name").value.trim();
  return NAME_PATTERN.test(name) ? name : "";
}

function elementsBasePath(flow) {
  return `/api/workflows/${encodeURIComponent(flow)}/elements`;
}

async function loadElements() {
  const flow = currentFlow();
  if (!flow) {
    state.elementsSeen = null;
    renderElements([], false);
    return;
  }
  let data;
  try {
    data = await api("GET", elementsBasePath(flow));
  } catch (err) {
    state.elementsSeen = null;
    renderElements([], false);
    return;
  }
  const names = data.elements || [];
  state.elementsSeen = new Set(names);
  renderElements(names, true);
}

// ---------------------------------------------------------------------------
// 捕获自动刷新（切片 F）：页面可见且有命名流程时，2s 轮询元素列表，
// 与已渲染集合做差集，仅在有新增时才重渲染（捕获常在后台 tab 落库）。
// ---------------------------------------------------------------------------

async function pollElements() {
  const flow = currentFlow();
  if (!flow || document.visibilityState !== "visible") return;
  const seen = state.elementsSeen;
  if (!seen) { loadElements(); return; }
  let data;
  try {
    data = await api("GET", elementsBasePath(flow));
  } catch (_) {
    return;
  }
  const names = data.elements || [];
  if (names.length !== seen.size || names.some((name) => !seen.has(name))) {
    state.elementsSeen = new Set(names);
    renderElements(names, true);
  }
}

function startElementPolling() {
  setInterval(pollElements, 2000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") loadElements();
  });
}


function renderElements(names, hasFlow) {
  const list = $("elements-list");
  list.textContent = "";
  if (!hasFlow) {
    const empty = document.createElement("li");
    empty.className = "elements-empty";
    empty.textContent = "先命名或打开流程，显示其元素资产";
    list.appendChild(empty);
    return;
  }
  if (!names.length) {
    const empty = document.createElement("li");
    empty.className = "elements-empty";
    empty.textContent = "空 · 用捕获存入元素";
    list.appendChild(empty);
    return;
  }
  const flow = currentFlow();
  for (const name of names) {
    const li = document.createElement("li");
    li.className = "element-item";
    const title = document.createElement("span");
    title.className = "element-name";
    title.textContent = name;
    title.style.cursor = "pointer";
    title.title = "点击编辑元素";
    title.addEventListener("click", () => editElement(name));
    li.appendChild(title);
    const insert = document.createElement("button");
    insert.textContent = "插入";
    insert.title = "插入到当前选中节点";
    insert.addEventListener("click", () => insertElement(name));
    const verify = document.createElement("button");
    verify.textContent = "验";
    verify.title = "结构校验";
    verify.addEventListener("click", () => verifyElement(name));
    const del = document.createElement("button");
    del.textContent = "✕";
    del.title = "删除";
    del.addEventListener("click", () => deleteElement(name));
    li.appendChild(insert);
    li.appendChild(verify);
    li.appendChild(del);
    list.appendChild(li);
    // 异步补摘要
    api("GET", `${elementsBasePath(flow)}/${encodeURIComponent(name)}`).then((element) => {
      const sub = document.createElement("span");
      sub.className = "element-sub";
      sub.textContent = `${elementKindLabel(element.kind)} · ${elementSummary(element)}`;
      li.appendChild(sub);
    }).catch(() => {});
  }
}

function requireFlow() {
  const flow = currentFlow();
  if (!flow) showCompileMessage("先命名或打开一个流程再操作元素库", false);
  return flow;
}

function insertElement(name) {
  const node = state.selected ? findNode(state.selected) : null;
  if (!node || node.type !== "action") {
    showCompileMessage("先选中一个 action 节点再插入元素", false);
    return;
  }
  const flow = requireFlow();
  if (!flow) return;
  api("GET", `${elementsBasePath(flow)}/${encodeURIComponent(name)}`).then((element) => {
    const manifest = manifestOf(node.command);
    const properties = manifest && manifest.input_schema ? manifest.input_schema.properties || {} : {};
    if (element.kind === "browser" && properties.selector) {
      setWith(node, "selector", element.selector.css);
    } else if (element.kind === "desktop" && properties.locator) {
      setWith(node, "locator", element.selector.locator);
    } else {
      showCompileMessage(
        `元素类型 ${elementKindLabel(element.kind)} 与命令 ${node.command} 不匹配`,
        false,
      );
      return;
    }
    markDirty();
    render();
    showCompileMessage(`已插入元素「${name}」到 ${node.command}`, true);
  }).catch((err) => showCompileMessage(String(err.message || err), false));
}

async function verifyElement(name) {
  const flow = requireFlow();
  if (!flow) return;
  try {
    const result = await api(
      "POST",
      `${elementsBasePath(flow)}/${encodeURIComponent(name)}/verify`,
    );
    if (result.valid) {
      showCompileMessage(`元素「${name}」结构校验通过`, true);
    } else {
      const detail = (result.errors || []).map((e) => `${e.path}: ${e.message}`).join("；");
      showCompileMessage(`元素「${name}」校验失败：${detail}`, false);
    }
  } catch (err) {
    showCompileMessage(String(err.message || err), false);
  }
}

async function deleteElement(name) {
  const flow = requireFlow();
  if (!flow) return;
  if (!confirm(`删除元素「${name}」？`)) return;
  try {
    await api("DELETE", `${elementsBasePath(flow)}/${encodeURIComponent(name)}`);
    showCompileMessage(`已删除元素「${name}」`, true);
    loadElements();
  } catch (err) {
    showCompileMessage(String(err.message || err), false);
  }
}

// ---------------------------------------------------------------------------
// 元素编辑确认对话框（捕获后确认入库 / 元素库面板点元素名编辑）
// ---------------------------------------------------------------------------

function openElementDialog({ mode, flow, descriptor, existingName }) {
  // mode: "confirm"（捕获后确认） | "edit"（元素库编辑）
  return new Promise((resolve) => {
    const mask = $("element-dialog-mask");
    const nameInput = $("element-dialog-name");
    const selectorInput = $("element-dialog-selector");
    const metaEl = $("element-dialog-meta");
    const verifyEl = $("element-dialog-verify");
    const titleEl = $("element-dialog-title");

    titleEl.textContent = mode === "edit" ? "编辑元素" : "捕获确认";
    const defaultName = existingName
      || `${descriptor.metadata?.tag || "element"}_${Date.now() % 100000}`;
    nameInput.value = defaultName;
    selectorInput.value = descriptor.selector?.css
      || JSON.stringify(descriptor.selector?.locator || descriptor.selector || "");
    const meta = descriptor.metadata || {};
    metaEl.textContent = [
      meta.tag && `tag: ${meta.tag}`,
      meta.id && `id: ${meta.id}`,
      meta.classes?.length && `classes: ${meta.classes.join(" ")}`,
      meta.text && `text: ${meta.text}`,
      meta.rect && `rect: ${meta.rect.width}×${meta.rect.height} @ (${meta.rect.x},${meta.rect.y})`,
      descriptor.kind === "desktop" && meta.controlType && `controlType: ${meta.controlType}`,
      descriptor.kind === "desktop" && meta.automationId && `automationId: ${meta.automationId}`,
      descriptor.kind === "desktop" && meta.windowTitle && `window: ${meta.windowTitle}`,
    ].filter(Boolean).join("\n") || "(无 metadata)";
    const vc = descriptor.verifyCount;
    verifyEl.textContent = vc != null ? `捕获时命中 ${vc} 个` : "";
    verifyEl.className = "dlg-verify " + (vc === 1 ? "ok" : "bad");

    const close = (result) => {
      mask.classList.add("hidden");
      resolve(result);
    };

    $("element-dialog-cancel").onclick = () => close(null);
    mask.onclick = (e) => { if (e.target === mask) close(null); };
    $("element-dialog-save").onclick = async () => {
      const name = nameInput.value.trim();
      const selector = selectorInput.value.trim();
      if (!name || !selector) {
        showCompileMessage("元素名和 selector 不能为空", false);
        return;
      }
      // 同名覆盖保护
      if (mode === "confirm") {
        try {
          const existing = await api("GET", `${elementsBasePath(flow)}/${encodeURIComponent(name)}`);
          if (existing && !confirm(`元素「${name}」已存在，覆盖？`)) return;
        } catch { /* 不存在，正常 */ }
      }
      const isBrowser = descriptor.kind === "browser";
      let selectorDoc;
      if (isBrowser) {
        selectorDoc = { css: selector };
      } else {
        try {
          selectorDoc = { locator: JSON.parse(selector) };
        } catch {
          showCompileMessage("desktop locator 不是合法 JSON", false);
          return;
        }
      }
      const doc = {
        kind: descriptor.kind,
        selector: selectorDoc,
        verifyCount: descriptor.verifyCount ?? 0,
        metadata: descriptor.metadata || {},
      };
      try {
        await api("POST", `${elementsBasePath(flow)}/${encodeURIComponent(name)}`, doc);
        loadElements();
        showCompileMessage(`元素「${name}」已入库`, true);
        close(name);
      } catch (err) {
        showCompileMessage(`保存失败：${err.message || err}`, false);
      }
    };
    mask.classList.remove("hidden");
    nameInput.focus();
  });
}

async function editElement(name) {
  const flow = requireFlow();
  if (!flow) return;
  try {
    const descriptor = await api("GET", `${elementsBasePath(flow)}/${encodeURIComponent(name)}`);
    const saved = await openElementDialog({
      mode: "edit", flow, descriptor, existingName: name,
    });
    if (saved && saved !== name) {
      // 改名：删旧存新
      await api("DELETE", `${elementsBasePath(flow)}/${encodeURIComponent(name)}`);
      loadElements();
    }
  } catch (err) {
    showCompileMessage(String(err.message || err), false);
  }
}

async function captureIntoField(node, key) {
  const flow = requireFlow();
  if (!flow) return;
  const descriptor = await captureBrowserElement(flow);
  if (descriptor && descriptor.selector && descriptor.selector.css) {
    setWith(node, key, descriptor.selector.css);
    markDirty();
    render();
    showCompileMessage(
      `已捕获 selector（命中 ${descriptor.verifyCount}）` +
        (descriptor._savedName ? ` 并存入「${descriptor._savedName}」` : "（未入库）"),
      true,
    );
  }
}

// 通用捕获：返回描述符（或 null）。kind: "browser"（extension 无缝）| "desktop"（hover+hybrid）
async function captureElement(kind) {
  const flow = requireFlow();
  if (!flow) return null;
  if (kind === "browser") return captureBrowserElement(flow);
  return captureDesktopElement(flow);
}

async function captureBrowserElement(flow) {
  showCompileMessage("正在启动捕获（扩展无缝模式）…", true);
  let sessionId;
  try {
    const start = await api("POST", "/api/capture/browser/start", {
      transport: "extension",
    });
    sessionId = start.sessionId;
  } catch (err) {
    showCompileMessage(`捕获启动失败：${err.message || err}`, false);
    return null;
  }
  showCompileMessage("捕获中：鼠标移到网页元素上 Ctrl+Click 捕获（Esc 取消）", true);
  try {
    const result = await api("POST", "/api/capture/browser/pick", {
      sessionId,
      timeoutSeconds: 90,
    });
    try { await api("POST", "/api/capture/browser/cancel", { sessionId }); } catch { /* 忽略 */ }
    return await _confirmAndSave(flow, result);
  } catch (err) {
    showCompileMessage(`捕获失败：${err.message || err}`, false);
    return null;
  }
}

async function captureDesktopElement(flow) {
  showCompileMessage("正在启动桌面捕获（hover）…", true);
  let sessionId;
  try {
    const start = await api("POST", "/api/capture/desktop/start", {
      hover: true,
      timeoutSeconds: 90,
    });
    sessionId = start.sessionId;
  } catch (err) {
    showCompileMessage(`捕获启动失败：${err.message || err}`, false);
    return null;
  }
  showCompileMessage("捕获中：鼠标移到目标控件按 F9 或 Ctrl+Click（Esc 取消）", true);
  try {
    const result = await api("POST", "/api/capture/desktop/pick", {
      sessionId,
      timeoutSeconds: 90,
    });
    try { await api("POST", "/api/capture/desktop/cancel", { sessionId }); } catch { /* 忽略 */ }
    return await _confirmAndSave(flow, result);
  } catch (err) {
    showCompileMessage(`捕获失败：${err.message || err}`, false);
    return null;
  }
}

// 捕获后统一确认入库：编辑对话框 → 保存
async function _confirmAndSave(flow, result) {
  if (!(result && result.kind && result.selector)) {
    if (result.cancelled) showCompileMessage("已取消捕获", false);
    else if (result.timeout) showCompileMessage("捕获超时", false);
    return null;
  }
  const savedName = await openElementDialog({
    mode: "confirm", flow, descriptor: result,
  });
  result._savedName = savedName || null;
  return result;
}

function toggleCaptureMenu() {
  $("capture-menu").classList.toggle("hidden");
}

// 运行状态高亮：读最近一次运行的 events，按 node_id 给画布节点标状态色
async function loadRunStatus() {
  const data = await api("GET", "/api/runs/latest-events");
  clearRunStatus();
  if (!data.runId) {
    showCompileMessage("没有运行记录（run_artifacts 为空）", false);
    return;
  }
  const status = {};
  for (const ev of data.events) {
    if (!ev.node_id) continue;
    if (ev.type === "stepStarted" || ev.type === "stepAttemptStarted") {
      status[ev.node_id] = status[ev.node_id] || "running";
    } else if (ev.type === "stepCompleted") {
      status[ev.node_id] = "succeeded";
    } else if (ev.type === "stepFailed") {
      status[ev.node_id] = "failed";
    }
  }
  for (const el of document.querySelectorAll("#canvas li[data-node]")) {
    const s = status[el.dataset.node];
    if (s) el.classList.add(`run-${s}`);
  }
  showCompileMessage(`已高亮运行 ${data.runId.slice(0, 8)}（${Object.keys(status).length} 节点）`, true);
}

function clearRunStatus() {
  for (const el of document.querySelectorAll("#canvas li[data-node]")) {
    el.classList.remove("run-succeeded", "run-failed", "run-running");
  }
}

// ---------------------------------------------------------------------------
// 运行控制（ADR 0011：子进程 run host）
// ---------------------------------------------------------------------------

let activeRunId = null;
let runPollTimer = null;

// ---------------------------------------------------------------------------
// 运行参数对话框（切片 G）：流程声明了顶层 inputs 时，先让用户填/覆盖再运行。
// ---------------------------------------------------------------------------

function collectRunInputs() {
  const workflow = state.workflow || {};
  const declared = workflow.inputs || {};
  const keys = Object.keys(declared);
  return new Promise((resolve) => {
    if (!keys.length) { resolve({}); return; }
    const mask = $("run-params-mask");
    const fields = $("run-params-fields");
    fields.textContent = "";
    for (const key of keys) {
      const raw = declared[key];
      const isBool = typeof raw === "boolean";
      const wrap = document.createElement("div");
      wrap.className = "run-param-field";
      const label = document.createElement("label");
      label.textContent = key;
      label.htmlFor = `run-param-${key}`;
      const input = document.createElement("input");
      input.id = `run-param-${key}`;
      input.value = raw == null ? "" : String(raw);
      if (isBool) { input.type = "checkbox"; input.checked = !!raw; }
      wrap.appendChild(label);
      wrap.appendChild(input);
      fields.appendChild(wrap);
    }
    const read = () => {
      const values = {};
      for (const key of keys) {
        const input = $(`run-param-${key}`);
        const raw = declared[key];
        const isBool = typeof raw === "boolean";
        if (isBool) values[key] = input.checked;
        else if (typeof raw === "number") {
          const n = Number(input.value);
          values[key] = Number.isNaN(n) ? raw : n;
        } else if (raw == null) {
          values[key] = input.value === "" ? null : input.value;
        } else {
          values[key] = input.value;
        }
      }
      return values;
    };
    $("run-params-cancel").onclick = () => { mask.classList.add("hidden"); resolve(null); };
    $("run-params-run").onclick = () => { mask.classList.add("hidden"); resolve(read()); };
    mask.classList.remove("hidden");
    const first = fields.querySelector("input");
    if (first) first.focus();
  });
}

async function runWorkflow() {
  const flow = currentFlow();
  if (!flow) {
    showCompileMessage("先命名或保存流程再运行", false);
    return;
  }
  if (state.dirty && !confirm("流程有未保存修改，先保存再运行？（取消则运行磁盘上的版本）")) return;
  if (state.dirty) await saveWorkflow();
  const inputs = await collectRunInputs();
  if (inputs === null) return; // 用户在参数对话框点了取消
  try {
    const body = { workflow: flow };
    if (Object.keys(inputs).length) body.inputs = inputs;
    const result = await api("POST", "/api/runs", body);
    activeRunId = result.runId;
    $("run-panel").classList.remove("hidden");
    $("btn-run-cancel").classList.remove("hidden");
    renderRunError(null); // 新一次运行先清掉上一轮的失败说明
    $("run-status-text").textContent = `运行中… ${flow} (${activeRunId})`;
    pollRunStatus();
  } catch (err) {
    showCompileMessage(`运行启动失败：${err.message || err}`, false);
  }
}

// 运行失败时把原因摊开：只写「完成：failed」时用户完全不知道"没打开浏览器"从何而来
function renderRunError(result) {
  const box = $("run-error");
  if (!box) return;
  const error = result && result.error;
  if (!error) {
    box.textContent = "";
    box.classList.add("hidden");
    return;
  }
  const details = error.details || {};
  const lines = [
    `失败：${error.code || "ERROR"}${details.nodeId ? ` · 节点 ${details.nodeId}` : ""}`,
  ];
  if (error.message) lines.push(error.message);
  if (details.transport || details.reason) {
    lines.push(`通道：${details.transport || "-"}${details.reason ? `（${details.reason}）` : ""}`);
  }
  if (details.commandId) lines.push(`指令：${details.commandId}`);
  box.textContent = lines.join("\n");
  box.classList.remove("hidden");
}

async function pollRunStatus() {
  if (!activeRunId) return;
  try {
    const s = await api("GET", `/api/runs/${activeRunId}`);
    if (s.running) {
      $("run-status-text").textContent = `运行中… (${activeRunId})`;
      runPollTimer = setTimeout(pollRunStatus, 800);
    } else {
      const result = s.result;
      const status = result ? result.status : `exit ${s.exitCode}`;
      const error = result && result.error;
      const summary = error ? `${status} · ${error.code}` : status;
      $("run-status-text").textContent = `完成：${summary} (${activeRunId})`;
      renderRunError(result);
      $("btn-run-cancel").classList.add("hidden");
      activeRunId = null;
      loadRunEventsOnce();
    }
  } catch (err) {
    showCompileMessage(String(err.message || err), false);
    activeRunId = null;
  }
}

async function cancelRun() {
  if (!activeRunId) return;
  try {
    await api("POST", `/api/runs/${activeRunId}/cancel`);
    showCompileMessage("已取消运行", true);
  } catch (err) {
    showCompileMessage(String(err.message || err), false);
  }
  if (runPollTimer) clearTimeout(runPollTimer);
  activeRunId = null;
  $("btn-run-cancel").classList.add("hidden");
  $("run-status-text").textContent = "已取消";
}

async function loadRunEventsOnce() {
  const panel = $("run-events");
  try {
    // 当前 run 已结束，读最近运行事件（runId 与 activeRunId 解耦）
    const data = await api("GET", "/api/runs/latest-events");
    panel.textContent = "";
    for (const ev of data.events.slice(-30)) {
      const line = document.createElement("div");
      line.className = "run-event-line";
      line.textContent = `${ev.type}${ev.node_id ? " · " + ev.node_id : ""}`;
      panel.appendChild(line);
    }
  } catch { /* 无运行记录 */ }
}

function toggleRunEvents() {
  $("run-events").classList.toggle("hidden");
}

// ---------------------------------------------------------------------------
// 浏览器捕获插件安装引导：开发者模式 Load unpacked（持久可用）。
// 引导四步：①复制/打开源码目录 ②打开浏览器 ③手动打开扩展管理页 ④加载已解压目录。
// 状态（GET /api/extension/status，按 location==4 && path==源码目录 匹配）只读展示。
// ---------------------------------------------------------------------------

const EXTENSION_NAMES = { chrome: "Chrome", edge: "Edge" };

async function loadExtensionStatus() {
  const data = await api("GET", "/api/extension/status");
  $("extension-dir-path").value = data.extensionDir || "";
  renderBrowserButtons("extension-open-browsers", data);
  renderStatusRows("extension-browsers", data);
  await loadExtensionPairing();
}

// 执行通道配对状态：扩展与本机 devserver 必须持有同一 token。
// 插件重装/升级后会换 token，此时扩展每 3s 被 403 重试、通道永远离线——
// 这是"插件重载了却怎么都不生效"的最常见成因，故在此显式暴露 + 一键重置。
async function loadExtensionPairing() {
  const el = $("extension-pair-state");
  if (!el) return;
  try {
    const data = await api("GET", "/api/capture/extension/token");
    el.textContent = data.configured
      ? `已配对（token ${String(data.token || "").slice(0, 12)}…）`
      : "未配对：扩展首次连接时自动配对";
  } catch (err) {
    el.textContent = `配对状态读取失败：${err.message || err}`;
  }
}

// 第 2 步：每浏览器一个「打开浏览器」按钮
function renderBrowserButtons(containerId, data) {
  const box = $(containerId);
  box.textContent = "";
  for (const browser of ["chrome", "edge"]) {
    const row = document.createElement("div");
    row.className = "ext-row";
    const btn = document.createElement("button");
    btn.className = "ext-open-browser";
    btn.dataset.browser = browser;
    btn.textContent = `打开 ${EXTENSION_NAMES[browser]}`;
    btn.addEventListener("click", async (e) => {
      const el = e.currentTarget;
      el.disabled = true;
      try {
        await api("POST", "/api/extension/open-browser", { browser });
        showExtensionResult(`已打开 ${EXTENSION_NAMES[browser]}。下一步：按第 3 步输入扩展页地址。`, true);
      } catch (err) {
        showExtensionResult(`操作失败：${err.message || err}`, false);
      } finally {
        el.disabled = false;
      }
    });
    row.innerHTML = `<span class="ext-name">${EXTENSION_NAMES[browser]}</span>`;
    row.appendChild(btn);
    box.appendChild(row);
  }
}

// 第 3 步：扩展页不能从外部打开，只展示加载状态（打开方式见 HTML 文字）
function renderStatusRows(containerId, data) {
  const box = $(containerId);
  box.textContent = "";
  for (const browser of ["chrome", "edge"]) {
    const info = data.browsers?.[browser] || {};
    const row = document.createElement("div");
    row.className = "ext-row";
    row.innerHTML = `
      <span class="ext-name">${EXTENSION_NAMES[browser]}</span>
      ${statusBadge(info)}
    `;
    box.appendChild(row);
  }
}

function statusBadge(info) {
  if (info.enabled) return '<span class="ext-badge ok">已启用</span>';
  if (info.installed) return '<span class="ext-badge warn">已加载未启用</span>';
  if (info.uninstallBlocked) return '<span class="ext-badge warn">有卸载记录</span>';
  return `<span class="ext-badge bad">未加载</span>`;
}

function showExtensionResult(text, ok = true) {
  const el = $("extension-result");
  el.textContent = text;
  el.className = ok ? "ok" : "bad";
  el.classList.remove("hidden");
}

async function copyExtensionDir() {
  const path = $("extension-dir-path").value;
  if (!path) { showExtensionResult("目录路径为空", false); return; }
  try {
    await navigator.clipboard.writeText(path);
    showExtensionResult(`已复制：${path}`, true);
  } catch (err) {
    showExtensionResult(`复制失败：${err.message || err}`, false);
  }
}

function toggleExtensionDialog(open) {
  const mask = $("extension-dialog-mask");
  const opening = open !== undefined ? open : mask.classList.contains("hidden");
  mask.classList.toggle("hidden", !opening);
  if (opening) {
    $("extension-result").classList.add("hidden");
    loadExtensionStatus().catch((err) =>
      showExtensionResult(String(err.message || err), false)
    );
  }
}

// ---------------------------------------------------------------------------
// 面板可拖拽分栏（切片 A/B）：palette/props 宽度 + 底部元素库高度可拖，记忆。
// 零构建 vanilla：mousedown 起拖 + mousemove 调整，min/max 夹取，松手保存。
// ---------------------------------------------------------------------------

const PANEL_RESIZE_KEY = "rpa_editor_panel_resize";
const PANEL_WIDTH_MIN = 180;
const PANEL_WIDTH_MAX = 600;
const BOTTOM_HEIGHT_MIN = 120;
const BOTTOM_HEIGHT_MAX = 520;

function restorePanelResize() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(PANEL_RESIZE_KEY) || "{}"); } catch (_) { saved = {}; }
  if (saved.palette) $("palette").style.width = `${saved.palette}px`;
  if (saved.props) $("props").style.width = `${saved.props}px`;
  const bottom = $("bottom-panels");
  if (saved.bottom && bottom) {
    bottom.style.height = `${Math.min(BOTTOM_HEIGHT_MAX, Math.max(BOTTOM_HEIGHT_MIN, saved.bottom))}px`;
  }
}

function savePanelResize() {
  const saved = {};
  try { saved.palette = Math.round($("palette").offsetWidth); } catch (_) { }
  try { saved.props = Math.round($("props").offsetWidth); } catch (_) { }
  try { saved.bottom = Math.round($("bottom-panels").offsetHeight); } catch (_) { }
  try { localStorage.setItem(PANEL_RESIZE_KEY, JSON.stringify(saved)); } catch (_) { }
}

function initPanelResize() {
  restorePanelResize();
  document.querySelectorAll(".resize-v").forEach((handle) => {
    handle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const target = handle.dataset.resize === "palette" ? $("palette") : $("props");
      const startX = e.clientX;
      const startWidth = target.offsetWidth;
      document.body.classList.add("resizing-panel");
      handle.classList.add("active");
      const onMove = (ev) => {
        const delta = handle.dataset.resize === "palette"
          ? ev.clientX - startX           // palette 拖右缘向右增宽
          : startX - ev.clientX;          // props 拖左缘向右增宽
        const w = Math.min(PANEL_WIDTH_MAX, Math.max(PANEL_WIDTH_MIN, startWidth + delta));
        target.style.width = `${w}px`;
      };
      const onUp = () => {
        savePanelResize();
        document.body.classList.remove("resizing-panel");
        handle.classList.remove("active");
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    });
  });

  const bottomHandle = $("bottom-resize");
  const bottom = $("bottom-panels");
  if (bottomHandle && bottom) {
    bottomHandle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const startY = e.clientY;
      const startHeight = bottom.offsetHeight;
      document.body.classList.add("resizing-bottom");
      bottomHandle.classList.add("active");
      const onMove = (ev) => {
        // dock 位于页面底部，分隔条在其上沿：向上拖（clientY 减小）增高，向下拖变矮
        const h = startHeight - (ev.clientY - startY);
        bottom.style.height = `${Math.min(BOTTOM_HEIGHT_MAX, Math.max(BOTTOM_HEIGHT_MIN, h))}px`;
      };
      const onUp = () => {
        savePanelResize();
        document.body.classList.remove("resizing-bottom");
        bottomHandle.classList.remove("active");
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    });
  }
}

async function init() {
  const data = await api("GET", "/api/catalog");
  state.catalog = data.commands;
  renderPalette();
  renderGlossary();
  await refreshOpenList();
  newWorkflow();
  $("palette-filter").addEventListener("input", renderPalette);
  // 画布 = 整块 canvas-wrap（含节点外的空白区）：拖放监听绑在 wrap 上，
  // 空画布/空白区落点走 rootEndDrop 兜底（追加到根末尾）。
  const canvasArea = $("canvas-wrap");
  canvasArea.addEventListener("dragover", handleCanvasDragOver);
  canvasArea.addEventListener("drop", handleCanvasDrop);
  canvasArea.addEventListener("dragleave", (e) => {
    if (e.target === canvasArea) clearDropMarkers();
  });
  requestAnimationFrame(autoScrollTick);
  $("btn-new").addEventListener("click", () => { newWorkflow(); showCompileMessage("已新建草稿", true); });
  $("open-select").addEventListener("change", (e) => { if (e.target.value) openWorkflow(e.target.value); });
  $("btn-save").addEventListener("click", saveWorkflow);
  $("btn-compile").addEventListener("click", compileWorkflow);
  $("btn-undo").addEventListener("click", undo);
  $("btn-redo").addEventListener("click", redo);
  $("btn-up").addEventListener("click", () => batchMove(-1));
  $("btn-down").addEventListener("click", () => batchMove(1));
  $("btn-copy").addEventListener("click", copySelection);
  $("btn-delete").addEventListener("click", batchDelete);
  $("btn-elements-refresh").addEventListener("click", loadElements);
  $("file-name").addEventListener("input", loadElements);
  $("btn-element-capture").addEventListener("click", toggleCaptureMenu);
  document.addEventListener("click", (e) => {
    const menu = $("capture-menu");
    if (menu && !menu.classList.contains("hidden")
        && !e.target.closest(".capture-entry")) {
      menu.classList.add("hidden");
    }
  });
  for (const btn of document.querySelectorAll("#capture-menu button")) {
    btn.addEventListener("click", () => {
      $("capture-menu").classList.add("hidden");
      captureElement(btn.dataset.kind);
    });
  }
  $("btn-extension").addEventListener("click", () => toggleExtensionDialog(true));
  $("btn-ext-copy-path").addEventListener("click", copyExtensionDir);
  $("btn-ext-open-dir").addEventListener("click", async () => {
    try {
      const r = await api("POST", "/api/extension/open-dir");
      showExtensionResult(`已在文件管理器中打开：${r.extensionDir}`, true);
    } catch (err) {
      showExtensionResult(`打开失败：${err.message || err}`, false);
    }
  });
  // 仅「关闭」按钮关闭模态框；点击遮罩不关闭（避免误触丢失引导上下文）
  $("extension-dialog-close").addEventListener("click", () => toggleExtensionDialog(false));
  $("btn-ext-reset-pair").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const data = await api("POST", "/api/capture/extension/token", { token: "" });
      showExtensionResult(
        data.reset
          ? "已重置配对：等 3~5 秒，扩展下次轮询会自动重新配对（顶部「扩展通道」徽标应转为在线）"
          : "重置未生效，请手动删除 workflows/.capture-extension-token",
        true,
      );
      await loadExtensionPairing();
    } catch (err) {
      showExtensionResult(`重置失败：${err.message || err}`, false);
    } finally {
      btn.disabled = false;
    }
  });
  $("btn-run-status").addEventListener("click", () => {
    loadRunStatus().catch((err) => showCompileMessage(String(err.message || err), false));
  });
  $("btn-run").addEventListener("click", () => {
    runWorkflow().catch((err) => showCompileMessage(String(err.message || err), false));
  });
  $("btn-run-cancel").addEventListener("click", () => {
    cancelRun().catch((err) => showCompileMessage(String(err.message || err), false));
  });
  $("run-events-toggle").addEventListener("click", toggleRunEvents);
  window.addEventListener("keydown", handleEditorKeydown);
  window.addEventListener("beforeunload", (e) => {
    if (state.dirty || activeRunId) { e.preventDefault(); e.returnValue = ""; }
  });
  initPanelResize();
  startElementPolling();
  loadElements();
  refreshExtChannel();
  setInterval(refreshExtChannel, 5000);
}

// 输入框聚焦时快捷键不劫持（复制粘贴/撤销留给文本编辑）。
function isEditableTarget(target) {
  return !!(
    target &&
    (target.tagName === "INPUT" || target.tagName === "TEXTAREA" ||
      target.tagName === "SELECT" || target.isContentEditable)
  );
}

function handleEditorKeydown(e) {
  if (isEditableTarget(e.target)) return;
  const key = e.key.toLowerCase();
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && key === "c") { copySelection(); e.preventDefault(); return; }
  if (ctrl && key === "v") { pasteClipboard(); e.preventDefault(); return; }
  if (ctrl && key === "z" && !e.shiftKey) { undo(); e.preventDefault(); return; }
  if (ctrl && (key === "y" || (key === "z" && e.shiftKey))) { redo(); e.preventDefault(); return; }
  if (key === "delete" || key === "backspace") { batchDelete(); e.preventDefault(); }
}

init();
