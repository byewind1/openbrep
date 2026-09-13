import { isTauriDesktop } from '../../api/updater'
import { useT } from '../../i18n'
import { useUpdateStore } from '../../state/updateStore'

/**
 * 顶栏版本 pill（Hermes 式更新入口）：显示当前版本号，
 * 有可用更新时带「有更新」徽标；点击打开更新对话框。
 * 仅 Tauri 桌面环境渲染。
 */
export function UpdatePill() {
  const t = useT()
  const info = useUpdateStore((s) => s.info)
  const currentVersion = useUpdateStore((s) => s.currentVersion)
  const openDialog = useUpdateStore((s) => s.openDialog)

  if (!isTauriDesktop()) return null

  return (
    <button
      type="button"
      className={`model-pill update-pill${info ? ' has-update' : ''}`}
      data-testid="update-pill"
      title={
        info
          ? t('update.settings.available', { version: info.version })
          : t('update.pill.title', { version: currentVersion ?? '…' })
      }
      onClick={openDialog}
    >
      <span className="model-pill-dot" aria-hidden="true" />
      <span className="model-pill-name">v{currentVersion ?? '…'}</span>
      {info ? <span className="update-pill-badge">{t('update.pill.new')}</span> : null}
    </button>
  )
}
