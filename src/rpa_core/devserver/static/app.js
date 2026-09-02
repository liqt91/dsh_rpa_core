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
  if (propSchema.enum) {
    return selectField(label, propSchema, required, value, (v) => setWith(node, key, v), key);
  }
  const type = propSchema.type;
  if (type === "boolean") {
    return checkboxField(label, required, Boolean(value), (v) => setWith(node, key, v), key);
  }
  if (type === "integer" || type === "number") {
    return numberField(label, value, (v) => setWith(node, key, v), required, key);
  }
  if (type === "array" || type === "object") {
    return jsonField(label, value, (v) => setWith(node, key, v), required, key);
  }
  return textField(label, value === undefined ? "" : String(value), (v) => setWith(node, key, v), required, key);
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
  showCompileMessage(`已保存 workflows/${name}.json`, true);
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
  window.addEventListener("keydown", handleEditorKeydown);
  window.addEventListener("beforeunload", (e) => {
    if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
  });
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
