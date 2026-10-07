import { onBeforeUnmount, onMounted, ref } from 'vue'

export function useHostEvents(read) {
  const status = ref('connecting')
  let socket = null, reading = false, again = false, active = true

  async function changed() {
    if (reading) { again = true; return }
    reading = true
    try {
      do { again = false; await read() } while (again && active)
    } finally {
      reading = false
    }
  }
  function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const connection = new WebSocket(`${protocol}//${location.host}/api/host/events`)
    socket = connection
    status.value = 'connecting'
    connection.onopen = () => { if (socket === connection) status.value = 'connected' }
    connection.onmessage = () => { if (socket === connection && active) changed() }
    connection.onclose = () => { if (socket === connection && active) status.value = 'disconnected' }
  }
  function reconnect() {
    socket?.close()
    connect()
    changed()
  }
  onMounted(connect)
  onBeforeUnmount(() => { active = false; socket?.close() })
  return { status, reconnect }
}
