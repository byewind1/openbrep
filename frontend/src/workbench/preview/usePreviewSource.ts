import type { Preview2DPayload, PreviewPayload } from '../../api/types'
import type { PreviewSourceMode } from '../../state/workbenchStoreTypes'
import type { PreviewSourceControl } from '../../components/PreviewViewport'
import { useWorkbenchStore } from '../../state/useWorkbenchStore'

/**
 * 3D 预览来源（本地近似 / Archicad 权威）：三个视口挂载点
 * （右栏 / 中央舞台 / 浮动窗）共用，保证任一视口切换来源、刷新、报错一致。
 *
 * 权威模式取数不跟随参数变化（Archicad 调用成本高），只靠显式「刷新权威」；
 * 取数时的 draftParameters 指纹与当前不一致 → stale 轻提示；
 * 取数失败 → 回退显示本地预览，error 由视口上屏。
 */
export function usePreviewSource(localPreview: PreviewPayload | null): {
  preview: PreviewPayload | null
  sourceControl: PreviewSourceControl
} {
  const mode = useWorkbenchStore((state) => state.previewSourceMode)
  const authoritative = useWorkbenchStore((state) => state.previewAuthoritative)
  const loading = useWorkbenchStore((state) => state.previewAuthoritativeLoading)
  const error = useWorkbenchStore((state) => state.previewAuthoritativeError)
  const paramsKey = useWorkbenchStore((state) => state.previewAuthoritativeParamsKey)
  const draftParameters = useWorkbenchStore((state) => state.draftParameters)
  const setPreviewSourceMode = useWorkbenchStore((state) => state.setPreviewSourceMode)
  const loadAuthoritativePreview = useWorkbenchStore((state) => state.loadAuthoritativePreview)
  const tapirStatus = useWorkbenchStore((state) => state.tapirStatus)
  const hostVerification = useWorkbenchStore((state) => state.hostVerification)
  const hostVerificationLoading = useWorkbenchStore((state) => state.hostVerificationLoading)
  const hostVerificationError = useWorkbenchStore((state) => state.hostVerificationError)
  const hostVerificationParamsKey = useWorkbenchStore((state) => state.hostVerificationParamsKey)
  const runHostVerification = useWorkbenchStore((state) => state.runHostVerification)
  const dirtyScripts = useWorkbenchStore((state) => state.dirtyScripts)
  const sourceFingerprint = useWorkbenchStore((state) => state.sourceFingerprint)

  const active = mode === 'authoritative'
  // 出错时回退本地预览（需求：保持显示上一次的本地预览，错误不静默）
  const showingAuthoritative = active && authoritative !== null && error === null
  const stale = active && paramsKey !== null && paramsKey !== JSON.stringify(draftParameters)
  const verificationStale = Boolean(hostVerification?.stale)
    || Boolean(hostVerification && hostVerification.source_fingerprint !== sourceFingerprint)
    || (hostVerificationParamsKey !== null && hostVerificationParamsKey !== JSON.stringify(draftParameters))

  return {
    preview: showingAuthoritative ? authoritative : localPreview,
    sourceControl: {
      available: Boolean(tapirStatus?.archicad_connected),
      active,
      loading,
      error: active ? error : null,
      stale,
      showingAuthoritative,
      onModeChange: (next: PreviewSourceMode) => void setPreviewSourceMode(next),
      onRefresh: () => void loadAuthoritativePreview(),
      verificationStatus: verificationStale ? 'stale' : (hostVerification?.status ?? 'not_checked'),
      verificationLoading: hostVerificationLoading,
      verificationError: hostVerificationError,
      verificationDisabled: Object.values(dirtyScripts).some(Boolean),
      onVerify: () => void runHostVerification(),
    },
  }
}

/** 2D 与 3D 共用同一次 Archicad 求值及来源开关。 */
export function usePreview2DSource(localPreview: Preview2DPayload | null): {
  preview: Preview2DPayload | null
  sourceControl: PreviewSourceControl
} {
  const mode = useWorkbenchStore((state) => state.previewSourceMode)
  const authoritative = useWorkbenchStore((state) => state.previewAuthoritative2d)
  const loading = useWorkbenchStore((state) => state.previewAuthoritativeLoading)
  const error = useWorkbenchStore((state) => state.previewAuthoritativeError)
  const paramsKey = useWorkbenchStore((state) => state.previewAuthoritativeParamsKey)
  const draftParameters = useWorkbenchStore((state) => state.draftParameters)
  const setPreviewSourceMode = useWorkbenchStore((state) => state.setPreviewSourceMode)
  const loadAuthoritativePreview = useWorkbenchStore((state) => state.loadAuthoritativePreview)
  const tapirStatus = useWorkbenchStore((state) => state.tapirStatus)
  const hostVerification = useWorkbenchStore((state) => state.hostVerification)
  const hostVerificationLoading = useWorkbenchStore((state) => state.hostVerificationLoading)
  const hostVerificationError = useWorkbenchStore((state) => state.hostVerificationError)
  const hostVerificationParamsKey = useWorkbenchStore((state) => state.hostVerificationParamsKey)
  const runHostVerification = useWorkbenchStore((state) => state.runHostVerification)
  const dirtyScripts = useWorkbenchStore((state) => state.dirtyScripts)
  const sourceFingerprint = useWorkbenchStore((state) => state.sourceFingerprint)

  const active = mode === 'authoritative'
  const showingAuthoritative = active && authoritative !== null && error === null
  const stale = active && paramsKey !== null && paramsKey !== JSON.stringify(draftParameters)
  const verificationStale = Boolean(hostVerification?.stale)
    || Boolean(hostVerification && hostVerification.source_fingerprint !== sourceFingerprint)
    || (hostVerificationParamsKey !== null && hostVerificationParamsKey !== JSON.stringify(draftParameters))
  return {
    preview: showingAuthoritative ? authoritative : localPreview,
    sourceControl: {
      available: Boolean(tapirStatus?.archicad_connected),
      active,
      loading,
      error: active ? error : null,
      stale,
      showingAuthoritative,
      onModeChange: (next: PreviewSourceMode) => void setPreviewSourceMode(next),
      onRefresh: () => void loadAuthoritativePreview(),
      verificationStatus: verificationStale ? 'stale' : (hostVerification?.status ?? 'not_checked'),
      verificationLoading: hostVerificationLoading,
      verificationError: hostVerificationError,
      verificationDisabled: Object.values(dirtyScripts).some(Boolean),
      onVerify: () => void runHostVerification(),
    },
  }
}
