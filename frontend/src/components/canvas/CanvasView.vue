<template>
  <div
    class="canvas-view"
    @dragover.prevent="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
    @contextmenu.prevent="onCanvasContextMenu"
    :class="{ 'drag-over': isDragOver }"
  >
    <div v-if="!root || !root.children?.length" class="canvas-empty">
      <div class="empty-icon">📋</div>
      <div class="empty-text">拖拽指令到此处创建工作流</div>
      <div class="empty-hint">从左侧面板拖入指令，或点击指令直接添加</div>
    </div>
    <div v-else class="canvas-tree">
      <TreeNode
        v-for="(child, i) in root.children"
        :key="child.id"
        :node="child"
        :depth="0"
        :index="i + 1"
        @contextmenu="onNodeContextMenu"
      />
    </div>
    <ContextMenu
      :visible="ctxMenu.visible"
      :x="ctxMenu.x"
      :y="ctxMenu.y"
      :nodeId="ctxMenu.nodeId"
      @copy="onCtxCopy"
      @cut="onCtxCut"
      @paste="onCtxPaste"
      @delete="onCtxDelete"
      @insert-before="onCtxInsertBefore"
      @insert-after="onCtxInsertAfter"
      @insert-inside="onCtxInsertInside"
      @close="closeCtxMenu"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { useWorkflowStore } from '../../stores/workflow'
import TreeNode from './TreeNode.vue'
import ContextMenu from '../common/ContextMenu.vue'

const store = useWorkflowStore()
const isDragOver = ref(false)

const root = computed(() => store.workflow?.root)

const ctxMenu = reactive({
  visible: false,
  x: 0,
  y: 0,
  nodeId: null,
})

function onDragOver(e) {
  isDragOver.value = true
}

function onDragLeave() {
  isDragOver.value = false
}

function onDrop(e) {
  isDragOver.value = false
  const controlType = e.dataTransfer.getData('application/x-control-type')
  const cmdId = e.dataTransfer.getData('application/x-command')

  if (controlType) {
    store.appendControlNode(controlType)
  } else if (cmdId) {
    store.appendNode(cmdId)
  }
}

function onCanvasContextMenu(e) {
  // 画布空白处右键：不显示菜单
}

function onNodeContextMenu({ nodeId, x, y }) {
  ctxMenu.visible = true
  ctxMenu.nodeId = nodeId
  ctxMenu.x = x
  ctxMenu.y = y
}

function closeCtxMenu() {
  ctxMenu.visible = false
}

function onCtxCopy() {
  store.copyNode(ctxMenu.nodeId)
  closeCtxMenu()
}

function onCtxCut() {
  store.cutNode(ctxMenu.nodeId)
  closeCtxMenu()
}

function onCtxPaste() {
  store.pasteNode(ctxMenu.nodeId)
  closeCtxMenu()
}

function onCtxDelete() {
  store.removeNode(ctxMenu.nodeId)
  closeCtxMenu()
}

function onCtxInsertBefore() {
  const node = store.findNode(ctxMenu.nodeId)
  if (!node) return closeCtxMenu()
  const parent = store.findParent(store.workflow.root, ctxMenu.nodeId)
  if (!parent) return closeCtxMenu()

  store.pushUndo()
  const newNode = store.createActionNode('browser.navigate')
  const list = parent.children || parent.then || []
  const idx = list.findIndex(n => n.id === ctxMenu.nodeId)
  list.splice(idx, 0, newNode)
  store.dirty = true
  store.selected = newNode.id
  closeCtxMenu()
}

function onCtxInsertAfter() {
  const node = store.findNode(ctxMenu.nodeId)
  if (!node) return closeCtxMenu()
  const parent = store.findParent(store.workflow.root, ctxMenu.nodeId)
  if (!parent) return closeCtxMenu()

  store.pushUndo()
  const newNode = store.createActionNode('browser.navigate')
  const list = parent.children || parent.then || []
  const idx = list.findIndex(n => n.id === ctxMenu.nodeId)
  list.splice(idx + 1, 0, newNode)
  store.dirty = true
  store.selected = newNode.id
  closeCtxMenu()
}

function onCtxInsertInside() {
  const node = store.findNode(ctxMenu.nodeId)
  if (!node || !store.isContainer(node)) return closeCtxMenu()

  store.pushUndo()
  const newNode = store.createActionNode('browser.navigate')
  const list = node.children || node.then || []
  list.push(newNode)
  store.dirty = true
  store.selected = newNode.id
  closeCtxMenu()
}
</script>

<style scoped>
.canvas-view {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: var(--canvas);
  transition: background 0.15s;
}
.canvas-view.drag-over {
  background: rgba(59, 130, 246, 0.1);
}
.canvas-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-muted);
}
.empty-icon {
  font-size: 48px;
  margin-bottom: 16px;
  opacity: 0.5;
}
.empty-text {
  font-size: 16px;
  font-weight: 500;
  margin-bottom: 8px;
  color: var(--text-secondary);
}
.empty-hint {
  font-size: 13px;
  color: var(--text-muted);
}
.canvas-tree {
  min-height: 100%;
}
</style>
