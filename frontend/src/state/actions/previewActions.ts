import type { PreviewQuality } from '../../api/types'
import type { PreviewSourceMode, WorkbenchActionContext } from '../workbenchStoreTypes'

export function createPreviewActions({ api, get, set }: WorkbenchActionContext) {
  let authoritativeRequestSerial = 0
  let hostVerificationRequestSerial = 0

  function dirtyScriptBuffers() {
    return Object.fromEntries(
      Object.entries(get().dirtyScripts)
        .filter(([, dirty]) => dirty)
        .map(([name]) => [name, get().scriptContents[name]])
        .filter(([, content]) => typeof content === 'string'),
    ) as Record<string, string>
  }

  return {
    async loadPreview3D() {
      const preview = await api.fetchPreview(get().draftParameters, dirtyScriptBuffers(), get().previewQuality)
      set({ preview, warnings: preview.warnings ?? [] })
    },

    async loadPreview2D() {
      const preview2d = await api.fetchPreview2D(get().draftParameters, dirtyScriptBuffers(), get().previewQuality)
      set({ preview2d, warnings: preview2d.warnings ?? [] })
    },

    async setPreviewQuality(quality: PreviewQuality) {
      set({ previewQuality: quality })
      await get().loadPreview3D()
      // 2D 同一参数链：当前 2D tab 活跃时一并按新档刷新
      if (get().activeRailPanel === '2d') {
        await get().loadPreview2D()
      }
    },

    async setPreviewSourceMode(mode: PreviewSourceMode) {
      if (mode === get().previewSourceMode) return
      set({ previewSourceMode: mode })
      // 首次切到权威模式立即取一次；之后只由「刷新权威」显式触发，
      // 不跟随参数改动自动重取（每次调用 Archicad 成本高）
      if (mode === 'authoritative' && !get().previewAuthoritative && !get().previewAuthoritativeLoading) {
        await get().loadAuthoritativePreview()
      }
    },

    async loadAuthoritativePreview() {
      const draft = get().draftParameters
      const requestedProjectEpoch = get().projectEpoch
      const requestSerial = ++authoritativeRequestSerial
      set({ previewAuthoritativeLoading: true, previewAuthoritativeError: null })
      const result = await api.fetchAuthoritativePreview(draft)
      // Archicad 求值可能较慢。切项目或后发刷新完成后，旧响应不得回写。
      if (requestSerial !== authoritativeRequestSerial || get().projectEpoch !== requestedProjectEpoch) return
      if (!result.ok || !result.preview) {
        // 失败不清空已有数据、不动本地 preview：视口回退显示本地预览，错误原文上屏
        set({
          previewAuthoritativeLoading: false,
          previewAuthoritativeError: result.error ?? 'Authoritative preview failed.',
        })
        return
      }
      set({
        previewAuthoritativeLoading: false,
        previewAuthoritativeError: null,
        previewAuthoritative: result.preview,
        previewAuthoritative2d: result.preview.preview2d ?? null,
        previewAuthoritativeParamsKey: JSON.stringify(draft),
      })
    },

    async loadHostVerification() {
      const requestedProjectEpoch = get().projectEpoch
      const requestSerial = ++hostVerificationRequestSerial
      const paramsKey = JSON.stringify(get().draftParameters)
      const result = await api.fetchHostVerification()
      if (requestSerial !== hostVerificationRequestSerial || get().projectEpoch !== requestedProjectEpoch) return
      if (result.status === 'not_checked' || !result.record_id) {
        set({ hostVerification: null, hostVerificationError: null, hostVerificationParamsKey: paramsKey })
        return
      }
      set({
        hostVerification: {
          ...(result as unknown as import('../../api/types').HostVerificationRecord),
          stale: result.stale,
          stale_reasons: result.stale_reasons,
        },
        hostVerificationError: null,
        hostVerificationParamsKey: paramsKey,
      })
    },

    async runHostVerification() {
      if (Object.values(get().dirtyScripts).some(Boolean)) {
        set({ hostVerificationError: '请先保存或取消脚本修改，再运行 AC 验收。' })
        return
      }
      const requestedProjectEpoch = get().projectEpoch
      const requestedSourceFingerprint = get().sourceFingerprint ?? ''
      const parameters = { ...get().draftParameters }
      const paramsKey = JSON.stringify(parameters)
      const requestSerial = ++hostVerificationRequestSerial
      set({ hostVerificationLoading: true, hostVerificationError: null })
      const result = await api.runHostVerification({
        parameters,
        expected_project_epoch: requestedProjectEpoch,
        expected_source_fingerprint: requestedSourceFingerprint,
      })
      if (requestSerial !== hostVerificationRequestSerial) return
      const inputsChanged = (
        get().projectEpoch !== requestedProjectEpoch
        || get().sourceFingerprint !== requestedSourceFingerprint
        || JSON.stringify(get().draftParameters) !== paramsKey
      )
      if (inputsChanged || result.current === false || result.stale === true) {
        set({ hostVerificationLoading: false })
        return
      }
      if (!result.ok || !result.verification) {
        set({
          hostVerificationLoading: false,
          hostVerificationError: result.error ?? 'AC 验收失败。',
        })
        return
      }
      set({
        hostVerification: result.verification,
        hostVerificationLoading: false,
        hostVerificationError: null,
        hostVerificationParamsKey: paramsKey,
      })
    },
  }
}
