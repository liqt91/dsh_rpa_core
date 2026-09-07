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
};
let dragState = null; // {type:"new", command} | {type:"new", flow} | {type:"move", path}
let pendingDrop = null; // {containerPath, key, index}
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
  let lastPath = null;
  for (const source of state.clipboard.nodes) {
    const node = structuredClone(source);
    remapSubtreeIds(node);
    insertNode(containerPath, key, index, node);
    lastPath = [...containerPath, { key, index }];
    index += 1;
  }
  pushUndo(pre);
  selectSingle(lastPath);
  markDirty();
  render();
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
  if (type === "try") return { type: "try", id, children: [], catch: [], error_var: "error" };  return { type: "return", id, value: null };
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

function computeReferencePaths() {
  const paths = [];
  if (!state.workflow) return paths;
  for (const key of Object.keys(state.workflow.inputs || {})) {
    paths.push(`\${inputs.${key}}`);
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

function iconSvg(name) {
  const span = document.createElement("span");
  span.className = "icon";
  span.innerHTML = ICONS ? ICONS.get(name) : "";
  return span;
}

function paletteGroup(name, items, list, icon) {
  if (!items.length) return;
  const header = document.createElement("li");
  header.className = "palette-group";
  if (icon) header.appendChild(iconSvg(icon));
  const text = document.createElement("span");
  text.textContent = name;
  header.appendChild(text);
  list.appendChild(header);
  for (const item of items) list.appendChild(item);
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
  const flowItems = FLOW_ITEMS.map((f) => flowPaletteItem(f, filter)).filter(Boolean);
  paletteGroup("控制流", flowItems, list, "branch");
  const groups = new Map();
  for (const manifest of state.catalog) {
    const prefix = manifest.id.split(".")[0];
    if (!groups.has(prefix)) groups.set(prefix, []);
    const item = commandPaletteItem(manifest, filter);
    if (item) groups.get(prefix).push(item);
  }
  for (const [prefix, items] of groups) paletteGroup(prefix, items, list, GROUP_ICONS[prefix]);
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
  body.appendChild(textField("节点 ID", node.id, (v) => {
    node.id = v;
    markDirty();
    renderCanvas();
  }));
  if (node.type !== "action") {
    renderControlProps(node, body);
    return;
  }
  const commandWrap = document.createElement("div");
  commandWrap.className = "field";
  const commandLabel = document.createElement("label");
  commandLabel.textContent = "命令";
  const commandInput = document.createElement("input");
  commandInput.type = "text";
  commandInput.value = node.command;
  commandInput.readOnly = true;
  commandWrap.appendChild(commandLabel);
  commandWrap.appendChild(commandInput);
  body.appendChild(commandWrap);
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
  if (!keys.length) {
    body.appendChild(jsonField("参数（with，JSON）", node["with"], (v) => {
      if (v === null) node["with"] = {}; else node["with"] = v;
      markDirty();
      renderCanvas();
    }));
  } else {
    const required = new Set(schema.required || []);
    for (const key of keys) {
      body.appendChild(schemaField(node, key, properties[key], required.has(key)));
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

function schemaField(node, key, propSchema, required) {
  const value = node["with"] ? node["with"][key] : undefined;
  const label = fieldLabel(key);
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
    field = textField(label, value === undefined ? "" : String(value), (v) => setWith(node, key, v), required, key);
  }
  if (key === "selector") {
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

const fieldLabel = (key) => (I18N && I18N.fields[key]) || key;

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

function textField(labelText, value, onChange, required, rawKey) {
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
  return wrapField(labelText, required, input, REFERENCE_HINT, rawKey);
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
  empty.textContent = "（未设置）";
  input.appendChild(empty);
  for (const option of propSchema.enum) {
    const element = document.createElement("option");
    element.value = String(option);
    element.textContent = (I18N && I18N.ops[option]) || String(option);
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
    renderElements([], false);
    return;
  }
  let data;
  try {
    data = await api("GET", elementsBasePath(flow));
  } catch (err) {
    renderElements([], false);
    return;
  }
  renderElements(data.elements || [], true);
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

function toggleFullscreen() {
  const wrap = $("canvas-wrap");
  const on = wrap.classList.toggle("fullscreen");
  $("btn-fullscreen").textContent = on ? "⛶ 退出全屏" : "⛶ 全屏";
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

async function runWorkflow() {
  const flow = currentFlow();
  if (!flow) {
    showCompileMessage("先命名或保存流程再运行", false);
    return;
  }
  if (state.dirty && !confirm("流程有未保存修改，先保存再运行？（取消则运行磁盘上的版本）")) return;
  if (state.dirty) await saveWorkflow();
  try {
    const result = await api("POST", "/api/runs", { workflow: flow });
    activeRunId = result.runId;
    $("run-panel").classList.remove("hidden");
    $("btn-run-cancel").classList.remove("hidden");
    $("run-status-text").textContent = `运行中… ${flow} (${activeRunId})`;
    pollRunStatus();
  } catch (err) {
    showCompileMessage(`运行启动失败：${err.message || err}`, false);
  }
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
      $("run-status-text").textContent = `完成：${status} (${activeRunId})`;
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
// 浏览器捕获插件安装入口：状态检测（GET /api/extension/status）+
// 单选安装（POST /api/extension/install）+ 装后去扩展页启用引导
// ---------------------------------------------------------------------------

const EXTENSION_NAMES = { chrome: "Chrome", edge: "Edge" };
const EXTENSION_ENABLE = { chrome: "chrome://extensions", edge: "edge://extensions" };

async function loadExtensionStatus() {
  const data = await api("GET", "/api/extension/status");
  const box = $("extension-browsers");
  box.textContent = "";
  for (const browser of ["chrome", "edge"]) {
    const info = data.browsers?.[browser] || {};
    const row = document.createElement("div");
    row.className = "ext-row";
    const badge = statusBadge(info);
    row.innerHTML = `
      <span class="ext-name">${EXTENSION_NAMES[browser]}</span>
      ${badge}
      <button class="ext-install" data-browser="${browser}" title="写入外部扩展注册表">安装</button>
    `;
    row.querySelector(".ext-install").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = "安装中…";
      try {
        const result = await api("POST", "/api/extension/install", { browser });
        showExtensionResult(
          `已写入 ${EXTENSION_NAMES[browser]} 外部扩展注册表。
           请打开扩展页并点一次「启用」：${EXTENSION_ENABLE[browser]}`
        );
        await loadExtensionStatus();
      } catch (err) {
        showExtensionResult(`安装失败：${err.message || err}`, false);
        btn.disabled = false;
        btn.textContent = "安装";
      }
    });
    box.appendChild(row);
  }
  if (!data.packed) {
    const row = document.createElement("div");
    row.className = "ext-row ext-note";
    row.textContent = "扩展尚未打包；点任一浏览器「安装」会自动打包（需本机 Chrome/Edge）。";
    box.appendChild(row);
  }
}

function statusBadge(info) {
  if (info.enabled) return '<span class="ext-badge ok">已启用</span>';
  if (info.installed) return '<span class="ext-badge warn">已装未启用</span>';
  if (info.registryEntry) return '<span class="ext-badge warn">已登记（待重启）</span>';
  return `<span class="ext-badge bad">未安装</span>`;
}

function showExtensionResult(text, ok = true) {
  const el = $("extension-result");
  el.textContent = text;
  el.className = ok ? "ok" : "bad";
  el.classList.remove("hidden");
}

function toggleExtensionDialog() {
  const mask = $("extension-dialog-mask");
  const opening = mask.classList.contains("hidden");
  mask.classList.toggle("hidden");
  if (opening) {
    $("extension-result").classList.add("hidden");
    loadExtensionStatus().catch((err) =>
      showExtensionResult(String(err.message || err), false)
    );
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
  const canvas = $("canvas");
  canvas.addEventListener("dragover", handleCanvasDragOver);
  canvas.addEventListener("drop", handleCanvasDrop);
  canvas.addEventListener("dragleave", (e) => {
    if (e.target === canvas) clearDropMarkers();
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
  $("btn-fullscreen").addEventListener("click", toggleFullscreen);
  $("btn-extension").addEventListener("click", toggleExtensionDialog);
  $("extension-dialog-close").addEventListener("click", toggleExtensionDialog);
  $("extension-dialog-mask").addEventListener("click", (e) => {
    if (e.target === $("extension-dialog-mask")) toggleExtensionDialog();
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
    if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
  });
  loadElements();
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
