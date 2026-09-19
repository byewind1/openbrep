import type { AddParameterRequest, UpdateParameterRequest, WorkbenchSnapshot } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'
import { nowTimeText } from '../workbenchStoreUtils'

const DRAFT_PREVIEW_DEBOUNCE_MS = 250

export function createParameterActions({ api, get, set }: WorkbenchActionContext) {
  // 快速连续改参数时请求会乱序返回：只有最后一次请求的响应允许写入预览，
  // 旧响应直接丢弃，避免旧帧覆盖新帧。
  let draftPreviewSeq = 0
  let draftPreviewTimer: ReturnType<typeof setTimeout> | null = null
  let effectiveSeq = 0
  let effectiveTimer: ReturnType<typeof setTimeout> | null = null

  async function refreshEffectiveParameters(parameters = get().draftParameters) {
    const requestId = ++effectiveSeq
    const epoch = get().projectEpoch
    const projectPath = get().project?.path ?? null
    const sourceFingerprint = get().sourceFingerprint
    const parametersKey = JSON.stringify(parameters)
    if (!get().project) {
      set({
        effectiveParameters: {},
        effectiveParameterDiagnostics: [],
        effectiveParametersBusy: false,
        effectiveParametersError: null,
      })
      return
    }
    set({ effectiveParametersBusy: true, effectiveParametersError: null })
    const result = await api.fetchEffectiveParameters(parameters)
    const current = get()
    const stale = requestId !== effectiveSeq
      || current.projectEpoch !== epoch
      || (projectPath !== null && current.project?.path !== projectPath)
      || JSON.stringify(current.draftParameters) !== parametersKey
      || (result.project_epoch !== undefined && result.project_epoch !== epoch)
      || (result.project_path !== undefined && projectPath !== null && result.project_path !== projectPath)
      || (sourceFingerprint !== null
        && result.source_fingerprint !== undefined
        && result.source_fingerprint !== sourceFingerprint)
    if (stale) return
    if (!result.ok) {
      set({
        effectiveParametersBusy: false,
        effectiveParametersError: result.error ?? 'Failed to evaluate effective parameters.',
      })
      return
    }
    set({
      effectiveParameters: Object.fromEntries(
        (result.parameters ?? []).map((parameter) => [parameter.name, parameter]),
      ),
      effectiveParameterDiagnostics: result.diagnostics ?? [],
      effectiveParametersBusy: false,
      effectiveParametersError: null,
    })
  }

  function scheduleEffectiveParameters(draftParameters: Record<string, unknown>) {
    if (effectiveTimer !== null) clearTimeout(effectiveTimer)
    effectiveTimer = setTimeout(() => {
      effectiveTimer = null
      void refreshEffectiveParameters(draftParameters)
    }, DRAFT_PREVIEW_DEBOUNCE_MS)
  }

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

  function applyParameterSnapshot(result: WorkbenchSnapshot) {
    set({
      project: result.project,
      parameters: result.parameters,
      parameterIssues: [],
      preview: result.preview,
      warnings: result.warnings,
      draftParameters: {},
      sourceFingerprint: result.source_fingerprint ?? null,
      effectiveParameters: {},
      effectiveParameterDiagnostics: [],
      applying: false,
      // 参数应用/增删改在后端都会 save_to_disk，算一次保存
      lastSavedAt: nowTimeText(),
    })
  }

  return {
    async setDraftParameter(name: string, value: unknown) {
      const draftParameters = { ...get().draftParameters, [name]: value }
      set({ draftParameters })
      scheduleDraftPreview(draftParameters)
      scheduleEffectiveParameters(draftParameters)
    },

    refreshEffectiveParameters,

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
        sourceFingerprint: result.source_fingerprint ?? null,
        effectiveParameters: {},
        effectiveParameterDiagnostics: [],
        applying: false,
      })
      await refreshEffectiveParameters({})
      await get().refreshProjectWorkspace({
        refreshAllScripts: true,
        refreshPreview: false,
        runDiagnostics: true,
      })
    },

    resetDraftParameters() {
      set({ draftParameters: {} })
      scheduleEffectiveParameters({})
    },

    hasDraftChanges() {
      return Object.keys(get().draftParameters).length > 0
    },
  }
}
