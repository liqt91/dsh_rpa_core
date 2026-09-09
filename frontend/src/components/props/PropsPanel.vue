<template>
  <div class="props-panel">
    <template v-if="node">
      <div class="props-header">
        <div class="props-icon" :style="{ background: nodeColor + '15', color: nodeColor }">
          {{ nodeIcon }}
        </div>
        <div class="props-title">
          <div class="title-main">{{ nodeLabel }}</div>
          <div class="title-sub">{{ node.id }}</div>
        </div>
      </div>

      <div v-if="isControlFlow" class="props-section">
        <div class="props-group">
          <label class="props-label">类型</label>
          <span class="props-value">{{ flowLabel }}</span>
        </div>
        <div v-if="node.type === 'if'" class="props-group">
          <label class="props-label">条件</label>
          <input class="props-input" v-model="node.condition" placeholder="例如: ${status} == 'ok'" />
        </div>
        <div v-if="node.type === 'forEach'" class="props-group">
          <label class="props-label">循环变量名</label>
          <input class="props-input" v-model="node.item_var" />
        </div>
        <div v-if="node.type === 'try'" class="props-group">
          <label class="props-label">错误变量名</label>
          <input class="props-input" v-model="node.error_var" />
        </div>
        <div class="props-group">
          <label class="props-label">说明</label>
          <span class="props-hint">{{ flowHint }}</span>
        </div>
      </div>

      <div v-else-if="manifest" class="props-section">
        <template v-for="(schema, key) in fields" :key="key">
          <div v-show="isVisible(key)" class="props-group">
            <label class="props-label">
              {{ fieldLabel(key) }}
              <span v-if="schema.required" class="required">*</span>
            </label>
            <div class="field-row">
              <template v-if="schema.type === 'boolean'">
                <div class="bool-field">
                  <label class="toggle">
                    <input type="checkbox" v-model="node.with[key]" />
                    <span class="toggle-slider"></span>
                  </label>
                  <span class="toggle-label">{{ node.with[key] ? '是' : '否' }}</span>
                </div>
              </template>

              <select v-else-if="schema.enum" class="props-select" v-model="node.with[key]">
                <option v-for="opt in schema.enum" :key="opt" :value="opt">
                  {{ fieldEnumLabel(key, opt) }}
                </option>
              </select>

              <input
                v-else-if="schema.type === 'number' || schema.type === 'integer'"
                class="props-input"
                type="number"
                v-model.number="node.with[key]"
                :placeholder="schema.default"
              />

              <div v-else class="string-field">
                <button
                  v-if="hasFx(key)"
                  class="expr-btn"
                  :class="{ active: exprMode(key) === 'fx' }"
                  @click="toggleFx(key)"
                  title="变量引用"
                >fx</button>
                <div v-if="exprMode(key) === 'fx' && hasFx(key)" class="var-dropdown" ref="dropdownRef">
                  <div v-if="node.with[key]" class="var-tag" @click="showDropdown = !showDropdown">
                    <span class="var-tag-text">{{ node.with[key] }}</span>
                    <button class="var-tag-remove" @click.stop="clearVar(key)">×</button>
                  </div>
                  <div v-else class="var-select" @click="showDropdown = !showDropdown">
                    <span class="var-placeholder">选择变量</span>
                    <span class="var-arrow">▾</span>
                  </div>
                  <div class="var-list" v-show="showDropdown">
                    <div class="var-group-header">用户变量</div>
                    <template v-for="v in filteredVars" :key="v.name">
                      <div
                        v-if="v.type === 'user'"
                        class="var-item"
                        :class="{ selected: node.with[key] === `\${${v.name}}` }"
                        @mousedown.prevent="selectVar(v, key)"
                      >
                        <span class="var-item-icon" style="color: var(--accent)">●</span>
                        {{ v.name }}
                      </div>
                    </template>
                    <div v-if="filteredVars.filter(v => v.type === 'user').length === 0" class="var-empty">暂无用户变量</div>

                    <div class="var-group-header">内置作用域</div>
                    <template v-for="v in filteredVars" :key="v.name">
                      <div
                        v-if="v.type !== 'user'"
                        class="var-item"
                        :class="{ selected: node.with[key] === `\${${v.name}}` }"
                        @mousedown.prevent="selectVar(v, key)"
                      >
                        <span class="var-item-icon" :style="{ color: v.type === 'builtin' ? 'var(--ok)' : v.type === 'loop' ? 'var(--warn)' : 'var(--bad)' }">●</span>
                        {{ v.name }}
                      </div>
                    </template>
                  </div>
                </div>
                <input
                  v-else
                  class="props-input"
                  :class="{ 'py-mode': exprMode(key) === 'py' }"
                  v-model="node.with[key]"
                  :placeholder="stringPlaceholder(key, schema)"
                />
                <button
                  v-if="hasPython(key)"
                  class="expr-btn py"
                  :class="{ active: exprMode(key) === 'py' }"
                  @click="togglePy(key)"
                  title="Python 表达式"
                >Py</button>
              </div>
            </div>
          </div>
        </template>

        <div v-if="outputs && Object.keys(outputs).length > 0" class="props-divider"></div>

        <div v-if="outputs && Object.keys(outputs).length > 0" class="props-group">
          <label class="props-label props-section-label">输出变量</label>
          <div class="outputs-list">
            <div v-for="(meta, field) in outputs" :key="field" class="output-row">
              <span class="output-field-label">{{ meta.label || field }}</span>
              <input
                class="props-input output-alias-input"
                :value="node.output_aliases?.[field] || ''"
                @input="setOutputAlias(field, $event.target.value)"
                :placeholder="meta.primary ? '必填' : '可选'"
                pattern="^[A-Za-z_]\w*$"
              />
            </div>
          </div>
        </div>
      </div>

      <div v-else class="props-section props-empty">
        <span>无命令信息</span>
      </div>
    </template>

    <div v-else class="props-empty-state">
      <div class="empty-icon">📝</div>
      <div class="empty-text">选择一个节点以编辑属性</div>
    </div>
  </div>
</template>

<script setup>
import { computed, watch, ref, onMounted, onUnmounted } from 'vue'
import { useWorkflowStore } from '../../stores/workflow'
import { i18n } from '../../i18n'

const store = useWorkflowStore()
const exprModes = ref({})
const showDropdown = ref(false)
const dropdownRef = ref(null)

const node = computed(() => {
  if (!store.selected) return null
  return store.findNode(store.selected)
})

const manifest = computed(() => {
  if (!node.value?.command) return null
  return store.manifestOf(node.value.command)
})

const fields = computed(() => manifest.value?.input_schema?.properties || {})

const outputs = computed(() => manifest.value?.['x-outputs'] || null)

function setOutputAlias(field, value) {
  if (!node.value) return
  if (!node.value.output_aliases) node.value.output_aliases = {}
  if (value) {
    node.value.output_aliases[field] = value
  } else {
    delete node.value.output_aliases[field]
  }
}

const nodeColor = computed(() => {
  if (node.value?.type === 'action') {
    const cmd = node.value.command || ''
    if (cmd.startsWith('browser.')) return '#3b82f6'
    if (cmd.startsWith('desktop.')) return '#8b5cf6'
    if (cmd.startsWith('data.')) return '#10b981'
  }
  const colors = { sequence: '#64748b', if: '#8b5cf6', forEach: '#f59e0b', try: '#ef4444', return: '#10b981' }
  return colors[node.value?.type] || '#64748b'
})

const nodeIcon = computed(() => {
  const icons = { action: '⚡', sequence: '≡', if: '❓', forEach: '🔄', try: '⚠️', return: '←' }
  if (node.value?.type === 'action') {
    const cmd = node.value.command || ''
    if (cmd.startsWith('browser.')) return '🌐'
    if (cmd.startsWith('desktop.')) return '🖥️'
    if (cmd.startsWith('data.')) return '📊'
  }
  return icons[node.value?.type] || '⚡'
})

const nodeLabel = computed(() => {
  if (node.value?.type === 'action') return store.commandName(node.value.command)
  return i18n.flow[node.value?.type] || node.value?.type || ''
})

const isControlFlow = computed(() => ['if', 'forEach', 'try', 'sequence', 'return'].includes(node.value?.type))
const flowLabel = computed(() => i18n.flow[node.value?.type] || node.value?.type)

const flowHint = computed(() => {
  const hints = {
    if: '当条件为真时执行"满足时"分支，否则执行"否则"分支',
    forEach: '遍历 items 数组，每次迭代将当前元素存入 item_var',
    try: '执行主体命令，出错时进入"出错时"分支',
    sequence: '按顺序依次执行子命令',
    return: '返回结果并终止工作流',
  }
  return hints[node.value?.type] || ''
})

const availableVars = computed(() => {
  return allVars.value.join(', ')
})

const allVars = computed(() => {
  if (!store.workflow?.root) return []
  const vars = []

  // 内置作用域
  if (store.workflow.inputs && Object.keys(store.workflow.inputs).length > 0) {
    for (const key of Object.keys(store.workflow.inputs)) {
      vars.push({ name: `inputs.${key}`, type: 'builtin', label: `输入参数: ${key}` })
    }
  }

  // 用户变量（output_aliases）
  const walk = (node) => {
    if (node.type === 'action' && node.output_aliases) {
      for (const [field, alias] of Object.entries(node.output_aliases)) {
        vars.push({ name: alias, type: 'user', label: `用户变量: ${alias} (${field})` })
      }
    }
    if (node.type === 'forEach' && node.item_var) {
      vars.push({ name: `loop.${node.item_var}`, type: 'loop', label: `循环元素: ${node.item_var}` })
      if (node.index) {
        vars.push({ name: `loop.${node.index}`, type: 'loop', label: `循环索引: ${node.index}` })
      }
    }
    if (node.type === 'try' && node.error_var) {
      vars.push({ name: node.error_var, type: 'error', label: `错误信息: ${node.error_var}` })
    }
    const lists = []
    if (Array.isArray(node.children)) lists.push(...node.children)
    if (Array.isArray(node.then)) lists.push(...node.then)
    if (Array.isArray(node.else)) lists.push(...node.else)
    if (Array.isArray(node.catch)) lists.push(...node.catch)
    for (const child of lists) walk(child)
  }
  walk(store.workflow.root)

  // 去重
  const seen = new Set()
  return vars.filter(v => {
    if (seen.has(v.name)) return false
    seen.add(v.name)
    return true
  })
})

const filteredVars = computed(() => {
  return allVars.value
})

function selectVar(v, key) {
  node.value.with[key] = `\${${v.name}}`
  showDropdown.value = false
}

function clearVar(key) {
  node.value.with[key] = ''
  showDropdown.value = false
}

function onDocClick(e) {
  if (dropdownRef.value && !dropdownRef.value.contains(e.target)) {
    showDropdown.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))

function fieldLabel(key) { return i18n.fields[key] || key }

function fieldEnumLabel(key, value) {
  const labels = {
    transport: { bsk: 'BrowserStateKit', playwright: 'Playwright' },
    clickType: { single: '单击', double: '双击', right: '右键' },
    button: { left: '左键', middle: '中键', right: '右键' },
    mode: { typeChars: '逐字输入', setValues: '设置值' },
    matchMode: { contains: '包含', exact: '精确', regex: '正则' },
    state: { maximize: '最大化', minimize: '最小化', restore: '还原', close: '关闭' },
  }
  return labels[key]?.[value] || value
}

function hasFx(key) { return fields.value[key]?.['x-fx'] === true }
function hasPython(key) { return fields.value[key]?.['x-python'] === true }
function exprMode(key) { return exprModes.value[key] || null }
function toggleFx(key) {
  const isActive = exprModes.value[key] === 'fx'
  exprModes.value[key] = isActive ? null : 'fx'
  if (!isActive) {
    showDropdown.value = true
  }
}
function togglePy(key) { exprModes.value[key] = exprModes.value[key] === 'py' ? null : 'py' }

function stringPlaceholder(key, schema) {
  if (exprModes.value[key] === 'fx') return '输入或选择变量'
  if (exprModes.value[key] === 'py') return 'lambda x: ...'
  return schema.default !== undefined ? String(schema.default) : ''
}

function isVisible(key) {
  const deps = manifest.value?.['x-depends']
  if (!deps || !deps[key]) return true
  const dep = deps[key]
  const val = node.value?.with?.[dep.field]
  if (dep.in !== undefined) return dep.in.includes(val)
  if (dep.eq !== undefined) return val === dep.eq
  if (dep.not_in !== undefined) return !dep.not_in.includes(val)
  return true
}

watch(() => store.selected, () => { exprModes.value = {} })
</script>

<style scoped>
.props-panel {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  min-height: 0;
  background: var(--bg-secondary);
}
.props-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  background: var(--bg-primary);
  border-radius: var(--card-radius);
  border: 1px solid var(--border);
  margin-bottom: 16px;
}
.props-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}
.props-title { flex: 1; min-width: 0; }
.title-main { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.title-sub { font-size: 11px; color: var(--text-muted); font-family: Consolas, monospace; }
.props-section { padding: 0; }
.props-divider {
  height: 1px;
  background: var(--border);
  margin: 12px 0;
}
.props-section-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}
.outputs-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.output-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.output-field-label {
  font-size: 12px;
  color: var(--text-secondary);
  min-width: 80px;
  flex-shrink: 0;
}
.output-alias-input {
  flex: 1;
  height: 32px;
  font-size: 12px;
}
.props-group { margin-bottom: 16px; }
.props-label { display: block; font-size: 12px; font-weight: 500; color: var(--text-secondary); margin-bottom: 6px; }
.bool-field { display: flex; align-items: center; gap: 10px; }
.var-dropdown { position: relative; flex: 1; }
.var-select {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  height: 36px;
  padding: 0 12px;
  font-size: 13px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-muted);
  cursor: pointer;
}
.var-select:hover {
  border-color: var(--border-light);
}
.var-arrow {
  font-size: 12px;
}
.var-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  font-size: 13px;
  font-family: Consolas, monospace;
  background: rgba(59, 130, 246, 0.1);
  color: var(--accent);
  border: 1px solid rgba(59, 130, 246, 0.3);
  border-radius: 6px;
  cursor: pointer;
}
.var-tag:hover {
  background: rgba(59, 130, 246, 0.15);
}
.var-tag-remove {
  width: 16px;
  height: 16px;
  padding: 0;
  font-size: 14px;
  line-height: 1;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
}
.var-tag-remove:hover {
  background: rgba(59, 130, 246, 0.2);
}
.var-list {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  max-height: 160px;
  overflow-y: auto;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  z-index: 100;
  margin-top: 4px;
}
.var-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  font-size: 13px;
  font-family: Consolas, monospace;
  color: var(--text-primary);
  cursor: pointer;
}
.var-item:hover {
  background: var(--bg-hover);
}
.var-item.selected {
  background: rgba(59, 130, 246, 0.1);
  color: var(--accent);
}
.var-item-icon {
  font-size: 8px;
  flex-shrink: 0;
}
.var-group-header {
  padding: 6px 12px 4px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.var-empty {
  padding: 8px 12px;
  font-size: 12px;
  color: var(--text-muted);
  text-align: center;
}
.required { color: var(--bad); }
.props-value { font-size: 13px; color: var(--text-primary); font-family: Consolas, monospace; }
.props-hint { font-size: 12px; color: var(--text-muted); line-height: 1.4; }
.props-input {
  width: 100%;
  height: 36px;
  padding: 0 12px;
  font-size: 13px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-primary);
  font-family: Consolas, monospace;
}
.props-input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.15); }
.props-input.py-mode { background: #1e1e2e; color: #cdd6f4; border-color: #45475a; }
.props-input.fx-mode { background: rgba(59, 130, 246, 0.05); border-color: var(--accent); }
.props-select {
  width: 100%;
  height: 36px;
  padding: 0 12px;
  font-size: 13px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-primary);
}
.props-select:focus { outline: none; border-color: var(--accent); }
.field-row { display: flex; flex-direction: column; gap: 6px; }
.string-field { display: flex; gap: 6px; }
.string-field .props-input { flex: 1; }
.expr-btn {
  width: 36px;
  height: 36px;
  padding: 0;
  font-size: 11px;
  font-weight: 600;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-surface);
  color: var(--text-muted);
  cursor: pointer;
  flex-shrink: 0;
}
.expr-btn:hover { background: var(--bg-hover); }
.expr-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.expr-btn.py { font-size: 10px; }
.toggle { position: relative; display: inline-block; width: 40px; height: 22px; flex-shrink: 0; }
.toggle input { opacity: 0; width: 0; height: 0; }
.toggle-slider { position: absolute; inset: 0; background: #d1d5db; border-radius: 22px; cursor: pointer; transition: 0.2s; }
.toggle-slider::before { content: ''; position: absolute; height: 18px; width: 18px; left: 2px; bottom: 2px; background: #fff; border-radius: 50%; transition: 0.2s; }
.toggle input:checked + .toggle-slider { background: var(--ok); }
.toggle input:checked + .toggle-slider::before { transform: translateX(18px); }
.toggle-label { font-size: 12px; color: var(--text-secondary); align-self: center; }
.field-hint {
  font-size: 11px;
  color: var(--accent);
  background: rgba(59, 130, 246, 0.08);
  padding: 6px 10px;
  border-radius: 6px;
  font-family: Consolas, monospace;
  word-break: break-all;
}
.props-empty { padding: 24px; text-align: center; color: var(--text-muted); }
.props-empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: var(--text-muted); }
.empty-icon { font-size: 40px; margin-bottom: 12px; opacity: 0.5; }
.empty-text { font-size: 13px; }
</style>
