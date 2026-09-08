import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { hydrateSnapshot } from '../workbenchStoreUtils'
import type { WorkbenchSnapshot } from '../../api/types'
import { beginSourceAction, captureProjectIdentity, endSourceAction, sameProjectIdentity } from './sourceActionHelpers'

export function createRevisionActions({ api, get, set }: WorkbenchActionContext) {
  return {
    async loadRevisions() {
      // 未打开项目时不发请求：后端对无项目的 revisions 返回 404，
      // 在 Console 刷无意义红错（修复 3）
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

    // SF1/R1：创建版本前先保存全部脚本草稿（失败即中止，版本 API 零调用）；
    // 不自动 Apply 参数草稿——有草稿时提示未纳入版本。
    // R1-02：版本 API 返回后再查身份；R1-03：异常分支复位 revisionLoading/sourceActionBusy。
    async saveRevision(message = '') {
      const guard = beginSourceAction(get, set, 'save-revision')
      if (!guard.ok) {
        set({ lastError: guard.reason ?? 'Save Revision is blocked.' })
        return false
      }
      const identity = captureProjectIdentity(get())
      try {
        set({ revisionLoading: true, lastError: null })
        const flushed = await get().flushDirtyScripts()
        if (!flushed.ok) {
          set({
            revisionLoading: false,
            lastError: get().lastError ?? flushed.error ?? 'Failed to save scripts.',
          })
          return false
        }
        if (!sameProjectIdentity(get(), identity)) {
          set({ revisionLoading: false })
          return false
        }
        const result = await api.saveProjectRevision(message)
        if (!result.ok) {
          set({
            revisionLoading: false,
            lastError: result.error ?? 'Failed to save revision.',
          })
          return false
        }
        if (!sameProjectIdentity(get(), identity)) {
          set({ revisionLoading: false })
          return false
        }
        await get().loadRevisions()
        const hasParameterDrafts = Object.keys(get().draftParameters).length > 0
        set((state) => ({
          revisionLoading: false,
          compileLog: [
            `Saved revision ${result.revision?.revision_id ?? ''}`.trim(),
            // SF1：明确提示未 Apply 的参数草稿不在此版本内
            ...(hasParameterDrafts ? ['Revision contains saved scripts; unapplied parameter drafts are not included.'] : []),
            ...state.compileLog,
          ].slice(0, 20),
        }))
        return true
      } catch (exc) {
        set({
          revisionLoading: false,
          lastError: exc instanceof Error ? exc.message : String(exc ?? 'Failed to save revision.'),
        })
        return false
      } finally {
        endSourceAction(set)
      }
    },

    async restoreRevision(revisionId: string) {
      const target = revisionId.trim()
      if (!target) return
      set({ revisionLoading: true, lastError: null })
      const result = await api.restoreProjectRevision(target)
      if (!result.ok || !result.project || !result.parameters || !result.preview) {
        set({
          revisionLoading: false,
          lastError: result.error ?? `Failed to restore revision: ${target}`,
        })
        return
      }
      set(hydrateSnapshot(result as WorkbenchSnapshot, get().compilerSettings, get().llmSettings))
      await get().loadScripts()
      await get().loadRevisions()
      set((state) => ({
        revisionLoading: false,
        compileLog: [`Restored revision ${target}`, ...state.compileLog].slice(0, 20),
      }))
    },
  }
}
