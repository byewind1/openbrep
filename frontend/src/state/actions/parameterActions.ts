import type { AddParameterRequest, UpdateParameterRequest, WorkbenchSnapshot } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createParameterActions({ api, get, set }: WorkbenchActionContext) {
  // 快速连续改参数时请求会乱序返回：只有最后一次请求的响应允许写入预览，
  // 旧响应直接丢弃，避免旧帧覆盖新帧。
  let draftPreviewSeq = 0

  async function refreshParameterSource() {
    await get().refreshProjectWorkspace({
      preferredScriptName: 'paramlist.xml',
      refreshAllScripts: true,
      refreshPreview: false,
      runDiagnostics: true,
    })
  }

  function applyParameterSnapshot(result: WorkbenchSnapshot) {
    set({
      project: result.project,
      parameters: result.parameters,
      parameterIssues: [],
      preview: result.preview,
      warnings: result.warnings,
      draftParameters: {},
      applying: false,
    })
  }

  return {
    async setDraftParameter(name: string, value: unknown) {
      const draftParameters = { ...get().draftParameters, [name]: value }
      set({ draftParameters })
      const requestId = ++draftPreviewSeq
      const preview = await api.fetchPreview(draftParameters)
      if (requestId !== draftPreviewSeq) return
      set({ preview, warnings: preview.warnings ?? [] })
      if (get().activeRailPanel === '2d') {
        await get().loadPreview2D()
      }
    },

    async addProjectParameter(parameter: AddParameterRequest) {
      set({ applying: true, lastError: null })
      const result = await api.addProjectParameter(parameter)
      if (!result.ok) {
        set({ applying: false, lastError: result.error ?? 'Failed to add parameter.' })
        return false
      }
      applyParameterSnapshot(result)
      await refreshParameterSource()
      return true
    },

    async updateProjectParameter(parameter: UpdateParameterRequest) {
      set({ applying: true, lastError: null })
      const result = await api.updateProjectParameter(parameter)
      if (!result.ok) {
        set({ applying: false, lastError: result.error ?? 'Failed to update parameter.' })
        return false
      }
      applyParameterSnapshot(result)
      await refreshParameterSource()
      return true
    },

    async deleteProjectParameter(name: string) {
      set({ applying: true, lastError: null })
      const result = await api.deleteProjectParameter(name)
      if (!result.ok) {
        set({ applying: false, lastError: result.error ?? 'Failed to delete parameter.' })
        return false
      }
      applyParameterSnapshot(result)
      await refreshParameterSource()
      return true
    },

    async validateProjectParameters() {
      const result = await api.validateProjectParameters()
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to validate parameters.' })
        return
      }
      set({ parameterIssues: result.issues })
    },

    async applyDraftParameters() {
      const draft = get().draftParameters
      if (Object.keys(draft).length === 0) return
      set({ applying: true, lastError: null })
      const result = await api.applyParameters(draft)
      if (!result.ok) {
        set({
          applying: false,
          lastError: result.error ?? 'Failed to apply parameters.',
        })
        return
      }
      set({
        project: result.project,
        parameters: result.parameters,
        parameterIssues: [],
        preview: result.preview,
        warnings: result.warnings,
        draftParameters: {},
        applying: false,
      })
      await get().refreshProjectWorkspace({
        refreshAllScripts: true,
        refreshPreview: false,
        runDiagnostics: true,
      })
    },

    resetDraftParameters() {
      set({ draftParameters: {} })
    },

    hasDraftChanges() {
      return Object.keys(get().draftParameters).length > 0
    },
  }
}
