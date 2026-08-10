import { useT } from '../i18n'

interface PreviewGhostToggleProps {
  /** store 有任务前快照才可开（无则 disabled + 说明 title） */
  available: boolean
  active: boolean
  onToggle: () => void
}

/**
 * 修改前后对比开关（P2a）：纯视图态，每视口实例独立、不进 store。
 * ghost 为 null（无可对比版本）时 disabled。
 */
export function PreviewGhostToggle({ available, active, onToggle }: PreviewGhostToggleProps) {
  const t = useT()
  return (
    <button
      type="button"
      className={`viewport-action-button${active ? ' active' : ''}`}
      disabled={!available}
      onClick={onToggle}
      title={available ? t('preview.ghost.toggleTitle') : t('preview.ghost.unavailableTitle')}
    >
      {t('preview.ghost.toggle')}
    </button>
  )
}
