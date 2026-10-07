import { onMounted, onBeforeUnmount, unref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { confirm } from './useConfirm.js'

export function useUnsavedChanges(dirty, { onDiscard, keep = [] } = {}) {
  const others = query => JSON.stringify(Object.entries(query).filter(([key]) => !keep.includes(key)).sort())
  async function confirmLeave() {
    if (!unref(dirty)) return true
    const leave = await confirm({ title: '放弃未保存的修改？', text: '这一页有还没保存的修改，离开后会丢失。', confirmLabel: '放弃修改', danger: true })
    if (leave) onDiscard?.()
    return leave
  }
  const beforeUnload = event => {
    if (!unref(dirty)) return
    event.preventDefault()
    event.returnValue = ''
  }
  onBeforeRouteLeave(confirmLeave)
  onBeforeRouteUpdate((to, from) => to.path === from.path && others(to.query) === others(from.query) ? true : confirmLeave())
  onMounted(() => window.addEventListener('beforeunload', beforeUnload))
  onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
  return { confirmLeave }
}
