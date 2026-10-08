import type { AssistantHistoryItem, AssistantImageAttachment, AssistantStreamEvent, AssistantThinkingStep, DeliveryContinueFrom, DeliveryPresentation, DeliverySource, GenerateResult, PendingExtraction, PendingPlan, SkillProposal, VisionExtraction, VisualRepairContext } from '../../api/types'
import type { AssistantMessage } from '../../api/types'
import type { PreviewGhostLabel, WorkbenchActionContext } from '../workbenchStoreTypes'
import { detectChatIntent, isResumeMessage } from '../chatIntent'
import { attachmentLabel } from '../../components/assistantImage'
import { classifyAssistantError, formatAssistantRequestError, hydrateSnapshot, normalizeScriptName } from '../workbenchStoreUtils'

/**
 * ST04：从持久候选里挑一个可以继续审批的。rejecting 是已持久化的拒绝意图，
 * 最高优先恢复；approving 是上次审批写盘失败，其次恢复以便重试收敛。
 */
function pickRestorableSkillProposal(proposals: SkillProposal[]): SkillProposal | null {
  const restorable = proposals.filter(
    (p) => p.status === 'draft' || p.status === 'approving' || p.status === 'rejecting',
  )
  if (!restorable.length) return null
  return [...restorable].sort((a, b) => {
    const rejectingDelta = (b.status === 'rejecting' ? 1 : 0) - (a.status === 'rejecting' ? 1 : 0)
    if (rejectingDelta !== 0) return rejectingDelta
    const approvingDelta = (b.status === 'approving' ? 1 : 0) - (a.status === 'approving' ? 1 : 0)
    if (approvingDelta !== 0) return approvingDelta
    return String(b.updated_at ?? b.created_at ?? '').localeCompare(String(a.updated_at ?? a.created_at ?? ''))
  })[0]
}

/** P2a ghost 快照原因：任务前（i18n key，zh/en 见 locales） */
const PREVIEW_GHOST_LABEL_PRE_TASK: PreviewGhostLabel = 'preview.ghost.preTask'

const ASSISTANT_PENDING_PREFIX = 'Thinking...'
// 计划确认门（V3）的待确认/执行中内容：保留 Thinking... 前缀，
// 让 replacePendingAssistantMessage 能正确替换上一条 pending 消息
const PLAN_PENDING_CONTENT = `${ASSISTANT_PENDING_PREFIX}\n📝 修改计划已生成，请确认后执行。`
const PLAN_EXECUTING_CONTENT = `${ASSISTANT_PENDING_PREFIX}\n⏳ 正在按已确认的计划执行修改…`
// 提取确认门（P5d-2）的待确认内容：保留 Thinking... 前缀，让 replacePendingAssistantMessage 能替换
const EXTRACTION_PENDING_CONTENT = `${ASSISTANT_PENDING_PREFIX}\n🖼️ 读图结果已生成，请确认或编辑后继续。`
const EXTRACTION_EXECUTING_CONTENT = `${ASSISTANT_PENDING_PREFIX}\n⏳ 正在按已确认的读图结果生成…`
const EXTRACTION_CANCELLED_CONTENT = '⏹ 已取消本次创建。'
const INTERRUPTED_CONTENT = '⏹ 已中断'

/** 去掉 undefined 键，避免 toEqual/持久化时出现无意义字段 */
function compactExtras<T extends Record<string, unknown>>(extras: T): Partial<T> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(extras)) {
    if (value !== undefined) out[key] = value
  }
  return out as Partial<T>
}

/** ST03 F2：历史发送时附带 delivery/continue 元数据（LLM 载荷仍只用 role/content）。
 *  导出供契约测试使用（卡01：任务反馈与连续对话修复——thinkingSteps 往返）。 */
export function withHistoryMeta(message: AssistantMessage): AssistantHistoryItem {
  const meta: NonNullable<AssistantHistoryItem['meta']> = {}
  if (message.delivery) meta.delivery = message.delivery
  if (message.deliverySource) meta.delivery_source = message.deliverySource
  if (message.deliveryContinueFrom) meta.delivery_continue_from = message.deliveryContinueFrom
  if (message.originalInstruction) meta.original_instruction = message.originalInstruction
  if (message.runId) meta.run_id = message.runId
  if (message.changedFiles?.length) meta.changed_files = message.changedFiles
  if (message.errorCategory) meta.error_category = message.errorCategory
  // 卡05：任务时间线与事件记录关联持久化——重开项目可复盘执行过程
  if (message.thinkingSteps?.length) meta.thinking_steps = message.thinkingSteps
  if (message.turnTaskRef) meta.task_ref = message.turnTaskRef
  if (message.pendingPlan !== undefined) meta.pending_plan = message.pendingPlan
  // 任务类 assistant 消息即使无 delivery 也写入 meta.delivery=null 标记，
  // 便于刷新后区分「本就没有卡」与「旧记录未关联」
  if (message.role === 'assistant' && (message.delivery || message.deliverySource || message.changedFiles?.length)) {
    if (!('delivery' in meta)) meta.delivery = null
  }
  // P2：消息创建时间透传——历史保存不再把全部时间戳覆盖为保存时刻
  const timestamp = message.createdAt ? new Date(message.createdAt).toISOString() : undefined
  return { role: message.role, content: message.content, timestamp, meta: Object.keys(meta).length ? meta : undefined }
}

/**
 * ST03 F2：刷新后 hydrate 历史。
 * - 有 delivery meta → 原样恢复卡片
 * - assistant 任务类消息缺 delivery → 明确 unlinked（旧记录，未关联）
 * - 无任务痕迹的 explain 消息不强行插卡
 */
export function hydrateHistoryMessages(messages: AssistantMessage[]): AssistantMessage[] {
  return (messages ?? []).map((raw) => {
    const message = normalizeHistoryMessage(raw)
    if (message.role !== 'assistant') return message
    // 卡05：恢复任务时间线（meta.thinking_steps）与事件记录关联（meta.task_ref）；
    // 任务类旧记录两者皆缺 → staleTimeline（显示"旧记录未保存执行过程"，不编造）
    const extras = raw as unknown as {
      meta?: Record<string, unknown>
      thinking_steps?: AssistantThinkingStep[]
      task_ref?: import('../../api/types').TurnTaskRef
    }
    const meta = extras.meta ?? {}
    const restoredSteps = (meta.thinking_steps ?? extras.thinking_steps) as AssistantThinkingStep[] | undefined
    const taskRef = (meta.task_ref ?? extras.task_ref) as import('../../api/types').TurnTaskRef | undefined
    if (restoredSteps?.length || taskRef) {
      return {
        ...message,
        thinkingSteps: restoredSteps?.length ? restoredSteps : message.thinkingSteps,
        turnTaskRef: taskRef ?? message.turnTaskRef,
      }
    }
    if (message.delivery) return message
    const looksLikeTaskResult =
      Boolean(message.deliverySource) ||
      Boolean(message.deliveryContinueFrom) ||
      Boolean(message.runId) ||
      Boolean(message.changedFiles?.length) ||
      /Changed files:/i.test(message.content) ||
      /旧记录，未关联/.test(message.content)
    if (!looksLikeTaskResult) {
      // meta 明确写了 delivery:null（保存时的任务消息）
      const rawDelivery = (raw as { delivery?: DeliveryPresentation | null }).delivery
      const nestedDelivery = (raw as { meta?: { delivery?: DeliveryPresentation | null } }).meta?.delivery
      if (rawDelivery === null || nestedDelivery === null) {
        return { ...message, delivery: unlinkedDeliveryPresentation(), staleTimeline: true }
      }
      return message
    }
    return {
      ...message,
      delivery: unlinkedDeliveryPresentation(),
      deliverySource: message.deliverySource ?? null,
      runId: message.runId ?? null,
      staleTimeline: true,
    }
  })
}

/** 后端历史字段（snake_case / meta）→ AssistantMessage */
function normalizeHistoryMessage(raw: AssistantMessage): AssistantMessage {
  const bag = raw as AssistantMessage & Record<string, unknown>
  const meta = (bag.meta ?? {}) as Record<string, unknown>
  const delivery =
    (bag.delivery as DeliveryPresentation | undefined) ??
    (meta.delivery as DeliveryPresentation | undefined)
  const deliverySource =
    (bag.deliverySource as DeliverySource | null | undefined) ??
    ((bag.delivery_source as DeliverySource | null | undefined) ??
      (meta.delivery_source as DeliverySource | null | undefined))
  const deliveryContinueFrom =
    (bag.deliveryContinueFrom as DeliveryContinueFrom | null | undefined) ??
    ((bag.delivery_continue_from as DeliveryContinueFrom | null | undefined) ??
      (meta.delivery_continue_from as DeliveryContinueFrom | null | undefined))
  const originalInstruction =
    (bag.originalInstruction as string | undefined) ??
    ((bag.original_instruction as string | undefined) ??
      (meta.original_instruction as string | undefined))
  const runId =
    (bag.runId as string | null | undefined) ??
    ((bag.run_id as string | null | undefined) ?? (meta.run_id as string | null | undefined))
  const changedFiles =
    (bag.changedFiles as string[] | undefined) ??
    ((bag.changed_files as string[] | undefined) ?? (meta.changed_files as string[] | undefined))
  const pendingPlan = (bag.pendingPlan as PendingPlan | null | undefined)
    ?? (meta.pending_plan as PendingPlan | null | undefined)
  return {
    ...raw,
    delivery: delivery || undefined,
    deliverySource: deliverySource ?? undefined,
    deliveryContinueFrom: deliveryContinueFrom ?? undefined,
    originalInstruction: originalInstruction || undefined,
    runId: runId ?? undefined,
    changedFiles: changedFiles || undefined,
    pendingPlan: pendingPlan ?? undefined,
  }
}

function unlinkedDeliveryPresentation(): DeliveryPresentation {
  return {
    state: null,
    status: 'unlinked',
    unlinked: true,
    headline: '旧记录，未关联交付版本',
    reason: '该记录产生于 delivery_source 契约之前，或刷新后未能恢复关联；无法定位 before/after',
    show_success_badge: false,
    show_before_after: false,
    show_changed_files: false,
    can_recover: false,
    can_continue: false,
    can_view_diff: false,
    diff_target: null,
    recover_revision_id: null,
    before_revision_id: null,
    after_revision_id: null,
    changed_files: [],
    run_id: null,
    error_code: null,
    check_status: 'unknown',
    version_status: null,
    original_instruction: null,
    continued_from: null,
  }
}

/**
 * HF4：把 store 的 assistantMessages 组装成发给后端的对话历史载荷。
 *
 * 规则（与后端 _build_messages / trim_history_messages 护栏对齐，HF5 口径）：
 * - 只带 role/content（卡片字段 changedFiles/verification/acceptance/
 *   visionExtractions/images 等一律剔除，与后端保存的历史同构）；
 * - 跳过 pending 占位（Thinking... 前缀，发送中的临时消息）；
 * - 跳过纯错误消息（errorCategory 存在 → 内容为错误文案）；
 * - interrupted 的 assistant 消息保留（它是真实对话的一部分）；
 * - 截断到最近 limit 条（默认 24 = 12 轮，1 轮 = user + assistant 2 条；
 *   与后端 core._build_messages / pipeline trim_history_messages 同源）。
 *   前端只是预截，最终生效以后端为准（后端还会做 8000 字符预算兜底）；
 * - 图片 b64 不重发；[图N] token 原样留在 content 里（模型能看到当时有图即可）。
 */
export function buildAssistantHistory(
  messages: AssistantMessage[],
  limit = 24,
): AssistantHistoryItem[] {
  return messages
    .filter((m) => {
      if (m.role !== 'user' && m.role !== 'assistant') return false
      if (!m.content.trim()) return false
      if (m.role === 'assistant' && m.content.startsWith(ASSISTANT_PENDING_PREFIX)) return false
      if (m.errorCategory) return false
      return true
    })
    .slice(-limit)
    .map(({ role, content }) => ({ role, content }))
}

/** 导出供契约测试使用（RF02：canonical 事件 → 时间线步骤映射）。 */
export function eventToThinkingStep(event: AssistantStreamEvent): AssistantThinkingStep | null {
  const { type, data } = event
  if (type === 'preparing') {
    // RF02：canonical 事件（与复盘恢复同形状）——阶段/等待步骤
    if (typeof data.message !== 'string' || !data.message) return null
    return {
      type: 'status',
      stage: typeof data.stage === 'string' ? (data.stage as AssistantThinkingStep['stage']) : 'think',
      message: data.message,
    }
  }
  if (type === 'verification') {
    const success = data.state === 'succeeded'
    return {
      type: 'status',
      stage: 'compile',
      message: success ? (typeof data.message === 'string' && data.message ? data.message : '✅ 验证通过') : '❌ 验证未通过',
      detail: !success && typeof data.message === 'string' ? data.message : undefined,
      ok: success,
    }
  }
  if (type === 'tool_finished') {
    const name = typeof data.tool_name === 'string' ? data.tool_name : 'tool'
    return {
      type: 'tool_call',
      stage: 'think',
      message: name,
      detail: typeof data.summary === 'string' ? data.summary : undefined,
      ok: data.state === 'succeeded',
      toolCallId: typeof data.tool_call_id === 'string' ? data.tool_call_id : undefined,
      durationMs: typeof data.duration_ms === 'number' ? data.duration_ms : undefined,
      at: Date.now(),
    }
  }
  if (type === 'public_commentary') {
    // RF02：公开说明显示正文（与 final 分离；合并进最近一条说明行）
    const content = typeof data.message === 'string' ? data.message : ''
    if (!content) return null
    return { type: 'status', stage: 'think', message: '💬 ' + content, commentary: true } as AssistantThinkingStep
  }
  if (type === 'status' && typeof data.message === 'string') {
    return {
      type: 'status',
      stage: typeof data.stage === 'string' ? data.stage : undefined,
      message: data.message,
    }
  }
  if (type === 'tool_started') {
    // 卡05：工具开始就显示（running 态，ok 缺省渲染为进行中，不误标失败）
    const name = typeof data.tool === 'string' ? data.tool : String(data.name ?? 'tool')
    return {
      type: 'tool_call',
      stage: typeof data.stage === 'string' ? data.stage : undefined,
      message: name,
      toolCallId: typeof data.tool_call_id === 'string' ? data.tool_call_id : undefined,
      at: Date.now(),
    }
  }
  if (type === 'tool_call') {
    const name = typeof data.display_name === 'string' ? data.display_name : String(data.name ?? 'tool')
    const summary = typeof data.summary === 'string' ? data.summary : ''
    return {
      type: 'tool_call',
      stage: typeof data.stage === 'string' ? data.stage : undefined,
      message: name,
      detail: summary,
      ok: data.ok === true,
      toolCallId: typeof data.tool_call_id === 'string' ? data.tool_call_id : undefined,
      durationMs: typeof data.duration_ms === 'number' ? data.duration_ms : undefined,
      at: Date.now(),
    }
  }
  if (type === 'plan') {
    return {
      type: 'plan',
      stage: 'plan',
      message: 'AI 计划：' + (typeof data.intent_summary === 'string' ? data.intent_summary : '制定修改方案'),
      intentSummary: typeof data.intent_summary === 'string' ? data.intent_summary : undefined,
      affectedFiles: Array.isArray(data.affected_files) ? data.affected_files.filter((f): f is string => typeof f === 'string') : undefined,
      parameterChanges: Array.isArray(data.parameter_changes) ? data.parameter_changes : undefined,
      strategy: typeof data.strategy === 'string' ? data.strategy : undefined,
    }
  }
  // 卡05：assistant_delta 不再每个 token 占一行（final 答复承载文本；
  // 公开说明由后端合并为 public_commentary 事件）
  if (type === 'compile_result') {
    const success = data.success === true
    return {
      type: 'status',
      stage: 'compile',
      message: success ? '✅ 编译通过' : '❌ 编译失败',
      detail: typeof data.error === 'string' && data.error ? data.error : undefined,
      ok: success,
    }
  }
  return null
}

/**
 * RF05：时间线步骤收束——同 toolCallId 的 start/finish 合并为同一行；
 * 未返回工具按 tool_call_id 去重；其余事件按序追加。
 */
export function pushThinkingStep(steps: AssistantThinkingStep[], step: AssistantThinkingStep): void {
  if (step.at === undefined) step.at = Date.now()
  if (step.type === 'tool_call') {
    if (step.ok === undefined) {
      // start：按 tool_call_id 去重（无 id 不去重）
      if (step.toolCallId && steps.some((s) => s.toolCallId === step.toolCallId)) return
      steps.push(step)
      return
    }
    // finish：优先按 tool_call_id 匹配未完成行，无 id 时按同名最近未完成行回退
    const target = [...steps]
      .reverse()
      .find((s) => s.type === 'tool_call' && s.ok === undefined
        && (step.toolCallId ? s.toolCallId === step.toolCallId : s.message === step.message))
    if (target) {
      target.ok = step.ok
      target.detail = step.detail ?? target.detail
      target.durationMs = step.durationMs ?? target.durationMs
      target.at = step.at
      return
    }
  }
  steps.push(step)
}

/** RF05：终止时未完成的工具不再显示运行中（cancelled/unknown，无旋转假进度）。 */
export function closeRunningSteps(steps: AssistantThinkingStep[]): AssistantThinkingStep[] {
  return steps.map((s) =>
    s.type === 'tool_call' && s.ok === undefined
      ? { ...s, ok: false, message: `${s.message}（未完成）` }
      : s,
  )
}

/** 卡05 错误码 → 用户可操作文案（不跨项目盲重发） */
export function turnErrorText(code: string | null | undefined, fallback: string): string {
  if (code === 'PROJECT_CHANGED') return '项目状态已变化，请重新确认当前项目后重试。'
  if (code === 'SOURCE_CHANGED') return '源码已变化，当前任务令牌已失效；请重新发起修改。'
  if (code === 'PLAN_STALE') return '计划已过期（源码已变化），请重新生成计划。'
  if (code === 'TURN_EXPIRED') return '任务已过期，请重新发起。'
  return fallback
}

/**
 * 卡05：任务事件（卡04 持久化记录）→ 时间线步骤。
 * 重开项目后按 chat meta.task_ref 拉取事件，恢复执行过程；不编造缺失数据。
 */
export function taskEventsToThinkingSteps(events?: Array<import('../../api/types').TaskEvent>): AssistantThinkingStep[] {
  const steps: AssistantThinkingStep[] = []
  for (const event of events ?? []) {
    const message = typeof event.message === 'string' ? event.message : ''
    const at = Date.parse(event.timestamp ?? '') || undefined
    if (event.kind === 'tool_started') {
      pushThinkingStep(steps, {
        type: 'tool_call', stage: 'think', message: event.tool_name ?? 'tool',
        toolCallId: event.tool_call_id ?? undefined, at,
      })
      continue
    }
    if (event.kind === 'tool_finished') {
      pushThinkingStep(steps, {
        type: 'tool_call', stage: 'think', message: event.tool_name ?? 'tool',
        detail: typeof event.summary === 'string' ? event.summary : undefined,
        ok: event.state === 'succeeded',
        toolCallId: event.tool_call_id ?? undefined,
        durationMs: typeof event.duration_ms === 'number' ? event.duration_ms : undefined,
        at,
      })
      continue
    }
    if (event.kind === 'verification') {
      steps.push({
        type: 'status',
        stage: 'compile',
        message: event.state === 'succeeded' ? (message || '✅ 验证通过') : '❌ 验证未通过',
        detail: message && event.state !== 'succeeded' ? message : undefined,
        ok: event.state === 'succeeded',
      })
      continue
    }
    if (event.kind === 'preparing') {
      if (event.stage === 'plan_gate') {
        steps.push({ type: 'status', stage: 'plan', message: '📝 修改计划已生成，待确认。', at })
      } else if (message) {
        steps.push({ type: 'status', stage: (event.stage as AssistantThinkingStep['stage']) ?? 'think', message, at })
      }
      continue
    }
    if (event.kind === 'public_commentary') {
      steps.push({ type: 'status', stage: 'think', message: '💬 ' + message, at })
      continue
    }
    if (event.kind === 'delivery') {
      steps.push({ type: 'status', stage: 'done', message: message || '已交付。', ok: true, at })
      continue
    }
    if (event.kind === 'completed') {
      steps.push({
        type: 'status',
        stage: 'done',
        message: event.state === 'partial' ? '⚠️ 任务部分完成' : message || '✅ 任务完成',
        ok: event.state !== 'partial',
        at,
      })
      continue
    }
    if (event.kind === 'failed' || event.kind === 'cancelled') {
      steps.push({ type: 'status', stage: 'cancel', message: message || (event.kind === 'failed' ? '❌ 任务失败' : '⏹ 已取消'), ok: false, at })
      continue
    }
    if (event.kind === 'source_changed') {
      steps.push({ type: 'status', stage: 'cancel', message: message || '源码已变化', ok: false, at })
    }
  }
  // RF05：恢复路径同样收束——终止/中断时未返回的工具不再永远显示运行中
  return closeRunningSteps(steps)
}

export function createAssistantActions({ api, get, set }: WorkbenchActionContext) {
  function userMessageContent(message: string, images?: AssistantImageAttachment[] | null) {
    const labels = (images ?? []).map((img) => attachmentLabel(img)).join(', ')
    return images && images.length ? `${message}\n[图: ${labels}]` : message
  }

  // R1-02：AI 用户入口在源操作进行中也必须拒绝（sourceActionBusy 反向冲突）。
  function guardSourceBusy(): boolean {
    if (get().sourceActionBusy) {
      set({ lastError: 'A source operation is in progress.' })
      return false
    }
    return true
  }

  async function persistAssistantHistory() {
    // 无项目时不写盘：聊天历史存在 <项目>/.openbrep/ 下，
    // 纯聊天不应触发任何落盘，也避免后端报错污染 lastError
    if (!get().project) return
    // ST03 F2：持久化 delivery/continue 元数据（后端 rewrite 提取 flat/meta）
    const messages = get().assistantMessages.map(withHistoryMeta)
    const result = await api.saveAssistantHistory(messages as AssistantMessage[])
    if (!result.ok && result.error) {
      set({ lastError: result.error })
    }
  }

  // 长操作期间用户切换了项目 → 丢弃过期结果，防止写进新项目的 state
  function projectSwitchedSince(epochAtStart: number) {
    return get().projectEpoch !== epochAtStart
  }

  function discardStaleResult(note: string) {
    set((state) => ({
      assistantBusy: false,
      compileLog: [note, ...state.compileLog].slice(0, 20),
    }))
  }
  // 流式修改执行的统一收尾：最终答复 + 时间线 + 预览刷新 + 历史持久化
  async function finishModifyStream(
    result: GenerateResult,
    epoch: number,
    initialContent: string,
    thinkingSteps: AssistantThinkingStep[],
    originalInstruction?: string,
    taskRef?: import('../../api/types').TurnTaskRef,
    workingIntent?: import('../../api/types').WorkingIntentSnapshot,
    repairContext?: VisualRepairContext,
  ) {
    if (projectSwitchedSince(epoch)) {
      discardStaleResult('Generation result discarded: project switched during the request.')
      return
    }
    const changedFiles = result.assistant?.changed_files ?? []
    const delivery: DeliveryPresentation | undefined = result.assistant?.delivery ?? undefined
    const deliverySource: DeliverySource | null | undefined = result.assistant?.delivery_source
    const continueFrom: DeliveryContinueFrom | null | undefined = result.assistant?.continue_from
    // U05：未产生源码变化时，文案与 changed files 不得伪称已修复
    const noSourceChange = delivery?.status === 'no_change' || delivery?.status === 'unlinked'
    const suffix = changedFiles.length ? `\n\nChanged files: ${changedFiles.join(', ')}` : ''
    const errorCode = (result as { code?: string | null }).code ?? null
    const finalReply =
      result.ok && result.assistant
        ? `${result.assistant.reply}${suffix}`
        : turnErrorText(errorCode, formatAssistantRequestError(result.error, 'Generation request failed.'))
    const closedSteps = closeRunningSteps(thinkingSteps)
    const replyExtras = result.ok
      ? compactExtras({
          changedFiles,
          verification: result.assistant?.verification ?? undefined,
          acceptance: result.assistant?.acceptance ?? undefined,
          thinkingSteps: closedSteps,
          visionExtractions: extractVisionExtractions(result.events),
          delivery,
          deliverySource: deliverySource ?? null,
          deliveryContinueFrom: continueFrom ?? null,
          originalInstruction: originalInstruction || delivery?.original_instruction || undefined,
          runId: result.assistant?.run_id ?? delivery?.run_id ?? null,
          turnTaskRef: taskRef,
          pendingPlan: null,
          workingIntent,
          repairContext,
          knowledgeSources: result.assistant?.knowledge_sources ?? [],
          knowledgeOmissions: result.assistant?.knowledge_omissions ?? [],
        })
      : compactExtras({
          errorCategory: classifyAssistantError(finalReply),
          thinkingSteps: closedSteps,
          delivery,
          deliverySource: deliverySource ?? null,
          originalInstruction: originalInstruction || delivery?.original_instruction || undefined,
          runId: result.assistant?.run_id ?? delivery?.run_id ?? null,
          turnTaskRef: taskRef,
          pendingPlan: null,
          workingIntent,
          repairContext,
          knowledgeSources: result.assistant?.knowledge_sources ?? [],
          knowledgeOmissions: result.assistant?.knowledge_omissions ?? [],
        })
    set((state) => ({
      assistantBusy: false,
      assistantMessages: replacePendingAssistantMessage(state.assistantMessages, finalReply, replyExtras),
      pendingPlan: null,
      lastError: result.ok ? null : finalReply,
      preview: result.preview ?? state.preview,
      warnings: result.warnings ?? result.preview?.warnings ?? state.warnings,
      draftParameters: {},
      // 模式级 skill 提案（P2-d）：成功交付后弹"沉淀提案"确认卡；无提案则清掉旧的
      pendingSkillProposal: result.ok ? (result.skill_proposal ?? null) : state.pendingSkillProposal,
      // continue 载荷已消费（新 run 自己的 delivery_source 才是权威）
      pendingDeliveryContinue: null,
      compileLog: noSourceChange && delivery?.headline
        ? [delivery.headline, ...state.compileLog].slice(0, 20)
        : state.compileLog,
    }))
    await persistAssistantHistory()
    const sourceWasRestored = repairContext?.state.startsWith('restored') ?? false
    if (result.ok || sourceWasRestored) {
      await get().refreshProjectWorkspace({
        preferredScriptName: changedFiles[0] ?? '',
        refreshAllScripts: true,
        refreshPreview: false,
        refreshParameters: true,
        runDiagnostics: true,
      })
    }
  }

  async function finishUnified(result: import('../../api/types').ConversationTurnResult, epoch: number,
    steps: AssistantThinkingStep[], message: string, originalHasImages = false) {
    if (projectSwitchedSince(epoch)) return discardStaleResult('Conversation result discarded: project switched.')
    // 卡05：任务事件记录关联（重开复盘的锚点）
    const taskRef: import('../../api/types').TurnTaskRef | undefined = result.turn_id
      ? { turn_id: result.turn_id, run_id: result.assistant?.run_id ?? null, reference_available: originalHasImages, schema_version: 1 }
      : undefined
    if (result.awaiting_extraction_confirmation && result.extractions?.length) {
      set((state) => ({ assistantBusy: false, pendingExtraction: { turn_id: result.turn_id, extractions: result.extractions!, message, images: [] },
        assistantMessages: replacePendingAssistantMessage(state.assistantMessages, EXTRACTION_PENDING_CONTENT, { turnTaskRef: taskRef, thinkingSteps: [...steps], workingIntent: result.working_intent }) }))
      await persistAssistantHistory()
      return
    }
    if (result.result_kind === 'awaiting_confirmation' && result.pending_plan) {
      const pendingPlan: PendingPlan = {
        ...result.pending_plan,
        turn_id: result.turn_id,
        original_request: message,
        original_has_images: originalHasImages,
      }
      set((state) => ({ assistantBusy: false,
        pendingPlan,
        assistantMessages: replacePendingAssistantMessage(state.assistantMessages, PLAN_PENDING_CONTENT,
          { turnTaskRef: taskRef, thinkingSteps: [...steps], pendingPlan, workingIntent: result.working_intent }),
      }))
      await persistAssistantHistory()
      return
    }
    if (result.result_kind === 'execution' && result.project && result.parameters && result.preview) {
      set(hydrateSnapshot(result as import('../../api/types').WorkbenchSnapshot, get().compilerSettings, get().llmSettings))
      await get().loadScripts()
      await get().loadRevisions()
      await get().loadRecentProjects()
      set((state) => ({ assistantBusy: false, pendingPlan: null, assistantMessages: replacePendingAssistantMessage(state.assistantMessages,
        result.assistant?.reply ?? 'Project created.', { verification: result.assistant?.verification ?? undefined, turnTaskRef: taskRef, pendingPlan: null, workingIntent: result.working_intent,
          knowledgeSources: result.assistant?.knowledge_sources ?? [], knowledgeOmissions: result.assistant?.knowledge_omissions ?? [] }) }))
      await persistAssistantHistory()
      return
    }
    if (result.result_kind === 'execution' || (result.assistant?.delivery && result.result_kind !== 'advice')) {
      await finishModifyStream(result, epoch, ASSISTANT_PENDING_PREFIX, steps, message, taskRef, result.working_intent, result.repair_context)
      if (result.events_recording?.status === 'degraded') markRecordingFailed(result.turn_id)
      return
    }
    const reply = result.ok
      ? result.assistant?.reply ?? '本轮未执行。'
      : turnErrorText(result.code, result.error ?? (result.cancelled ? '⏹ 已取消本轮。' : '本轮未执行。'))
    const refreshedPlan = result.pending_plan
      ? { ...result.pending_plan, turn_id: result.turn_id }
      : null
    set((state) => ({ assistantBusy: false,
      assistantMessages: replacePendingAssistantMessage(state.assistantMessages, reply,
        { advisor: result.advisor, turnTaskRef: taskRef, thinkingSteps: closeRunningSteps(steps),
          recordingFailed: result.events_recording?.status === 'degraded', pendingPlan: refreshedPlan,
          workingIntent: result.working_intent,
          knowledgeSources: result.assistant?.knowledge_sources ?? [],
          knowledgeOmissions: result.assistant?.knowledge_omissions ?? [] }),
      lastError: result.ok ? null : reply,
      pendingPlan: refreshedPlan,
    }))
    // RF04：咨询/失败/取消等全部终端分支都立即落盘聊天正文与 task_ref
    await persistAssistantHistory()
  }

  /** RF03：落盘失败提示贴到对应任务消息（仅会话内存，不阻塞任务） */
  function markRecordingFailed(turnId?: string) {
    if (!turnId) return
    set((state) => ({
      assistantMessages: state.assistantMessages.map((m) =>
        m.turnTaskRef?.turn_id === turnId ? { ...m, recordingFailed: true } : m,
      ),
    }))
  }

  async function executeUnified(turnId: string, epoch: number, message: string,
    signal?: AbortSignal, approval?: PendingPlan, priorSteps: AssistantThinkingStep[] = []) {
    if (projectSwitchedSince(epoch)) return
    const saved = await get().flushDirtyScripts()
    if (!saved.ok) throw new Error(saved.error ?? '草稿保存失败，本轮未执行。')
    if (projectSwitchedSince(epoch) || signal?.aborted) return
    const preview = get().preview
    set({ previewGhost: preview, previewGhostLabel: preview ? PREVIEW_GHOST_LABEL_PRE_TASK : null })
    const steps: AssistantThinkingStep[] = [...priorSteps]
    const result = await api.conversationTurn({ phase: 'execute', turn_id: turnId, stream: true,
      ...(approval ? { approve: true, plan_id: approval.plan_id, plan_version: approval.plan_version } : {}),
    }, (event) => {
      if (projectSwitchedSince(epoch) || event.data.turn_id !== turnId || event.data.project_epoch !== epoch) return
      const step = eventToThinkingStep(event)
      if (step) pushThinkingStep(steps, step)
      set((state) => ({ assistantMessages: replacePendingAssistantMessage(state.assistantMessages,
        PLAN_EXECUTING_CONTENT, { thinkingSteps: [...steps] }) }))
    }, signal)
    return { result, steps }
  }

  async function sendUnified(message: string, images: AssistantImageAttachment[], requestedMode: 'auto' | 'plan',
    approveCreate?: () => Promise<boolean>, proposal?: { id: string; action: 'select' | 'execute' }, confirmBeforeExecute = false) {
    const epoch = get().projectEpoch
    const controller = new AbortController()
    const history = buildAssistantHistory(get().assistantMessages)
    set((state) => ({ assistantBusy: true, chatAbortController: controller, pendingDeliveryContinue: null,
      assistantMessages: [...state.assistantMessages,
        { role: 'user', content: userMessageContent(message, images), images: images.length ? images : undefined, createdAt: Date.now() },
        { role: 'assistant', content: ASSISTANT_PENDING_PREFIX, createdAt: Date.now() }],
    }))
    // 卡05：prepare 也流式——语义路由/咨询/计划生成的阶段即时上时间线
    const prepareSteps: AssistantThinkingStep[] = []
    const prepare = () => api.conversationTurn({ phase: 'prepare', client_turn_id: crypto.randomUUID(),
      message, history, images, requested_mode: proposal?.action === 'select' ? 'consult' : requestedMode,
      confirm_before_execute: confirmBeforeExecute, project_epoch: epoch,
      stream: true,
      // R2（二轮 review）：效果契约的 change_kind 由后端按本轮任务意图确定性
      // 推导（材质/新增选项/参数/几何），不再用"有图片"替代意图判断——
      // "按图把材质改为金属，形状不变"是 material 任务，强加 geometry 门会
      // 把合法交付误判为 no_effect。显式契约字段仍由调用方按需传入。
      ...(proposal ? { proposal_id: proposal.id, proposal_action: proposal.action } : {}),
      assistant_settings: get().llmSettings.assistant_settings,
      draft_scripts: Object.fromEntries(Object.entries(get().dirtyScripts).filter(([, dirty]) => dirty)
        .map(([name]) => [name, get().scriptContents[name] ?? ''])),
    }, (event) => {
      if (projectSwitchedSince(epoch) || controller.signal.aborted) return
      const step = eventToThinkingStep(event)
      if (!step) return
      pushThinkingStep(prepareSteps, step)
      set((state) => ({ assistantMessages: replacePendingAssistantMessage(state.assistantMessages,
        ASSISTANT_PENDING_PREFIX, { thinkingSteps: [...prepareSteps] }) }))
    }, controller.signal)
    try {
      let result = await prepare()
      if (projectSwitchedSince(epoch) || controller.signal.aborted) return
      if (result.result_kind === 'ready_to_execute' && result.turn_id) {
        if (result.task_intent === 'CREATE' && get().project && !(await approveCreate?.())) {
          result = await api.conversationTurn({ phase: 'execute', turn_id: result.turn_id, approve: false })
        } else {
          let execution = await executeUnified(result.turn_id, epoch, message, controller.signal, undefined, prepareSteps)
          if (execution?.result.code === 'SOURCE_CHANGED' && requestedMode !== 'plan') {
            result = await prepare()
            if (result.result_kind === 'ready_to_execute' && result.turn_id) {
              execution = await executeUnified(result.turn_id, epoch, message, controller.signal, undefined, prepareSteps)
            } else execution = { result, steps: [...prepareSteps] }
          }
          if (execution) {
            await finishUnified(execution.result, epoch, execution.steps, message, images.length > 0)
            return
          }
        }
      }
      await finishUnified(result, epoch, [...prepareSteps], message, images.length > 0)
    } catch (error) {
      if (!projectSwitchedSince(epoch)) {
        set((state) => ({ assistantBusy: false,
          lastError: String(error), assistantMessages: replacePendingAssistantMessage(state.assistantMessages,
            controller.signal.aborted ? INTERRUPTED_CONTENT : String(error),
            { thinkingSteps: closeRunningSteps(prepareSteps) }),
        }))
        // RF04：异常路径同样落盘（中断记录是真实对话的一部分）
        await persistAssistantHistory()
      }
    } finally {
      if (get().chatAbortController === controller) set({ assistantBusy: false, chatAbortController: null })
    }
  }

  async function _createProject(
    message: string,
    images: AssistantImageAttachment[] = [],
    signal?: AbortSignal,
    confirmedExtractions?: VisionExtraction[],
  ) {
    const trimmed = message.trim()
    if (!trimmed) return
    const isConfirm = !!confirmedExtractions
    if (!isConfirm) {
      // 首次发起：追加用户消息 + pending 占位
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [
          ...state.assistantMessages,
          { role: 'user', content: userMessageContent(trimmed, images), images: images.length ? images : undefined },
          { role: 'assistant', content: pendingAssistantMessage('create', images) },
        ],
      }))
    } else {
      // 确认重发：复用已有 pending 消息占位（不重复追加用户消息）
      set({ assistantBusy: true })
    }
    const epoch = get().projectEpoch
    const result = await api.createProjectFromPrompt(
      trimmed,
      get().llmSettings.assistant_settings,
      images,
      signal,
      confirmedExtractions,
    )
    if (projectSwitchedSince(epoch)) {
      discardStaleResult(
        result.ok && result.project
          ? `Project "${result.project.name}" was created, but the workspace switched meanwhile. Open it from recent projects.`
          : 'Create result discarded: project switched during the request.',
      )
      return
    }
    // P5d-2 提取确认门：读图完成 → 弹可编辑卡片，等用户确认/编辑后再生成
    // （extractions 为空说明无提取可确认，落到常规交付/错误分支，不卡门）
    if (result.awaiting_extraction_confirmation && result.extractions && result.extractions.length > 0) {
      set((state) => ({
        assistantBusy: false,
        pendingExtraction: { extractions: result.extractions ?? [], message: trimmed, images },
        assistantMessages: replacePendingAssistantMessage(
          state.assistantMessages,
          EXTRACTION_PENDING_CONTENT,
        ),
      }))
      return
    }
    if (!result.ok || !result.project || !result.parameters || !result.preview) {
      const error = formatAssistantRequestError(result.error, 'Create request failed.')
      set((state) => ({
        assistantBusy: false,
        assistantMessages: replacePendingAssistantMessage(state.assistantMessages, error, {
          errorCategory: classifyAssistantError(error),
        }),
        lastError: error,
      }))
      await persistAssistantHistory()
      return
    }
    set(hydrateSnapshot(result, get().compilerSettings, get().llmSettings))
    await get().loadRecentProjects()
    await get().loadScripts()
    await get().loadRevisions()
    const projectPath = result.project.path ?? ''
    const locationNote = projectPath
      ? `\n\n📁 项目已创建：${projectPath}\n可继续发修改指令（如「把层板数改成 5」），或在编辑器里直接改脚本、参数面板里调参数。`
      : ''
    set((state) => ({
      assistantBusy: false,
      pendingExtraction: null,
      assistantMessages: replacePendingAssistantMessage(
        state.assistantMessages,
        `${result.assistant?.reply ?? 'Project created.'}${locationNote}${formatAssistantEventSummary(result.events)}`,
        {
          verification: result.assistant?.verification ?? undefined,
          // P5d-1：从事件流提取读图结果 → 渲染只读提取卡片
          visionExtractions: extractVisionExtractions(result.events),
        },
      ),
      // 模式级 skill 提案（P2-d）：CREATE 成功交付后弹"沉淀提案"确认卡
      pendingSkillProposal: result.skill_proposal ?? null,
    }))
    await persistAssistantHistory()
  }

  return {
    setActiveRailPanel(panel: '3d' | '2d' | 'inspect' | 'ai') {
      set({ activeRailPanel: panel })
    },

    async loadAssistantHistory() {
      // ST04：项目加载/重启/切换后恢复持久候选（失败不影响历史加载）
      await get().restoreSkillProposals()
      const result = await api.listAssistantHistory()
      if (!result.ok) {
        if (result.error) {
          set({ lastError: result.error })
        }
        return
      }
      // ST03 F2：刷新后恢复 delivery 卡；旧/缺关联记录显示 unlinked
      const hydrated = hydrateHistoryMessages(result.messages ?? [])
      const historicPending = [...hydrated].reverse().find((message) => message.pendingPlan)?.pendingPlan
      // Workbench snapshot may already have restored the live server turn. An
      // empty/older history response must not erase that authoritative state.
      let restoredPending: PendingPlan | null = get().pendingPlan
      if (historicPending) {
        restoredPending = { ...historicPending, restored_display_only: true }
        if (historicPending.turn_id) {
          try {
            const status = await api.conversationTurn({ phase: 'status', turn_id: historicPending.turn_id })
            if (status.result_kind === 'awaiting_confirmation' && status.pending_plan) {
              restoredPending = {
                ...status.pending_plan,
                turn_id: historicPending.turn_id,
                original_request: historicPending.original_request,
                original_has_images: historicPending.original_has_images,
              }
            }
          } catch { /* History still shows the plan as display-only. */ }
        }
      }
      set({ assistantMessages: hydrated, pendingPlan: restoredPending })
      // RF04：进程退出后未完成的任务——从任务索引发现（不依赖前端末次 save），
      // 合成"已开始未结束"条目并恢复其执行过程，不伪造最终答复。
      if (typeof api.listTurnEvents === 'function') {
        try {
          const index = await api.listTurnEvents()
          const knownTurnIds = new Set(
            hydrated.filter((m) => m.turnTaskRef?.turn_id).map((m) => m.turnTaskRef!.turn_id),
          )
          const unterminated = (index.turns ?? []).filter(
            (t) => !t.terminal && !knownTurnIds.has(t.turn_id),
          )
          if (unterminated.length) {
            const synthesized = await Promise.all(unterminated.slice(-5).map(async (turn) => {
              let steps: AssistantThinkingStep[] = []
              if (typeof api.fetchTurnEvents === 'function') {
                try {
                  const events = await api.fetchTurnEvents(turn.turn_id)
                  if (events.ok) steps = taskEventsToThinkingSteps(events.events)
                } catch { /* 读取失败按无过程处理 */ }
              }
              return {
                role: 'assistant' as const,
                content: `⏹ 任务未完成（进程退出前中断），没有最终答复。${turn.message ? `原始目标：${turn.message}` : ''}`,
                createdAt: Date.now(),
                turnTaskRef: { turn_id: turn.turn_id, run_id: turn.run_id ?? null, schema_version: 1 },
                thinkingSteps: steps.length ? steps : undefined,
                staleTimeline: !steps.length,
              }
            }))
            set((state) => ({ assistantMessages: [...state.assistantMessages, ...synthesized] }))
          }
        } catch { /* 任务索引读取失败不影响历史加载 */ }
      }
      // 卡05：按 meta.task_ref 拉取任务事件，恢复执行过程时间线（最近 10 条）
      if (typeof api.fetchTurnEvents === 'function') {
        const taskMessages = hydrated.filter((m) => m.role === 'assistant' && m.turnTaskRef?.turn_id).slice(-10)
        if (taskMessages.length) {
          const restored = await Promise.all(taskMessages.map(async (m) => {
            try {
              const events = await api.fetchTurnEvents(m.turnTaskRef!.turn_id)
              if (!events.ok || !events.events?.length) return null
              return { turnId: m.turnTaskRef!.turn_id, steps: taskEventsToThinkingSteps(events.events) }
            } catch {
              return null
            }
          }))
          const byTurn = new Map<string, AssistantThinkingStep[]>()
          for (const item of restored) {
            if (item && item.steps.length) byTurn.set(item.turnId, item.steps)
          }
          if (byTurn.size) {
            set((state) => ({
              assistantMessages: state.assistantMessages.map((m) => {
                const steps = m.turnTaskRef ? byTurn.get(m.turnTaskRef.turn_id) : undefined
                return steps ? { ...m, thinkingSteps: steps, staleTimeline: false } : m
              }),
            }))
          }
        }
      }
    },

    async restoreSkillProposals() {
      if (typeof api.listSkillProposals !== 'function') return
      try {
        const result = await api.listSkillProposals()
        if (!result?.ok) return
        set({ pendingSkillProposal: pickRestorableSkillProposal(result.proposals ?? []) })
      } catch {
        // best-effort：恢复失败不清空现有卡片，也不阻塞项目加载
      }
    },

    async clearAssistantHistory() {
      const result = await api.clearAssistantHistory()
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to clear assistant history.' })
        return
      }
      set({ assistantMessages: [] })
    },

    resetAssistantConversation() {
      set({ assistantMessages: [], pendingExtraction: null, pendingPlan: null })
    },

    async deleteAssistantMessages(indices: number[]) {
      const remove = new Set(indices)
      set((state) => ({ assistantMessages: state.assistantMessages.filter((_, index) => !remove.has(index)) }))
      await persistAssistantHistory()
    },

    /** P6a：从另一个项目追加合并聊天记录到当前项目（纯文件操作，无 LLM）。 */
    async importAssistantHistory(sourcePath: string) {
      const result = await api.importAssistantHistory(sourcePath)
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to import assistant history.' })
        return
      }
      await get().loadAssistantHistory()
      const imported = result.imported ?? 0
      const sourceName = result.source_name ?? sourcePath
      const note =
        imported > 0
          ? `已从「${sourceName}」导入 ${imported} 条聊天记录（追加合并，不覆盖现有记录）`
          : `项目「${sourceName}」没有可导入的聊天记录`
      set((state) => ({
        compileLog: [note, ...state.compileLog].slice(0, 20),
      }))
    },

    /** P6b：LLM 把当前项目聊天记录整理成指令 → 填入 AI 输入框草稿（不自动发送）。
     *  长操作期间切换项目 → 丢弃结果（projectEpoch 守卫），防止旧项目整理结果
     *  填进新项目的输入框。 */
    async distillAssistantHistory() {
      const epoch = get().projectEpoch
      const result = await api.distillAssistantHistory()
      if (projectSwitchedSince(epoch)) {
        discardStaleResult('Distill result discarded: project switched during the request.')
        return
      }
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to distill assistant history.' })
        return
      }
      const instruction = (result.instruction ?? '').trim()
      if (!instruction) {
        set({ lastError: 'Failed to distill assistant history: empty instruction.' })
        return
      }
      // 只填草稿通道，绝不自动发送；面板监听 seed 后填入输入框并消费
      set({ assistantDraftSeed: instruction })
    },

    consumeAssistantDraftSeed() {
      set({ assistantDraftSeed: null })
    },

    async reviewVisualTurn(turnId: string, force = false) {
      const epoch = get().projectEpoch
      const runId = get().assistantMessages.find((message) => message.turnTaskRef?.turn_id === turnId)?.turnTaskRef?.run_id
      const update = (patch: Partial<AssistantMessage>) => set((state) => ({
        assistantMessages: state.assistantMessages.map((message) =>
          message.turnTaskRef?.turn_id === turnId ? { ...message, ...patch } : message,
        ),
      }))
      update({ visualReviewBusy: true, visualReviewError: undefined })
      try {
        if (!force && runId) {
          const saved = await api.fetchSavedVisualReviews(runId)
          if (projectSwitchedSince(epoch)) return
          const prior = saved.ok ? saved.reports?.[0] : undefined
          if (prior) {
            update({ visualReviewBusy: false, visualReview: prior, visualReviewRestored: true })
            return
          }
        }
        const result = await api.requestVisualReview(turnId, epoch)
        if (projectSwitchedSince(epoch)) return
        if (!result.ok || !result.review) {
          update({ visualReviewBusy: false, visualReviewError: result.error ?? '视觉对照失败。' })
          return
        }
        update({ visualReviewBusy: false, visualReview: result.review, visualReviewError: undefined, visualReviewRestored: false })
      } catch (error) {
        if (!projectSwitchedSince(epoch)) update({ visualReviewBusy: false, visualReviewError: String(error) })
      }
    },

    async repairVisualFinding(reviewId: string, findingId: string) {
      const epoch = get().projectEpoch
      if (get().assistantBusy) return
      set({ assistantBusy: true, lastError: undefined })
      try {
        const result = await api.requestVisualRepair(reviewId, findingId, epoch)
        if (projectSwitchedSince(epoch)) return
        if (!result.ok || !result.pending_plan || !result.turn_id) {
          set({ lastError: result.error ?? '无法准备该视觉差异的修复计划。' })
          return
        }
        const repairTurnId = result.turn_id
        const plan = { ...result.pending_plan, turn_id: repairTurnId, original_has_images: true }
        const repairContext = result.repair
        set((state) => ({
          pendingPlan: plan,
          assistantMessages: [...state.assistantMessages, {
            role: 'assistant', content: '已根据当前视觉证据准备一轮限定范围的修复计划，请审核后决定是否执行。',
            createdAt: Date.now(), pendingPlan: plan,
            turnTaskRef: { turn_id: repairTurnId, run_id: null, reference_available: true, schema_version: 1 },
            repairContext,
          }],
        }))
      } catch (error) {
        if (!projectSwitchedSince(epoch)) set({ lastError: String(error) })
      } finally {
        if (!projectSwitchedSince(epoch)) set({ assistantBusy: false })
      }
    },

    async resolveVisualRepair(turnId: string, reviewId: string, resolution: 'accept' | 'restore') {
      const epoch = get().projectEpoch
      const result = await api.resolveVisualRepair(turnId, reviewId, resolution, epoch)
      if (projectSwitchedSince(epoch)) return
      if (!result.ok || !result.repair_context) {
        set({ lastError: result.error ?? '无法处理视觉修复结果。' })
        return
      }
      set((state) => ({
        assistantMessages: state.assistantMessages.map((item) => item.turnTaskRef?.turn_id === turnId
          ? { ...item, repairContext: result.repair_context }
          : item),
        lastError: undefined,
      }))
      if (resolution === 'restore') {
        await get().refreshProjectWorkspace({ refreshAllScripts: true, refreshPreview: true, refreshParameters: true, runDiagnostics: true })
        await get().loadRevisions()
      }
      await persistAssistantHistory()
    },

    async adoptAssistantMessageCode(index: number) {
      const message = get().assistantMessages[index]
      if (!message || message.role !== 'assistant') {
        set({ lastError: 'Select an assistant message with code to adopt.' })
        return
      }
      const result = await api.extractAssistantCodeBlocks(message.content)
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to extract code from assistant message.' })
        return
      }
      if (!result.blocks.length) {
        set({ lastError: 'No GDL or XML code blocks found in this assistant message.' })
        return
      }
      const normalizedBlocks = result.blocks
        .map((block) => ({
          scriptName: normalizeScriptName(block.script_name || block.path.split('/').pop() || ''),
          content: block.content,
        }))
        .filter((block) => block.scriptName && typeof block.content === 'string')
      if (!normalizedBlocks.length) {
        set({ lastError: 'No supported script files found in this assistant message.' })
        return
      }
      set((state) => {
        const scriptContents = { ...state.scriptContents }
        const dirtyScripts = { ...state.dirtyScripts }
        for (const block of normalizedBlocks) {
          scriptContents[block.scriptName] = block.content
          dirtyScripts[block.scriptName] = true
        }
        return {
          activeScriptName: normalizedBlocks[0].scriptName,
          scriptContents,
          dirtyScripts,
          lastError: null,
          compileLog: [`Adopted code from assistant history: ${normalizedBlocks.map((block) => block.scriptName).join(', ')}`, ...state.compileLog].slice(0, 20),
        }
      })
    },

    async sendAssistantMessage(message: string) {
      if (!guardSourceBusy()) return
      const trimmed = message.trim()
      if (!trimmed) return
      const history = buildAssistantHistory(get().assistantMessages)
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [
          ...state.assistantMessages,
          { role: 'user', content: trimmed },
          { role: 'assistant', content: pendingAssistantMessage('explain') },
        ],
      }))
      const epoch = get().projectEpoch
      const result = await api.askAssistant(trimmed, history)
      if (projectSwitchedSince(epoch)) {
        discardStaleResult('Assistant reply discarded: project switched during the request.')
        return
      }
      const reply =
        result.ok && result.assistant
          ? result.assistant.reply
          : formatAssistantRequestError(result.error, 'Assistant request failed.')
      set((state) => ({
        assistantBusy: false,
        assistantMessages: replacePendingAssistantMessage(
          state.assistantMessages,
          reply,
          result.ok ? {} : { errorCategory: classifyAssistantError(reply) },
        ),
        lastError: result.ok ? null : reply,
        // ST04：显式沉淀请求走 explain 通道时也带回候选
        pendingSkillProposal: result.skill_proposal ?? state.pendingSkillProposal,
      }))
      await persistAssistantHistory()
    },

    async createProjectFromPrompt(message: string, images: AssistantImageAttachment[] = []) {
      if (!guardSourceBusy()) return
      return _createProject(message, images)
    },

    async generateAssistantChanges(message: string, images: AssistantImageAttachment[] = []) {
      if (!guardSourceBusy()) return
      const trimmed = message.trim()
      if (!trimmed) return
      const continueFrom = get().pendingDeliveryContinue
      set({ pendingDeliveryContinue: null })
      const history = buildAssistantHistory(get().assistantMessages)
      set((state) => ({
        assistantBusy: true,
        assistantMessages: [
          ...state.assistantMessages,
          { role: 'user', content: userMessageContent(trimmed, images), images: images.length ? images : undefined },
          { role: 'assistant', content: pendingAssistantMessage('generate', images) },
        ],
      }))
      // 生成基于磁盘上的 HSF，先把编辑器手改落盘，否则会被生成结果静默覆盖
      const flushed = await get().flushDirtyScripts()
      if (!flushed.ok) {
        const error = get().lastError ?? 'Failed to save edited scripts before generation.'
        set((state) => ({
          assistantBusy: false,
          assistantMessages: replacePendingAssistantMessage(state.assistantMessages, error),
        }))
        return
      }
      const epoch = get().projectEpoch
      const result = await api.generateWithAssistant(
        trimmed,
        get().llmSettings.assistant_settings,
        images,
        history,
        undefined,
        { continueFrom },
      )
      if (projectSwitchedSince(epoch)) {
        discardStaleResult('Generation result discarded: project switched during the request.')
        return
      }
      const changedFiles = result.assistant?.changed_files ?? []
      const delivery = result.assistant?.delivery ?? undefined
      const suffix = changedFiles.length ? `\n\nChanged files: ${changedFiles.join(', ')}` : ''
      const eventSummary = formatAssistantEventSummary(result.events)
      const reply =
        result.ok && result.assistant
          ? `${result.assistant.reply}${suffix}${eventSummary}`
          : formatAssistantRequestError(result.error, 'Generation request failed.')
      const replyExtras = result.ok
        ? compactExtras({
            changedFiles,
            verification: result.assistant?.verification ?? undefined,
            acceptance: result.assistant?.acceptance ?? undefined,
            visionExtractions: extractVisionExtractions(result.events),
            delivery,
            deliverySource: result.assistant?.delivery_source ?? null,
            deliveryContinueFrom: result.assistant?.continue_from ?? null,
            originalInstruction:
              continueFrom?.original_instruction || delivery?.original_instruction || undefined,
            runId: result.assistant?.run_id ?? delivery?.run_id ?? null,
          })
        : compactExtras({
            errorCategory: classifyAssistantError(reply),
            delivery,
            deliverySource: result.assistant?.delivery_source ?? null,
            originalInstruction:
              continueFrom?.original_instruction || delivery?.original_instruction || undefined,
            runId: result.assistant?.run_id ?? delivery?.run_id ?? null,
          })
      set((state) => ({
        assistantBusy: false,
        assistantMessages: replacePendingAssistantMessage(state.assistantMessages, reply, replyExtras),
        lastError: result.ok ? null : reply,
        preview: result.preview ?? state.preview,
        warnings: result.warnings ?? result.preview?.warnings ?? state.warnings,
        draftParameters: {},
        pendingDeliveryContinue: null,
        // ST04：非流式 generate 也消费显式/自动 skill 候选，弹审批卡
        pendingSkillProposal: result.ok ? (result.skill_proposal ?? null) : state.pendingSkillProposal,
      }))
      await persistAssistantHistory()
      if (result.ok) {
        await get().refreshProjectWorkspace({
          preferredScriptName: changedFiles[0] ?? '',
          refreshAllScripts: true,
          refreshPreview: false,
          refreshParameters: true,
          runDiagnostics: true,
        })
      }
    },

    // ── Unified chat entry point ───────────────────────────────────────────
    // Detects intent → routes to explain / generate / create.
    // Supports AbortController for ESC / stop-button interruption.
    async sendChat(message: string, images: AssistantImageAttachment[] = [], requestedMode: 'auto' | 'plan' = 'auto', approveCreate?: () => Promise<boolean>, confirmBeforeExecute = false) {
      if (!guardSourceBusy()) return
      const trimmed = message.trim()
      if (!trimmed) return
      if (get().llmSettings.conversation_entry !== 'legacy') return sendUnified(trimmed, images, requestedMode, approveCreate, undefined, confirmBeforeExecute)

      const hasProject = !!get().project
      const interrupted = get().interruptedContext
      const deliveryContinue = get().pendingDeliveryContinue
      // Consume the link once. The pending plan carries its own request metadata;
      // cancellation, aborts, and save failures must not tag the next user task.
      set({ pendingDeliveryContinue: null })

      // Follow-up after an interrupt: "继续" retries the original
      // ST03：delivery continue 已带回原始指令；「继续」只在 interrupt 上下文生效
      let finalMessage = trimmed
      let intent = detectChatIntent(trimmed, hasProject)
      if (interrupted && isResumeMessage(trimmed) && !deliveryContinue) {
        finalMessage = interrupted.message
        intent = detectChatIntent(interrupted.message, hasProject)
      }
      // delivery continue：点「继续」且已有 pendingDeliveryContinue 时，指令已由
      // continueDelivery 注入；这里只保证 intent 按原始指令重判
      if (deliveryContinue && isResumeMessage(trimmed)) {
        finalMessage = deliveryContinue.original_instruction || finalMessage
        intent = detectChatIntent(finalMessage, hasProject)
      }

      // P2a ghost：任务发起时快照"任务前"预览（修改前后对比用）。
      // CREATE（无项目）时 preview 为 null → ghost 置 null；参数防抖刷新 /
      // 手动 Update / 质量档切换都不覆盖；项目切换经 hydrateSnapshot 清空。
      const previewAtTaskStart = get().preview
      set({
        previewGhost: previewAtTaskStart,
        previewGhostLabel: previewAtTaskStart ? PREVIEW_GHOST_LABEL_PRE_TASK : null,
      })

      const controller = new AbortController()
      set({ chatAbortController: controller, interruptedContext: null })

      try {
        if (intent === 'create') {
          // createProjectFromPrompt manages its own pending messages;
          // pass signal so the stop button can abort project creation too.
          await _createProject(finalMessage, images, controller.signal)
        } else if (intent === 'modify') {
          // 计划确认门（V3）：MODIFY 先出非代码语言计划，用户确认后才执行
          const settings = get().llmSettings.assistant_settings ?? ''
          const initialContent = pendingAssistantMessage('generate', images)
          const history = buildAssistantHistory(get().assistantMessages)
          set((state) => ({
            assistantBusy: true,
            assistantMessages: [
              ...state.assistantMessages,
              { role: 'user', content: userMessageContent(finalMessage, images), images: images.length ? images : undefined },
              { role: 'assistant', content: initialContent, thinkingSteps: [] },
            ],
          }))
          const flushed = await get().flushDirtyScripts()
          if (!flushed.ok) {
            const error = get().lastError ?? 'Failed to save scripts before generation.'
            set((state) => ({
              assistantBusy: false,
              assistantMessages: replacePendingAssistantMessage(state.assistantMessages, error),
            }))
            return
          }
          const epoch = get().projectEpoch
          const continueFrom = deliveryContinue
          const planResult = await api.requestModifyPlan(
            finalMessage,
            settings,
            images,
            controller.signal,
            history,
            continueFrom,
          )
          if (projectSwitchedSince(epoch)) {
            discardStaleResult('Generation result discarded: project switched during the request.')
            return
          }
          if (planResult.awaiting_confirmation && planResult.pending_plan) {
            const plan: PendingPlan = planResult.pending_plan
            set((state) => ({
              assistantBusy: false,
              pendingPlan: plan,
              assistantMessages: replacePendingAssistantMessage(
                state.assistantMessages,
                PLAN_PENDING_CONTENT,
                {
                  thinkingSteps: [{
                    type: 'plan',
                    stage: 'plan',
                    message: 'AI 计划：' + plan.intent_summary,
                    intentSummary: plan.intent_summary,
                    affectedFiles: plan.affected_files,
                    userVisibleChanges: plan.user_visible_changes,
                    risk: plan.risk,
                  }],
                },
              ),
            }))
            return
          }
          // 计划失败回落 / micro_modify / V1 DSL 命中：直接展示执行结果
          await finishModifyStream(
            planResult,
            epoch,
            pendingAssistantMessage('generate', images),
            [],
            continueFrom?.original_instruction || finalMessage,
          )
        } else if (intent === 'debug') {
          // DEBUG 不走确认门：默认走 agent loop 流式路径，实时显示每一步事件
          const settings = get().llmSettings.assistant_settings ?? ''
          const initialContent = pendingAssistantMessage('generate', images)
          const history = buildAssistantHistory(get().assistantMessages)
          set((state) => ({
            assistantBusy: true,
            assistantMessages: [
              ...state.assistantMessages,
              { role: 'user', content: userMessageContent(finalMessage, images), images: images.length ? images : undefined },
              { role: 'assistant', content: initialContent, thinkingSteps: [] },
            ],
          }))
          const flushed = await get().flushDirtyScripts()
          if (!flushed.ok) {
            const error = get().lastError ?? 'Failed to save scripts before generation.'
            set((state) => ({
              assistantBusy: false,
              assistantMessages: replacePendingAssistantMessage(state.assistantMessages, error),
            }))
            return
          }
          const epoch = get().projectEpoch
          const thinkingSteps: AssistantThinkingStep[] = []
          const debugContinueFrom = deliveryContinue

          const result = await api.generateWithAssistantStream(
            finalMessage,
            settings,
            images,
            (event: AssistantStreamEvent) => {
              const step = eventToThinkingStep(event)
              if (step) {
                pushThinkingStep(thinkingSteps, step)
              }
              set((state) => ({
                assistantMessages: replacePendingAssistantMessage(
                  state.assistantMessages,
                  initialContent,
                  { thinkingSteps: [...thinkingSteps] },
                ),
              }))
            },
            controller.signal,
            history,
            debugContinueFrom,
          )
          await finishModifyStream(
            result,
            epoch,
            initialContent,
            thinkingSteps,
            debugContinueFrom?.original_instruction || finalMessage,
          )
        } else {
          // explain
          const history = buildAssistantHistory(get().assistantMessages)
          set((state) => ({
            assistantBusy: true,
            assistantMessages: [
              ...state.assistantMessages,
              { role: 'user', content: finalMessage },
              { role: 'assistant', content: pendingAssistantMessage('explain') },
            ],
          }))
          const epoch = get().projectEpoch
          const result = await api.askAssistant(finalMessage, history, controller.signal)
          if (projectSwitchedSince(epoch)) {
            discardStaleResult('Assistant reply discarded: project switched during the request.')
            return
          }
          const reply =
            result.ok && result.assistant
              ? result.assistant.reply
              : formatAssistantRequestError(result.error, 'Assistant request failed.')
          set((state) => ({
            assistantBusy: false,
            assistantMessages: replacePendingAssistantMessage(
              state.assistantMessages,
              reply,
              result.ok ? {} : { errorCategory: classifyAssistantError(reply) },
            ),
            lastError: result.ok ? null : reply,
          }))
          await persistAssistantHistory()
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') {
          set((state) => ({
            assistantBusy: false,
            chatAbortController: null,
            interruptedContext: { message: finalMessage, intent },
            assistantMessages: state.assistantMessages.map((m, i) =>
              i === state.assistantMessages.length - 1 &&
              m.role === 'assistant' &&
              m.content.startsWith(ASSISTANT_PENDING_PREFIX)
                ? { ...m, content: INTERRUPTED_CONTENT, interrupted: true, thinkingSteps: m.thinkingSteps }
                : m,
            ),
          }))
          return
        }
        throw e
      } finally {
        set({ chatAbortController: null })
      }
    },

    async actOnAdvisorProposal(id: string, action: 'select' | 'execute', approveCreate?: () => Promise<boolean>) {
      if (!guardSourceBusy() || get().assistantBusy) return
      await sendUnified(action === 'execute' ? '执行该方案' : '选择该方案', [], 'auto', approveCreate, { id, action })
    },

    stopChat() {
      get().chatAbortController?.abort()
    },

    async confirmPendingPlan(approve: boolean) {
      if (!guardSourceBusy()) return
      // 计划确认门（V3）：approve=true → 带已确认计划执行（SSE 接回进度流）；false → 取消
      const plan = get().pendingPlan
      if (!plan) {
        set({ lastError: '没有待确认的修改计划，请先发起一次修改。' })
        return
      }
      if (plan.restored_display_only) {
        if (approve) {
          set({ lastError: '这份历史计划没有可用的执行许可；请重新规划后再审批。' })
          return
        }
        set((state) => ({
          pendingPlan: null,
          assistantMessages: state.assistantMessages.map((message) => message.pendingPlan?.plan_id === plan.plan_id
            ? { ...message, pendingPlan: null, content: '⏹ 已关闭历史计划。重新规划后才能执行。' }
            : message),
        }))
        await persistAssistantHistory()
        return
      }
      if (plan.turn_id) {
        const epoch = get().projectEpoch
        const controller = new AbortController()
        set({ assistantBusy: true, chatAbortController: controller })
        try {
          if (!approve) {
            const result = await api.conversationTurn({ phase: 'execute', turn_id: plan.turn_id, approve: false })
            set({ pendingPlan: null })
            await finishUnified(result, epoch, [], '')
          } else {
            const execution = await executeUnified(plan.turn_id, epoch, '', controller.signal, plan)
            if (execution) {
              if (execution.result.code !== 'PLAN_STALE') set({ pendingPlan: null })
              await finishUnified(execution.result, epoch, execution.steps, '', Boolean(plan.original_has_images))
            }
          }
        } catch (error) {
          set({ lastError: String(error) })
        } finally {
          if (get().chatAbortController === controller) set({ assistantBusy: false, chatAbortController: null })
        }
        return
      }
      if (!approve) {
        const result = await api.confirmModifyPlan(false)
        set((state) => ({
          pendingPlan: null,
          pendingDeliveryContinue: null,
          assistantMessages: replacePendingAssistantMessage(state.assistantMessages, '⏹ 已取消本次修改。'),
        }))
        if (!result.ok && result.error) {
          set({ lastError: result.error })
        }
        await persistAssistantHistory()
        return
      }
      const lastMessage = get().assistantMessages[get().assistantMessages.length - 1]
      const thinkingSteps: AssistantThinkingStep[] = [...(lastMessage?.thinkingSteps ?? [])]
      // HF4：确认执行也携带此前对话。当前轮 user 消息已随 requestModifyPlan 的
      // message / pending body 流转，不重复放进 history。
      const pendingHistory = buildAssistantHistory(get().assistantMessages)
      const history = pendingHistory.at(-1)?.role === 'user' ? pendingHistory.slice(0, -1) : pendingHistory
      set((state) => ({ pendingPlan: null, assistantBusy: true }))
      const epoch = get().projectEpoch
      const result = await api.confirmModifyPlan(true, true, (event: AssistantStreamEvent) => {
        const step = eventToThinkingStep(event)
        if (step) {
          thinkingSteps.push(step)
        }
        set((state) => ({
          assistantMessages: replacePendingAssistantMessage(
            state.assistantMessages,
            PLAN_EXECUTING_CONTENT,
            { thinkingSteps: [...thinkingSteps] },
          ),
        }))
      }, undefined, history)
      await finishModifyStream(result, epoch, '⏳ 正在按已确认的计划执行修改…', thinkingSteps)
    },

    async revisePendingPlan(instruction: string) {
      if (!guardSourceBusy()) return
      const plan = get().pendingPlan
      if (!plan?.turn_id || !plan.plan_id || !Number.isInteger(plan.plan_version)) {
        set({ lastError: '当前计划不能修改，请重新生成计划。' })
        return
      }
      const revisionInstruction = instruction.trim()
      if (!revisionInstruction) return
      if (plan.restored_display_only) {
        if (plan.original_has_images) {
          set({ lastError: '这份计划使用过参考图；请在输入区重新附上图片，再发起审批计划。' })
          return
        }
        const originalRequest = plan.original_request?.trim()
        if (!originalRequest) {
          set({ lastError: '历史计划没有可恢复的原始要求，请在输入区重新描述任务。' })
          return
        }
        set((state) => ({
          pendingPlan: null,
          assistantMessages: state.assistantMessages.map((message) => message.pendingPlan?.plan_id === plan.plan_id
            ? { ...message, pendingPlan: null, content: '📝 正在按修订要求重新规划…' }
            : message),
        }))
        await persistAssistantHistory()
        await get().sendChat(`${originalRequest}\n用户要求修改计划：${revisionInstruction}`, [], 'auto', undefined, true)
        return
      }
      const epoch = get().projectEpoch
      const controller = new AbortController()
      set({ assistantBusy: true, chatAbortController: controller })
      try {
        const result = await api.conversationTurn({
          phase: 'revise',
          turn_id: plan.turn_id,
          plan_id: plan.plan_id,
          plan_version: plan.plan_version,
          revision_instruction: revisionInstruction,
        }, undefined, controller.signal)
        if (result.pending_plan) set({ pendingPlan: { ...result.pending_plan, turn_id: result.turn_id } })
        else if (result.result_kind !== 'awaiting_confirmation') set({ pendingPlan: null })
        await finishUnified(result, epoch, [], plan.original_request ?? revisionInstruction, plan.original_has_images)
      } catch (error) {
        if (!projectSwitchedSince(epoch)) set({ lastError: String(error) })
      } finally {
        if (get().chatAbortController === controller) set({ assistantBusy: false, chatAbortController: null })
      }
    },

    /** P5d-2 提取确认门：approve=true 用编辑后的 extractions 重发创建（跳过 harness）；false 取消清态。 */
    async confirmPendingExtraction(extractions: VisionExtraction[], approve: boolean) {
      if (!guardSourceBusy()) return
      const pending = get().pendingExtraction
      if (!pending) {
        set({ lastError: '没有待确认的读图结果，请先发起一次带图的创建。' })
        return
      }
      if (pending.turn_id) {
        const epoch = get().projectEpoch
        const controller = new AbortController()
        set({ assistantBusy: true, chatAbortController: controller })
        try {
          const result = await api.conversationTurn({ phase: 'execute', turn_id: pending.turn_id,
            approve, approve_extraction: approve, confirmed_extractions: approve ? extractions : undefined, stream: true,
          }, undefined, controller.signal)
          if (result.ok) set({ pendingExtraction: null })
          await finishUnified(result, epoch, [], pending.message)
        } catch (error) {
          set({ lastError: String(error) })
        } finally {
          if (get().chatAbortController === controller) set({ assistantBusy: false, chatAbortController: null })
        }
        return
      }
      if (!approve) {
        set((state) => ({
          pendingExtraction: null,
          assistantMessages: replacePendingAssistantMessage(
            state.assistantMessages,
            EXTRACTION_CANCELLED_CONTENT,
          ),
        }))
        await persistAssistantHistory()
        return
      }
      set((state) => ({
        pendingExtraction: null,
        assistantBusy: true,
        assistantMessages: replacePendingAssistantMessage(
          state.assistantMessages,
          EXTRACTION_EXECUTING_CONTENT,
        ),
      }))
      await _createProject(pending.message, pending.images, undefined, extractions)
    },

    async confirmPendingSkillProposal(approve: boolean) {
      // 模式级 skill 提案（P2-d）/ 显式候选（ST04）：approve → propose+verify；
      // false → 丢弃。失败必须保留卡片与重试入口，且绝不显示成功文案。
      const proposal = get().pendingSkillProposal
      if (!proposal) {
        set({ lastError: '没有待确认的 skill 提案。' })
        return
      }
      const epoch = get().projectEpoch
      const effectiveApprove = proposal.status === 'rejecting' ? false : approve
      const result = await api.confirmSkillProposal(effectiveApprove, proposal.proposal_id)
      if (projectSwitchedSince(epoch)) {
        discardStaleResult('Skill proposal result discarded: project switched during the request.')
        return
      }
      if (!result.ok) {
        const actionLabel = effectiveApprove ? '沉淀' : '拒绝'
        const retryable = result.retryable === true
          || result.code === undefined
          || result.code.endsWith('SAVE_FAILED')
        set((state) => ({
          // 保留卡片：用户可以直接重试（后端返回 retryable 的路径）
          pendingSkillProposal: state.pendingSkillProposal,
          assistantMessages: replacePendingAssistantMessage(
            state.assistantMessages,
            `❌ skill「${proposal.name}」${actionLabel}失败：${result.error ?? '未知错误'}${
              retryable ? '（可重试）' : ''
            }`,
          ),
          lastError: result.error ?? null,
        }))
        await persistAssistantHistory()
        return
      }
      set((state) => ({
        pendingSkillProposal: null,
        assistantMessages: replacePendingAssistantMessage(
          state.assistantMessages,
          effectiveApprove
            ? result.verified
              ? `✅ skill「${proposal.name}」已沉淀并通过验证（${result.gate} 门禁）`
              : `📝 skill「${proposal.name}」已落盘为未激活产物（验证未过/含未核验断言），暂不可用`
            : `🗑 已丢弃 skill 提案「${proposal.name}」。`,
        ),
        lastError: null,
      }))
      await persistAssistantHistory()
    },
  }
}

function pendingAssistantMessage(action: 'explain' | 'create' | 'generate', images?: AssistantImageAttachment[] | null) {
  const labels = (images ?? []).map((img) => attachmentLabel(img)).join(', ')
  const imageStep = images && images.length ? `Reading the attached reference image: ${labels}.` : null
  const steps =
    action === 'generate'
      ? [
          'Inspecting the loaded HSF project.',
          imageStep ?? 'Preparing generation context.',
          'Calling the configured LLM.',
          'Applying returned GDL changes and refreshing preview.',
        ]
      : action === 'create'
        ? [
            imageStep ?? 'Preparing a new HSF project plan.',
            'Calling the configured LLM.',
            'Writing generated HSF source.',
            'Building the initial preview.',
          ]
        : ['Reading the current HSF project.', 'Preparing a concise explanation.']
  return `${ASSISTANT_PENDING_PREFIX}\n${steps.map((step) => `- ${step}`).join('\n')}`
}

function replacePendingAssistantMessage(messages: AssistantMessage[], reply: string, extras: Partial<AssistantMessage> = {}) {
  const replyMessage = { role: 'assistant' as const, content: reply, ...extras }
  const last = messages.at(-1)
  if (last?.role === 'assistant' && last.content.startsWith(ASSISTANT_PENDING_PREFIX)) {
    return [...messages.slice(0, -1), replyMessage]
  }
  return [...messages, replyMessage]
}

/**
 * P5d-1：从事件流提取读图结果（只读卡片数据源）。
 *
 * pipeline 的 vision_analysis_done 事件 payload 携带 `extraction` 提取摘要
 * （与 TaskResult.metadata["vision_extractions"] 条目同构，snake_case 原样透传）；
 * 这里聚合成按图顺序的数组，无提取事件时返回 undefined（不污染消息）。
 * token 缺省时用事件里的 image_index 拼「图N」；skipped 条目保留给卡片过滤。
 */
function extractVisionExtractions(
  events?: Array<{ type: string; data: unknown }>,
): VisionExtraction[] | undefined {
  const out: VisionExtraction[] = []
  for (const event of events ?? []) {
    if (event.type !== 'vision_analysis_done') continue
    const data = event.data
    if (!data || typeof data !== 'object') continue
    const rawExtraction = (data as { extraction?: unknown }).extraction
    if (!rawExtraction || typeof rawExtraction !== 'object') continue
    const ext = rawExtraction as VisionExtraction
    const index = (data as { image_index?: unknown }).image_index
    out.push({
      ...ext,
      token:
        ext.token ??
        (typeof index === 'number' ? `图${index}` : undefined),
    })
  }
  return out.length ? out : undefined
}

function formatAssistantEventSummary(events?: Array<{ type: string; data: unknown }>) {
  const messages = (events ?? [])
    .map((event) => {
      const data = event.data
      if (data && typeof data === 'object' && 'message' in data && typeof data.message === 'string') {
        return data.message
      }
      if (event.type === 'compile_result') {
        return 'Compile verification finished.'
      }
      if (event.type === 'vision_analysis_done') {
        return 'Reference image analysis finished.'
      }
      if (event.type === 'object_plan_done') {
        return 'GDL object plan finished.'
      }
      return ''
    })
    .filter(Boolean)
    .filter((message, index, all) => all.indexOf(message) === index)
    .slice(0, 5)

  if (!messages.length) {
    return ''
  }
  return `\n\nProcess:\n${messages.map((message) => `- ${message}`).join('\n')}`
}
