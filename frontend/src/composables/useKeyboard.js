import { onMounted, onUnmounted } from 'vue'
import { useWorkflowStore } from '../stores/workflow'

export function useKeyboard() {
  const store = useWorkflowStore()

  function handleKeyDown(e) {
    const ctrl = e.ctrlKey || e.metaKey
    const shift = e.shiftKey

    // Ctrl+Z: 撤销
    if (ctrl && !shift && e.key === 'z') {
      e.preventDefault()
      store.undo()
      return
    }
    // Ctrl+Shift+Z / Ctrl+Y: 重做
    if ((ctrl && shift && e.key === 'Z') || (ctrl && e.key === 'y')) {
      e.preventDefault()
      store.redo()
      return
    }
    // Delete / Backspace: 删除选中节点
    if ((e.key === 'Delete' || e.key === 'Backspace') && store.selected) {
      // 忽略输入框中的删除
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return
      e.preventDefault()
      store.removeNode(store.selected)
      return
    }
    // Escape: 取消选择
    if (e.key === 'Escape') {
      store.selected = null
      store.multi = []
      return
    }
    // Ctrl+C: 复制
    if (ctrl && e.key === 'c' && store.selected) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return
      e.preventDefault()
      store.copyNode(store.selected)
      return
    }
    // Ctrl+X: 剪切
    if (ctrl && e.key === 'x' && store.selected) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return
      e.preventDefault()
      store.cutNode(store.selected)
      return
    }
    // Ctrl+V: 粘贴
    if (ctrl && e.key === 'v') {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return
      e.preventDefault()
      store.pasteNode()
      return
    }
  }

  onMounted(() => {
    document.addEventListener('keydown', handleKeyDown)
  })

  onUnmounted(() => {
    document.removeEventListener('keydown', handleKeyDown)
  })
}
