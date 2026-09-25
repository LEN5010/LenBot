import { sourcePath } from '../router/navigation.js'

// 消息保留已读窗口；其他子页仅保留位置、筛选和展开状态，返回后重读业务资料。
// 只存于当前页面内存；登出清空，不写浏览器持久存储或业务数据库。
const visits = new Map()
const limit = 8

export function rememberSceneVisit(route, view) {
  const key = sourcePath(route)
  visits.delete(key)
  visits.set(key, view)
  while (visits.size > limit) visits.delete(visits.keys().next().value)
}

export function sceneVisit(route) {
  return visits.get(sourcePath(route))
}
export function clearSceneVisits() {
  visits.clear()
}
