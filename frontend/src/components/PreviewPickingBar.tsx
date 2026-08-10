import { useT } from '../i18n'
import type { PreviewSelection } from './previewPicking'

interface PreviewPickingBarProps {
  selection: PreviewSelection
  onJump: () => void
  onDismiss: () => void
}

/**
 * 3D 预览拾取信息条（P1a）：显示选中 mesh 由哪行 GDL 生成，
 * 提供跳转到编辑器对应行的按钮；无 source_ref 时如实显示并禁用跳转。
 */
export function PreviewPickingBar({ selection, onJump, onDismiss }: PreviewPickingBarProps) {
  const t = useT()
  const source = selection.source
  return (
    <div className="viewport-pick-bar" role="status" aria-label={t('preview.pick.barAriaLabel')}>
      <span className="pick-mesh-name" title={selection.meshName}>
        {selection.meshName}
      </span>
      {source ? (
        <span className="pick-source" title={source.summary}>
          {source.summary}
        </span>
      ) : (
        <span className="pick-no-source">{t('preview.pick.noSource')}</span>
      )}
      <button
        type="button"
        className="pick-jump"
        onClick={onJump}
        disabled={!source}
        title={t('preview.pick.jumpToSource')}
      >
        {t('preview.pick.jumpToSource')}
      </button>
      <button
        type="button"
        className="pick-dismiss"
        onClick={onDismiss}
        aria-label={t('preview.pick.dismiss')}
        title={t('preview.pick.dismiss')}
      >
        ×
      </button>
    </div>
  )
}
