import type { KeyboardEvent } from 'react'
import { openReleasesPage } from '../../api/updater'
import { useT } from '../../i18n'
import { useUpdateStore } from '../../state/updateStore'
import { parseReleaseHighlights, type HighlightKind } from './releaseNotes'
import './update.css'

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

const KIND_LABEL: Record<HighlightKind, 'update.kind.feature' | 'update.kind.fix' | 'update.kind.other'> = {
  feature: 'update.kind.feature',
  fix: 'update.kind.fix',
  other: 'update.kind.other',
}

/**
 * 更新对话框（Hermes 式：顶栏版本 pill 点开）。内容：
 * 版本号对比、Release notes 解析出的更新要点（新增/修复/其他）、
 * 一键下载安装（带进度），失败降级为手动下载页。
 */
export function UpdateDialog() {
  const t = useT()
  const open = useUpdateStore((s) => s.dialogOpen)
  const channel = useUpdateStore((s) => s.channel)
  const info = useUpdateStore((s) => s.info)
  const currentVersion = useUpdateStore((s) => s.currentVersion)
  const checked = useUpdateStore((s) => s.checked)
  const checking = useUpdateStore((s) => s.checking)
  const phase = useUpdateStore((s) => s.phase)
  const downloaded = useUpdateStore((s) => s.downloaded)
  const total = useUpdateStore((s) => s.total)
  const error = useUpdateStore((s) => s.error)
  const check = useUpdateStore((s) => s.check)
  const startUpdate = useUpdateStore((s) => s.startUpdate)
  const closeDialog = useUpdateStore((s) => s.closeDialog)

  if (!open) return null

  const highlights = parseReleaseHighlights(info?.notes)
  const busy = phase === 'downloading' || phase === 'installing'

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape' && !busy) {
      event.preventDefault()
      event.stopPropagation()
      closeDialog()
    }
  }

  return (
    <div
      className="themed-dialog-overlay"
      onClick={() => {
        if (!busy) closeDialog()
      }}
      onKeyDown={handleKeyDown}
    >
      <div
        className="themed-dialog update-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={t('update.dialog.title')}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="themed-dialog-title">
          {info ? t('update.dialog.titleNew', { version: info.version }) : t('update.dialog.title')}
        </h2>

        <p className="themed-dialog-message update-dialog-version">
          {currentVersion ? t('update.settings.current', { version: currentVersion }) : null}
          {info ? (
            <>
              {' → '}
              <strong>{info.version}</strong>
            </>
          ) : null}
        </p>

        {!info && checking ? (
          <p className="themed-dialog-message">{t('update.settings.checking')}</p>
        ) : null}
        {!info && checked && !checking ? (
          error ? (
            <p className="themed-dialog-message update-dialog-error">
              {t('update.settings.error')}
              <br />
              <small>{error}</small>
            </p>
          ) : (
            <p className="themed-dialog-message">{t('update.settings.latest')}</p>
          )
        ) : null}

        {info ? (
          <div className="update-highlights">
            <h3 className="update-highlights-title">{t('update.dialog.highlights')}</h3>
            {highlights.length > 0 ? (
              <ul className="update-highlights-list">
                {highlights.map((h) => (
                  <li key={h.text}>
                    <span className={`update-kind update-kind-${h.kind}`}>{t(KIND_LABEL[h.kind])}</span>
                    <span className="update-highlight-text">{h.text}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="themed-dialog-message">{t('update.dialog.noNotes')}</p>
            )}
          </div>
        ) : null}

        {phase === 'downloading' ? (
          <p className="themed-dialog-message">
            {t('update.banner.downloading')}
            {total ? ` ${formatBytes(downloaded)} / ${formatBytes(total)}` : ` ${formatBytes(downloaded)}`}
            <progress className="update-dialog-progress" value={downloaded} max={total ?? undefined} />
          </p>
        ) : null}
        {phase === 'installing' ? (
          <p className="themed-dialog-message">{t('update.banner.installing')}</p>
        ) : null}
        {phase === 'error' ? (
          <p className="themed-dialog-message update-dialog-error" title={error ?? ''}>
            {t('update.banner.failed')}
          </p>
        ) : null}

        <div className="themed-dialog-actions">
          <button type="button" onClick={() => void openReleasesPage(channel)}>
            {t('update.dialog.fullNotes')}
          </button>
          {!info && !checking ? (
            <button type="button" onClick={() => void check()}>
              {t('update.settings.check')}
            </button>
          ) : null}
          {phase === 'error' ? (
            <button type="button" onClick={() => void startUpdate()}>
              {t('update.banner.retry')}
            </button>
          ) : null}
          <button type="button" disabled={busy} onClick={closeDialog}>
            {t('update.dialog.close')}
          </button>
          {info && phase !== 'installing' ? (
            <button
              type="button"
              className="primary-action"
              disabled={busy}
              onClick={() => void startUpdate()}
            >
              {phase === 'downloading' ? t('update.banner.downloading') : t('update.banner.action')}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  )
}
