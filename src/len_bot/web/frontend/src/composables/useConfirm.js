import { reactive } from 'vue'

export const confirmState = reactive({ open: false, title: '', text: '', confirmLabel: '确定', danger: false, resolve: null })

export function confirm({ title, text = '', confirmLabel = '确定', danger = false }) {
  confirmState.resolve?.(false)
  return new Promise(resolve => {
    Object.assign(confirmState, { open: true, title, text, confirmLabel, danger, resolve })
  })
}

export function settleConfirm(value) {
  const resolve = confirmState.resolve
  Object.assign(confirmState, { open: false, resolve: null })
  resolve?.(value)
}
