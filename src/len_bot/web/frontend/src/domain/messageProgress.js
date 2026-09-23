import { attentionReason, fmtTime } from '../api.js'
import { eventLabel, interactionReason, publicationActionLabel } from './activity.js'

const inputTypes = new Set(['GROUP_MESSAGE_RECEIVED', 'PRIVATE_MESSAGE_RECEIVED'])
const receiptTypes = new Set(['MESSAGE_SENT', 'MESSAGE_SEND_FAILED', 'ACTION_SHADOWED', 'DELIVERY_ATTEMPTED', 'FILE_UPLOADED', 'FILE_UPLOAD_FAILED'])
export function supportsMessageProgress(event) {
  return Boolean(event && (inputTypes.has(event.event_type) || receiptTypes.has(event.event_type) && event.payload?.action_id))
}

// relations 含同轮上下文；这里仅沿明确的来源、提交和行动身份收窄展示。
// 不按时间接近、工具名称或正文猜归属，也不保存新的处理状态。
export function messageRecords(event, relations) {
  const allActions = relations?.actions || [], allTurns = relations?.turns || []
  const actionId = receiptTypes.has(event?.event_type) ? event.payload?.action_id : null
  // 单条回执已取得、关联尚未取得时，仍可展示这份回执本身的事实。
  const selectedAction = actionId ? allActions.find(item => item.id === actionId) || {
    id: actionId, origin_event_id: event.payload.origin_event_id,
    job_id: event.payload.job_id, acknowledges_task_id: event.payload.acknowledges_task_id,
    fulfils_task_id: event.payload.fulfils_task_id, answer_basis: event.payload.answer_basis,
    file_asset_id: event.payload.file_asset_id, file_id: event.payload.file_id,
    delivery_status: event.delivery_status, simulated: event.simulated, origin_mode: event.origin_mode,
    receipt_event_ids: [event.id],
  } : null
  const sourceId = actionId ? selectedAction.origin_event_id || event.payload.origin_event_id : inputTypes.has(event?.event_type) ? event.id : null
  const source = actionId ? relations?.events?.find(item => item.id === sourceId) : event
  const readTurns = sourceId ? allTurns.filter(turn => turn.read_source_event_ids?.includes(sourceId)
    && (!actionId || Boolean(selectedAction?.commit_event_id) && turn.event_id === selectedAction.commit_event_id)) : []
  const handlingTurn = readTurns.find(turn => turn.source_outcomes?.some(item => item.source_event_id === sourceId)
    || turn.handled_source_event_ids?.includes(sourceId))
  const outcome = handlingTurn?.source_outcomes?.find(item => item.source_event_id === sourceId)
  const jobIds = new Set([selectedAction?.job_id, selectedAction?.acknowledges_task_id,
    selectedAction?.fulfils_task_id, selectedAction?.answer_basis?.work_result?.job_id].filter(Boolean))
  const jobs = (relations?.jobs || []).filter(job => actionId ? jobIds.has(job.id) : sourceId && job.request_source_event_id === sourceId)
  const actionIds = new Set(readTurns.flatMap(turn => (turn.source_outcomes || [])
    .filter(item => item.source_event_id === sourceId).flatMap(item => item.action_ids || [])))
  for (const job of jobs) {
    if (job.ack_action_id) actionIds.add(job.ack_action_id)
    if (job.delivery_action_id) actionIds.add(job.delivery_action_id)
  }
  const actions = actionId ? [selectedAction] : allActions.filter(action => sourceId && (action.origin_event_id === sourceId || actionIds.has(action.id)))
  const linkedActionIds = new Set(actions.map(action => action.id))
  const deliveryReceipts = [...new Map([...(relations?.events || []), event].filter(item => item
    && item.scene_id === event?.scene_id
    && ['MESSAGE_SENT', 'MESSAGE_SEND_FAILED', 'FILE_UPLOADED', 'FILE_UPLOAD_FAILED', 'ACTION_SHADOWED'].includes(item.event_type)
    && linkedActionIds.has(item.payload.action_id)).map(item => [item.id, item])).values()]
  const deliveryProblems = deliveryReceipts.filter(item => ['MESSAGE_SEND_FAILED', 'FILE_UPLOAD_FAILED'].includes(item.event_type) && item.payload.error)
  const committed = new Set([...readTurns.map(turn => turn.event_id), ...actions.map(action => action.commit_event_id).filter(Boolean)])
  const attempts = (relations?.traces || []).filter(trace => ['conversation', 'conversation_error'].includes(trace.kind)
    && (actionId ? selectedAction.commit_event_id && trace.commit_event_ids?.includes(selectedAction.commit_event_id)
      : sourceId && (trace.source_event_ids?.includes(sourceId) || trace.read_source_event_ids?.includes(sourceId))))
  const problems = attempts.filter(trace => trace.error || trace.publication_error)
  const episodeIds = new Set([
    ...readTurns.map(turn => turn.episode_id),
    ...actions.map(action => action.episode_id),
    ...attempts.map(trace => trace.ref_id),
  ].filter(Boolean))
  // A shared episode is a round-level relation, not per-message accounting.
  const calls = [...new Map((relations?.calls || [])
    .filter(call => call.scene_id === event?.scene_id && !call.job_id && episodeIds.has(call.episode_id))
    .map(call => [call.id, call])).values()]
    .sort((left, right) => left.started_at - right.started_at || left.id.localeCompare(right.id))
  const requestRecords = attempts.flatMap(trace => (trace.requests || []).map(request => ({ ...request, trace_id: trace.id })))
  return {
    sourceId, source, readTurns, handlingTurn, outcome, jobs, actions, committed, attempts, problems, deliveryProblems, deliveryReceipts, calls, requestRecords, isReceipt: Boolean(actionId),
    pending: relations?.source_handling?.event_id === sourceId && relations.source_handling.pending === true,
    limited: Object.values(relations?.truncated || {}).some(Boolean),
  }
}

const step = (name, state, summary, detail = '') => ({ name, state, summary, detail })
const outcomeLabels = { replied: '已组织回应', delegated: '已委托', waiting: '等待外部回应', incomplete: '本次未完成', silent: '选择旁听' }
const outcomeStates = { replied: 'recorded', delegated: 'waiting', waiting: 'waiting', incomplete: 'partial', silent: 'skipped' }

export function messageProgress(event, relations) {
  const facts = messageRecords(event, relations)
  const { source, readTurns, handlingTurn, outcome, actions, jobs, committed } = facts
  let entry = step('触发与入口', 'unknown', source ? '来源已保存，触发判定未记录' : '尚无可确认的来源入口记录')
  if (source?.interaction?.conversation_excluded) {
    entry = step('触发与入口', 'skipped', '未进入普通对话', interactionReason(source.interaction.interaction_reason) || '独立交互的执行另见记录。')
  } else if (source && Object.hasOwn(source.attention || {}, 'attention_reasons')) {
    const reasons = source.attention.attention_reasons
    entry = reasons.length
      ? step('触发与入口', facts.pending ? 'waiting' : 'recorded', facts.pending ? '读取机会仍待处理' : '已记录读取机会', reasons.map(attentionReason).join(' · '))
      : step('触发与入口', 'skipped', '未产生独立读取机会', '原话仍已保存，也可能被后续对话作为上下文读到。')
    if (source.attention.attention_due_at) entry.detail += `；计划观察截止 ${fmtTime(source.attention.attention_due_at)}，不是正在思考的证明。`
  } else if (source && !inputTypes.has(source.event_type)) {
    entry = step('触发与入口', 'recorded', '已保存运行来源', eventLabel(source.event_type))
  }
  const read = readTurns.length
    ? step('实际阅读', 'recorded', `${readTurns.length} 份提交确认提供了这条来源`, '读取与处理分别记录，不以目录命中或工具取得代替。')
    : step('实际阅读', 'unknown', '未找到已提交的读取事实', '待处理、在途或失败轮次可能尚无此记录，不能据此断言从未读过。')
  const handled = outcome
    ? step('最近已记录处理', outcomeStates[outcome.status] || 'unknown', outcomeLabels[outcome.status] || `未识别状态：${outcome.status}`, outcome.reason || '')
    : handlingTurn
      ? step('最近已记录处理', 'recorded', '已记录处理，方式未细分', '旧记录没有本条来源的具体处理结局。')
      : step('最近已记录处理', 'unknown', '没有本条来源的明确处理结局', readTurns.length ? '本条已读，但同轮整体决定不能冒充它的独立处理结果。' : '')
  const bases = actions.map(action => action.answer_basis).filter(Boolean)
  const sourced = bases.filter(basis => basis.event_ids?.length || basis.result_spans?.length || basis.work_result)
  const gaps = bases.some(basis => basis.unresolved?.length)
  const materials = step('资料与工作', gaps ? 'partial' : sourced.length || jobs.length ? 'recorded' : bases.length ? 'skipped' : 'unknown',
    gaps ? '答复记录了未核实缺口' : sourced.length || jobs.length ? '已有明确资料或工作关联' : bases.length ? '未声明资料引用' : '尚无单条资料或工作依据',
    `${sourced.length} 条表达记有来源依据，${jobs.length} 项工作${facts.isReceipt ? '与此行动明确关联' : '明确由该来源提出'}；不把同轮全部工具结果作为本条依据。`)
  let commit = step('提交与发布', 'unknown', '未找到明确提交身份')
  if (committed.size) {
    const interrupted = actions.some(action => action.publication_status === 'not_enqueued' && action.publication_error)
    const uncertain = actions.some(action => action.publication_status === 'enqueue_unknown')
    const states = [...new Set(actions.map(action => publicationActionLabel(action.publication_status)))]
    commit = step('提交与发布', interrupted ? 'failed' : uncertain ? 'unknown' : 'recorded',
      interrupted ? '已有提交，部分表达未入队且同次发布报告问题' : uncertain ? '已有提交，部分表达入队结果未知' : '已有持久提交',
      states.length ? states.join('；') : '已有读取或处理提交，没有据此证明发送。')
  } else if (actions.length) {
    commit = step('提交与发布', 'unknown', '已有行动关联，提交身份未取得', '只展示已保存身份，不从发送时间反推提交。')
  }
  const counts = { sent: 0, failed: 0, unknown: 0, shadow: 0, simulated: 0, pending: 0, unrecorded: 0 }
  for (const action of actions) {
    if (action.simulated || action.origin_mode === 'simulated') counts.simulated++
    else if (action.delivery_status === 'shadow') counts.shadow++
    else if (action.delivery_status === 'sent' && action.receipt_event_ids?.length) {
      if (action.file_asset_id && !action.file_id) counts.unknown++
      else counts.sent++
    }
    else if (['not_sent', 'rejected'].includes(action.delivery_status)) counts.failed++
    else if (action.delivery_status === 'unknown') counts.unknown++
    else if (action.delivery_status === 'pending' || !action.delivery_status && action.publication_status === 'enqueued') counts.pending++
    else counts.unrecorded++
  }
  const deliveryLabels = { sent: '真实送达', failed: '明确未送达', unknown: '送达未知', shadow: 'Shadow', simulated: '模拟', pending: '待回执', unrecorded: '未记录结局' }
  const delivery = actions.length
    ? step('发送与回执', counts.failed ? 'failed' : counts.unknown || counts.unrecorded ? 'unknown' : counts.pending ? 'waiting' : counts.sent ? 'recorded' : 'skipped',
      `本页 ${actions.length} 条明确归属的行动`, Object.entries(counts).filter(([, count]) => count).map(([key, count]) => `${deliveryLabels[key]} ${count}`).join(' · '))
    : outcome?.status === 'silent' && !facts.limited
      ? step('发送与回执', 'skipped', '已选择旁听，未关联表达', '旁听是正常处理结局，不是发送失败。')
      : step('发送与回执', 'unknown', '未找到归属本条的表达行动', '未记录不等于发送失败，工作结果与实际送达也不是同一阶段。')
  return { ...facts, steps: [entry, read, handled, materials, commit, delivery] }
}
