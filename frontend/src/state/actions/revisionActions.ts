import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { hydrateSnapshot } from '../workbenchStoreUtils'
import type { DeliveryPresentation, RestoreDraftPolicy, WorkbenchSnapshot } from '../../api/types'

/** ST03：恢复 before 的草稿策略。取消必须在调用方完成（本 action 不发起确认框）。 */
export interface RestoreRevisionOptions {
  /** discard = 丢弃脚本/参数草稿；keep = 恢复磁盘源后把编辑器草稿重新叠回 */
  draftPolicy: RestoreDraftPolicy
  /** 可选：恢复来源标注（delivery recover / revision panel） */
  source?: 'delivery' | 'revision_panel' | string
  /** delivery recover 时的原 run_id，仅用于 compileLog 可追溯 */
  originRunId?: string | null
}

interface DraftSnapshot {
  scriptContents: Record<string, string>
  dirtyScripts: Record<string, boolean>
  draftParameters: Record<string, unknown>
}

function readDraftState(state: {
  scriptContents?: Record<string, string>
  dirtyScripts?: Record<string, boolean>
  draftParameters?: Record<string, unknown>
}): DraftSnapshot {
  return {
    scriptContents: { ...(state.scriptContents ?? {}) },
    dirtyScripts: Object.fromEntries(
      Object.entries(state.dirtyScripts ?? {}).filter(([, dirty]) => dirty),
    ),
    draftParameters: { ...(state.draftParameters ?? {}) },
  }
}

function hasUnsavedDrafts(state: {
  dirtyScripts?: Record<string, boolean>
  draftParameters?: Record<string, unknown>
}): boolean {
  return (
    Object.values(state.dirtyScripts ?? {}).some(Boolean) ||
    Object.keys(state.draftParameters ?? {}).length > 0
  )
}

export function createRevisionActions({ api, get, set }: WorkbenchActionContext) {
  async function restoreRevision(revisionId: string, options?: RestoreRevisionOptions) {
    const target = revisionId.trim()
    if (!target) return

    const draftsPresent = hasUnsavedDrafts(get())
    if (draftsPresent && !options?.draftPolicy) {
      set({
        lastError:
          'Restore blocked: unsaved script/parameter drafts exist. Confirm discard or keep drafts first.',
      })
      return
    }

    const draftPolicy: RestoreDraftPolicy | null = options?.draftPolicy ?? null
    const kept: DraftSnapshot | null = draftPolicy === 'keep' ? readDraftState(get()) : null

    set({ revisionLoading: true, lastError: null })
    const result = await api.restoreProjectRevision(target, draftPolicy)
    if (!result.ok || !result.project || !result.parameters || !result.preview) {
      set({
        revisionLoading: false,
        lastError: result.error ?? `Failed to restore revision: ${target}`,
      })
      return
    }

    set(hydrateSnapshot(result as WorkbenchSnapshot, get().compilerSettings, get().llmSettings))

    if (kept) {
      const dirtyScripts: Record<string, boolean> = {}
      const scriptContents: Record<string, string> = {}
      for (const [name, dirty] of Object.entries(kept.dirtyScripts)) {
        if (!dirty) continue
        if (kept.scriptContents[name] === undefined) continue
        scriptContents[name] = kept.scriptContents[name]
        dirtyScripts[name] = true
      }
      set({
        scriptContents,
        dirtyScripts,
        draftParameters: kept.draftParameters,
      })
    }

    await get().loadScripts()
    await get().loadRevisions()

    const originNote = options?.originRunId ? ` (run ${options.originRunId})` : ''
    const draftNote = kept
      ? ' · drafts kept in editor (not applied to restored source)'
      : draftsPresent
        ? ' · drafts discarded'
        : ''
    set((state) => ({
      revisionLoading: false,
      pendingDeliveryContinue: null,
      compileLog: [`Restored revision ${target}${originNote}${draftNote}`, ...state.compileLog].slice(0, 20),
    }))
  }

  return {
    async loadRevisions() {
      if (!get().project) {
        set({ revisionLoading: false, revisions: [], latestRevisionId: null })
        return
      }
      set({ revisionLoading: true })
      const result = await api.listProjectRevisions()
      if (!result.ok) {
        set({
          revisionLoading: false,
          revisions: [],
          latestRevisionId: null,
        })
        return
      }
      set({
        revisionLoading: false,
        revisions: result.revisions ?? [],
        latestRevisionId: result.latest_revision_id ?? null,
      })
    },

    async saveRevision(message = '') {
      set({ revisionLoading: true, lastError: null })
      const result = await api.saveProjectRevision(message)
      if (!result.ok) {
        set({
          revisionLoading: false,
          lastError: result.error ?? 'Failed to save revision.',
        })
        return
      }
      await get().loadRevisions()
      set((state) => ({
        compileLog: [`Saved revision ${result.revision?.revision_id ?? ''}`.trim(), ...state.compileLog].slice(0, 20),
      }))
    },

    restoreRevision,

    async viewRevisionDiff(fromRevisionId: string, toRevisionId: string): Promise<string | null> {
      const fromId = fromRevisionId.trim()
      const toId = toRevisionId.trim()
      if (!fromId || !toId) return null
      const result = await api.getProjectRevisionDiff(fromId, toId)
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to load revision diff.' })
        return null
      }
      return result.diff ?? ''
    },

    async recoverDeliveryBefore(
      presentation: DeliveryPresentation | null | undefined,
      options?: { draftPolicy?: RestoreDraftPolicy; source?: string },
    ): Promise<boolean> {
      const recoverId = presentation?.recover_revision_id
      if (!recoverId || !presentation?.can_recover) {
        set({ lastError: 'No recoverable before-revision for this delivery record.' })
        return false
      }
      const draftPolicy = options?.draftPolicy ?? 'discard'
      await restoreRevision(recoverId, {
        draftPolicy,
        source: options?.source ?? 'delivery',
        originRunId: presentation.run_id,
      })
      return !get().lastError
    },

    async continueDelivery(payload: {
      originRunId: string | null
      originalInstruction: string
      intent?: string
    }): Promise<void> {
      const instruction = (payload.originalInstruction || '').trim()
      if (!instruction) {
        set({ lastError: 'Cannot continue: original instruction is missing on this delivery record.' })
        return
      }
      if (!get().project) {
        set({ lastError: 'Open a project before continuing a delivery run.' })
        return
      }
      if (!payload.originRunId) {
        set({ lastError: 'Cannot continue: original run id is missing (old unlinked record).' })
        return
      }
      set({
        pendingDeliveryContinue: {
          origin_run_id: payload.originRunId,
          original_instruction: instruction,
          intent: payload.intent,
        },
      })
      await get().sendChat(instruction)
    },

    clearPendingDeliveryContinue() {
      set({ pendingDeliveryContinue: null })
    },
  }
}
