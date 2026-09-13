import { useEffect, useState } from 'react'
import {
  checkForUpdate,
  downloadAndInstallUpdate,
  isTauriDesktop,
  openReleasesPage,
  type UpdateInfo,
} from '../../api/updater'
import { useT } from '../../i18n'
import './UpdateBanner.css'

type Phase =
  | { kind: 'hidden' }
  | { kind: 'available'; info: UpdateInfo }
  | { kind: 'downloading'; info: UpdateInfo; downloaded: number; total: number | null }
  | { kind: 'installing'; info: UpdateInfo }
  | { kind: 'error'; info: UpdateInfo; message: string }

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

/**
 * 启动时静默检查更新；有新版本时在顶栏下方显示横幅。
 * 仅在 Tauri 桌面环境激活；浏览器/测试环境渲染 null。
 */
export function UpdateBanner() {
  const t = useT()
  const [phase, setPhase] = useState<Phase>({ kind: 'hidden' })

  useEffect(() => {
    if (!isTauriDesktop()) return
    let cancelled = false
    checkForUpdate()
      .then((info) => {
        if (!cancelled && info) setPhase({ kind: 'available', info })
      })
      .catch(() => {
        // 静默失败：断网、无已发布 Release、开发环境都不打扰用户
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (phase.kind === 'hidden') return null

  const { info } = phase

  function startDownload() {
    if (phase.kind !== 'available' && phase.kind !== 'error') return
    const current = phase.info
    setPhase({ kind: 'downloading', info: current, downloaded: 0, total: null })
    downloadAndInstallUpdate(({ downloaded, total }) => {
      setPhase((prev) =>
        prev.kind === 'downloading' ? { ...prev, downloaded, total } : prev,
      )
    })
      .then(() => {
        setPhase({ kind: 'installing', info: current })
      })
      .catch((e) => {
        setPhase({ kind: 'error', info: current, message: String(e) })
      })
  }

  return (
    <div className="update-banner" role="status">
      {phase.kind === 'available' && (
        <>
          <span className="update-banner-text">
            {t('update.banner.title', { version: info.version })}
            <span className="update-banner-current">
              {t('update.banner.current', { version: info.current_version })}
            </span>
          </span>
          <button type="button" className="primary-action" onClick={startDownload}>
            {t('update.banner.action')}
          </button>
          <button type="button" onClick={() => setPhase({ kind: 'hidden' })}>
            {t('update.banner.later')}
          </button>
        </>
      )}
      {phase.kind === 'downloading' && (
        <span className="update-banner-text">
          {t('update.banner.downloading')}
          {phase.total
            ? ` ${formatBytes(phase.downloaded)} / ${formatBytes(phase.total)}`
            : ` ${formatBytes(phase.downloaded)}`}
          <progress
            className="update-banner-progress"
            value={phase.downloaded}
            max={phase.total ?? undefined}
          />
        </span>
      )}
      {phase.kind === 'installing' && (
        <span className="update-banner-text">{t('update.banner.installing')}</span>
      )}
      {phase.kind === 'error' && (
        <>
          <span className="update-banner-text" title={phase.message}>
            {t('update.banner.failed')}
          </span>
          <button type="button" className="primary-action" onClick={() => void openReleasesPage()}>
            {t('update.banner.manual')}
          </button>
          <button type="button" onClick={startDownload}>
            {t('update.banner.retry')}
          </button>
          <button type="button" onClick={() => setPhase({ kind: 'hidden' })}>
            {t('update.banner.later')}
          </button>
        </>
      )}
    </div>
  )
}
