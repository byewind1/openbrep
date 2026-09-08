import type { WorkbenchGet, WorkbenchSet, WorkbenchState } from '../workbenchStoreTypes'

// SF1：短时源操作（Save / Save As / 参数写入 / Save Revision）的共用守卫与辅助。
// 只处理 source 保存语义，不混入 AI/知识/宿主功能。

export interface SourceActionGuard {
  ok: boolean
  reason?: string
}

/** 进入顶层源操作：冲突（重入 / AI 执行中 / 编译中 / 项目切换中）直接拒绝。
 *  成功时置 sourceActionBusy=true，调用方必须在 finally 里 endSourceAction。
 *  flushDirtyScripts 是内部工具，不经过这里（避免 Save 调 flush 时拒绝自己）。 */
export function beginSourceAction(get: WorkbenchGet, set: WorkbenchSet, _action: string): SourceActionGuard {
  const state = get()
  if (state.sourceActionBusy) {
    return { ok: false, reason: 'A source operation is already in progress.' }
  }
  if (state.assistantBusy) {
    return { ok: false, reason: 'An AI task is running. Wait for it to finish.' }
  }
  if (state.compiling) {
    return { ok: false, reason: 'A compile is running. Wait for it to finish.' }
  }
  if (state.loading) {
    return { ok: false, reason: 'The project is loading. Wait for it to finish.' }
  }
  set({ sourceActionBusy: true, lastError: null })
  return { ok: true }
}

export function endSourceAction(set: WorkbenchSet) {
  set({ sourceActionBusy: false })
}

/** 记录操作开始时的项目身份；异步返回后用它丢弃跨项目结果。 */
export function captureProjectIdentity(state: WorkbenchState) {
  return { sessionId: state.sessionId, projectEpoch: state.projectEpoch }
}

export function sameProjectIdentity(
  state: WorkbenchState,
  identity: { sessionId: string | null; projectEpoch: number },
) {
  return state.sessionId === identity.sessionId && state.projectEpoch === identity.projectEpoch
}

/** 收集本次 Save As 请求的脚本覆盖：只取当前 dirty 且内容存在的脚本。
 *  空字符串是有效覆盖，不能被 truthy 判断丢掉。
 *  返回 {overrides, error?}：dirty=true 但 content 缺失时返回明确错误。 */
export function collectScriptOverrides(state: WorkbenchState): { overrides: Record<string, string>; error?: string } {
  const overrides: Record<string, string> = {}
  const missing: string[] = []
  for (const [name, dirty] of Object.entries(state.dirtyScripts)) {
    if (!dirty) continue
    const content = state.scriptContents[name]
    if (typeof content !== 'string') {
      missing.push(name)
      continue
    }
    overrides[name] = content
  }
  if (missing.length > 0) {
    return {
      overrides,
      error: `Cannot export drafts: missing editor buffer for ${missing.join(', ')}.`,
    }
  }
  return { overrides }
}

/** 任一草稿存在（脚本草稿或参数草稿）——离开项目确认共用的判定。 */
export function hasAnyDraft(state: Pick<WorkbenchState, 'dirtyScripts' | 'draftParameters'>): boolean {
  if (Object.values(state.dirtyScripts).some(Boolean)) return true
  return Object.keys(state.draftParameters).length > 0
}

/** Save As 成功后保留仍合法的参数草稿：新项目参数定义里没有的字段不静默吞掉，
 *  返回被丢弃的字段名供调用方提示。 */
export function keepValidDraftParameters(
  draftParameters: Record<string, unknown>,
  parameterNames: Array<string>,
): { kept: Record<string, unknown>; dropped: string[] } {
  const valid = new Set(parameterNames)
  const kept: Record<string, unknown> = {}
  const dropped: string[] = []
  for (const [name, value] of Object.entries(draftParameters)) {
    if (valid.has(name)) kept[name] = value
    else dropped.push(name)
  }
  return { kept, dropped }
}

/** 根据保留/丢弃的参数草稿生成提示，未保留任何字段时不谎称已保留。 */
export function formatDraftKeptNotice(kept: Record<string, unknown>, dropped: string[]): string[] {
  const notices: string[] = []
  if (Object.keys(kept).length > 0) {
    notices.push('Parameter drafts kept (not applied).')
  }
  if (dropped.length > 0) {
    notices.push(`Parameter drafts dropped (no longer valid): ${dropped.join(', ')}.`)
  }
  return notices
}
