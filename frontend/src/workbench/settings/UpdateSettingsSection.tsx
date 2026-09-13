import { isTauriDesktop, openReleasesPage } from '../../api/updater'
import { useT } from '../../i18n'
import { useUpdateStore } from '../../state/updateStore'

/**
 * 设置里的「软件更新」区块：当前版本 + 手动检查更新。
 * 发现新版本时打开统一的更新对话框（更新要点 + 一键更新）。
 * 仅在 Tauri 桌面环境有实际行为；浏览器下显示提示文案。
 */
export function UpdateSettingsSection() {
  const t = useT()
  const currentVersion = useUpdateStore((s) => s.currentVersion)
  const info = useUpdateStore((s) => s.info)
  const checking = useUpdateStore((s) => s.checking)
  const checked = useUpdateStore((s) => s.checked)
  const error = useUpdateStore((s) => s.error)
  const phase = useUpdateStore((s) => s.phase)
  const check = useUpdateStore((s) => s.check)
  const openDialog = useUpdateStore((s) => s.openDialog)

  return (
    <div className="settings-metadata-grid">
      <span>{t('update.settings.title')}</span>
      <div className="settings-actions inline">
        {currentVersion ? <strong>{t('update.settings.current', { version: currentVersion })}</strong> : null}
        {isTauriDesktop() ? (
          <>
            <button type="button" disabled={checking} onClick={() => void check()}>
              {checking ? t('update.settings.checking') : t('update.settings.check')}
            </button>
            {info ? (
              <>
                <span>{t('update.settings.available', { version: info.version })}</span>
                <button type="button" className="primary-action" onClick={openDialog}>
                  {t('update.banner.action')}
                </button>
              </>
            ) : null}
            {!info && checked && !checking && !error ? (
              <span>{t('update.settings.latest')}</span>
            ) : null}
            {phase === 'error' || (error && checked) ? (
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
