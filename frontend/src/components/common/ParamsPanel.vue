<template>
  <div class="params-panel">
    <div class="params-header">
      <span class="params-title">流程参数</span>
      <button class="add-btn" @click="addParam">+ 添加参数</button>
    </div>
    <div class="params-table">
      <div class="table-header">
        <div class="col-name">参数名称</div>
        <div class="col-direction">参数方向</div>
        <div class="col-type">参数类型</div>
        <div class="col-default">默认值</div>
        <div class="col-desc">描述</div>
        <div class="col-action"></div>
      </div>
      <div v-if="!params.length" class="empty-params">
        暂无流程参数
      </div>
      <div
        v-for="(param, i) in params"
        :key="i"
        class="table-row"
      >
        <div class="col-name">
          <input v-model="param.name" placeholder="参数名" class="cell-input" />
        </div>
        <div class="col-direction">
          <select v-model="param.direction" class="cell-select">
            <option value="in">输入</option>
            <option value="out">输出</option>
            <option value="inout">输入输出</option>
          </select>
        </div>
        <div class="col-type">
          <select v-model="param.type" class="cell-select">
            <option value="string">字符串</option>
            <option value="number">数字</option>
            <option value="boolean">布尔</option>
            <option value="array">数组</option>
            <option value="object">对象</option>
          </select>
        </div>
        <div class="col-default">
          <input v-model="param.default" placeholder="默认值" class="cell-input" />
        </div>
        <div class="col-desc">
          <input v-model="param.description" placeholder="描述" class="cell-input" />
        </div>
        <div class="col-action">
          <button class="delete-btn" @click="removeParam(i)">✕</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const params = ref([])

function addParam() {
  params.value.push({
    name: '',
    direction: 'in',
    type: 'string',
    default: '',
    description: '',
  })
}

function removeParam(index) {
  params.value.splice(index, 1)
}
</script>

<style scoped>
.params-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.params-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
}
.params-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}
.add-btn {
  height: 24px;
  padding: 0 8px;
  font-size: 11px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: transparent;
  color: var(--accent);
}
.add-btn:hover {
  background: var(--bg-hover);
}
.params-table {
  flex: 1;
  overflow: auto;
  padding: 8px;
}
.table-header {
  display: flex;
  gap: 8px;
  padding: 8px;
  background: var(--bg-card);
  border-radius: 6px;
  margin-bottom: 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
}
.table-row {
  display: flex;
  gap: 8px;
  padding: 8px;
  border-radius: 6px;
  margin-bottom: 4px;
}
.table-row:hover {
  background: var(--bg-hover);
}
.col-name { flex: 2; }
.col-direction { flex: 1; }
.col-type { flex: 1; }
.col-default { flex: 1.5; }
.col-desc { flex: 2; }
.col-action { width: 32px; }
.cell-input, .cell-select {
  width: 100%;
  height: 28px;
  padding: 0 8px;
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--bg-surface);
  color: var(--text-primary);
}
.cell-input:focus, .cell-select:focus {
  outline: none;
  border-color: var(--accent);
}
.delete-btn {
  width: 24px;
  height: 24px;
  padding: 0;
  font-size: 12px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-muted);
}
.delete-btn:hover {
  background: rgba(239, 68, 68, 0.2);
  color: var(--bad);
}
.empty-params {
  text-align: center;
  padding: 24px;
  color: var(--text-muted);
  font-size: 12px;
}
</style>
