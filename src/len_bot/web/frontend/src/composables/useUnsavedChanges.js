import { onMounted, onBeforeUnmount, unref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

export function useUnsavedChanges(dirty) {
  const confirmLeave = () => !unref(dirty) || window.confirm('有尚未保存的修改。放弃这些修改并离开？')
  const beforeUnload = event => {
    if (!unref(dirty)) return
    event.preventDefault()
    event.returnValue = ''
  }
  onBeforeRouteLeave(confirmLeave)
  onMounted(() => window.addEventListener('beforeunload', beforeUnload))
  onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
  return { confirmLeave }
}
