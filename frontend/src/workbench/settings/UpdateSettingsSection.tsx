import { useEffect, useState } from 'react'
import {
  checkForUpdate,
  downloadAndInstallUpdate,
  fetchAppVersion,
  isTauriDesktop,
  openReleasesPage,
  type UpdateInfo,
} from '../../api/updater'
import { useT } from '../../i18n'

type CheckState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'latest' }
  | { kind: 'available'; info: UpdateInfo }
  | { kind: 'updating' }
  | { kind: 'error' }

/**
 * 设置里的「软件更新」区块：当前版本 + 手动检查更新。
 * 仅在 Tauri 桌面环境有实际行为；浏览器下显示提示文案。
 */
export function UpdateSettingsSection() {
  const t = useT()
  const [version, setVersion] = useState<string | null>(null)
  const [state, setState] = useState<CheckState>({ kind: 'idle' })

  useEffect(() => {
    if (!isTauriDesktop()) return
    fetchAppVersion()
      .then(setVersion)
      .catch(() => setVersion(null))
  }, [])

  function runCheck() {
    setState({ kind: 'checking' })
    checkForUpdate()
      .then((info) => {
        setState(info ? { kind: 'available', info } : { kind: 'latest' })
      })
      .catch(() => setState({ kind: 'error' }))
  }

  function runUpdate(info: UpdateInfo) {
    setState({ kind: 'updating' })
    downloadAndInstallUpdate(() => {}).catch(() => setState({ kind: 'error' }))
  }

  return (
    <div className="settings-metadata-grid">
      <span>{t('update.settings.title')}</span>
      <div className="settings-actions inline">
        {version ? <strong>{t('update.settings.current', { version })}</strong> : null}
        {isTauriDesktop() ? (
          <>
            <button
              type="button"
              disabled={state.kind === 'checking' || state.kind === 'updating'}
              onClick={runCheck}
            >
              {state.kind === 'checking' ? t('update.settings.checking') : t('update.settings.check')}
            </button>
            {state.kind === 'latest' ? <span>{t('update.settings.latest')}</span> : null}
            {state.kind === 'available' ? (
              <>
                <span>{t('update.settings.available', { version: state.info.version })}</span>
                <button
                  type="button"
                  className="primary-action"
                  onClick={() => runUpdate(state.info)}
                >
                  {t('update.banner.action')}
                </button>
              </>
            ) : null}
            {state.kind === 'updating' ? <span>{t('update.banner.installing')}</span> : null}
            {state.kind === 'error' ? (
              <>
                <span className="settings-save-error">{t('update.settings.error')}</span>
                <button type="button" onClick={() => void openReleasesPage()}>
                  {t('update.banner.manual')}
                </button>
              </>
            ) : null}
          </>
        ) : (
          <span>{t('update.settings.notDesktop')}</span>
        )}
      </div>
    </div>
  )
}
