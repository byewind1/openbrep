import type { AddParameterRequest, UpdateParameterRequest, WorkbenchSnapshot } from '../../api/types'
import type { WorkbenchActionContext, WorkbenchState } from '../workbenchStoreTypes'
import { nowTimeText } from '../workbenchStoreUtils'
import {
  beginSourceAction,
  captureProjectIdentity,
  endSourceAction,
  formatDraftKeptNotice,
  keepValidDraftParameters,
  sameProjectIdentity,
} from './sourceActionHelpers'

const DRAFT_PREVIEW_DEBOUNCE_MS = 250

export function createParameterActions({ api, get, set }: WorkbenchActionContext) {
  // 快速连续改参数时请求会乱序返回：只有最后一次请求的响应允许写入预览，
  // 旧响应直接丢弃，避免旧帧覆盖新帧。
  let draftPreviewSeq = 0
  let draftPreviewTimer: ReturnType<typeof setTimeout> | null = null

  function scheduleDraftPreview(draftParameters: Record<string, unknown>) {
    const requestId = ++draftPreviewSeq
    if (draftPreviewTimer !== null) {
      clearTimeout(draftPreviewTimer)
    }
    draftPreviewTimer = setTimeout(() => {
      draftPreviewTimer = null
      void (async () => {
        const preview = await api.fetchPreview(draftParameters, undefined, get().previewQuality)
        if (requestId !== draftPreviewSeq) return
        set({ preview, warnings: preview.warnings ?? [] })
        if (get().activeRailPanel === '2d') {
          await get().loadPreview2D()
        }
      })()
    }, DRAFT_PREVIEW_DEBOUNCE_MS)
  }

  async function refreshParameterSource() {
    await get().refreshProjectWorkspace({
      preferredScriptName: 'paramlist.xml',
      refreshAllScripts: true,
      refreshPreview: false,
      runDiagnostics: true,
    })
  }

  // SF1/R1-04：参数快照应用后不顺手清掉无关参数草稿——保留仍合法的未提交项；
  // appliedDraft（本次提交的草稿）经合法校验，被丢弃的字段返回给调用方提示；
  // removedName（删除参数）单项清除。
  function applyParameterSnapshot(
    result: WorkbenchSnapshot,
    appliedDraft?: Record<string, unknown>,
    removedName?: string,
  ): { kept: Record<string, unknown>; dropped: string[] } {
    const validNames = (result.parameters ?? []).map((parameter) => parameter.name)
    let kept: Record<string, unknown> = {}
    let dropped: string[] = []
    if (appliedDraft) {
      // Apply 提交的草稿：合法项已被后端采纳，应全部清空；只把失效字段返回提示
      const validated = keepValidDraftParameters(appliedDraft, validNames)
      kept = {}
      dropped = validated.dropped
    } else {
      const validated = keepValidDraftParameters(get().draftParameters, validNames)
      kept = validated.kept
      dropped = validated.dropped
      if (removedName && kept[removedName] !== undefined) {
        delete kept[removedName]
      }
    }
    set({
      project: result.project,
      parameters: result.parameters,
      parameterIssues: [],
      preview: result.preview,
      warnings: result.warnings,
      draftParameters: kept,
      applying: false,
      // 参数应用/增删改在后端都会 save_to_disk，算一次保存
      lastSavedAt: nowTimeText(),
    })
    return { kept, dropped }
  }

  // SF1/R1：参数写入（Apply/增/改/删）的统一前置：先保存全部脏脚本，失败即中止
  // （参数 API 零调用）；flush 后重新确认项目身份再提交。成功后只清被提交的
  // 参数草稿，保留无关合法草稿；flush 已成功而参数写入失败时如实说明。
  // R1-02：await write 返回后再查身份，过期响应不覆盖新项目。
  // R1-03：异常分支复位 applying/sourceActionBusy 并显示错误。
  async function runParameterWrite(
    action: string,
    removedName: string | undefined,
    appliedDraft: Record<string, unknown> | undefined,
    write: () => Promise<WorkbenchSnapshot & { ok: boolean; error?: string }>,
  ): Promise<boolean> {
    const guard = beginSourceAction(get, set, action)
    if (!guard.ok) {
      set({ lastError: guard.reason ?? `${action} is blocked.` })
      return false
    }
    const identity = captureProjectIdentity(get())
    try {
      set({ applying: true, lastError: null })
      const flushed = await get().flushDirtyScripts()
      if (!flushed.ok) {
        set({ applying: false, lastError: get().lastError ?? flushed.error ?? 'Failed to save scripts.' })
        return false
      }
      if (!sameProjectIdentity(get(), identity)) {
        set({ applying: false })
        return false
      }
      const result = await write()
      if (!result.ok) {
        set({
          applying: false,
          lastError: flushed.didSave
            ? `Scripts saved, but ${action} failed: ${result.error ?? 'unknown error'}`
            : result.error ?? `Failed to ${action}.`,
        })
        return false
      }
      if (!sameProjectIdentity(get(), identity)) {
        set({ applying: false })
        return false
      }
      const { kept, dropped } = applyParameterSnapshot(result, appliedDraft, removedName)
      await refreshParameterSource()
      if (dropped.length > 0) {
        set((state) => ({
          compileLog: [
            `Parameter drafts dropped after ${action}: ${dropped.join(', ')}.`,
            ...state.compileLog,
          ].slice(0, 20),
        }))
      }
      if (Object.keys(kept).length > 0) {
        set((state) => ({
          compileLog: ['Parameter drafts kept (not applied).', ...state.compileLog].slice(0, 20),
        }))
      }
      return true
    } catch (exc) {
      set({
        applying: false,
        lastError: exc instanceof Error ? exc.message : String(exc ?? `Failed to ${action}.`),
      })
      return false
    } finally {
      endSourceAction(set)
    }
  }

  return {
    async setDraftParameter(name: string, value: unknown) {
      const draftParameters = { ...get().draftParameters, [name]: value }
      set({ draftParameters })
      scheduleDraftPreview(draftParameters)
    },

    async addProjectParameter(parameter: AddParameterRequest) {
      return runParameterWrite('add parameter', undefined, undefined, () => api.addProjectParameter(parameter))
    },

    async updateProjectParameter(parameter: UpdateParameterRequest) {
      return runParameterWrite('update parameter', undefined, undefined, () => api.updateProjectParameter(parameter))
    },

    async deleteProjectParameter(name: string) {
      return runParameterWrite('delete parameter', name, undefined, () => api.deleteProjectParameter(name))
    },

    async validateProjectParameters() {
      const result = await api.validateProjectParameters()
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to validate parameters.' })
        return
      }
      set({ parameterIssues: result.issues })
    },

    // SF1：网络写参前先保存全部脏脚本（失败中止、参数 API 零调用），
    // 成功后才清理已提交的参数草稿并刷新脚本/预览（此时不再有会被覆盖的脏脚本）。
    async applyDraftParameters() {
      const draft = get().draftParameters
      if (Object.keys(draft).length === 0) return true
      return runParameterWrite('apply parameters', undefined, draft, () => api.applyParameters(draft))
    },

    resetDraftParameters() {
      set({ draftParameters: {} })
    },

    hasDraftChanges() {
      return Object.keys(get().draftParameters).length > 0
    },
  }
}
