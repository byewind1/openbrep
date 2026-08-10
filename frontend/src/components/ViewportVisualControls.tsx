import { useT } from '../i18n'
import type { PreviewQuality } from '../api/types'

interface ShadowsToggleProps {
  enabled: boolean
  onToggle: () => void
}

/** 接地阴影开关（P1b）：solid/mono/random 默认开，wire/xray 默认关，可手动覆盖 */
export function ShadowsToggle({ enabled, onToggle }: ShadowsToggleProps) {
  const t = useT()
  return (
    <button
      type="button"
      className={`viewport-action-button${enabled ? ' active' : ''}`}
      onClick={onToggle}
      title={t('preview.shadows.toggleTitle')}
    >
      {t('preview.shadows.toggle')}
    </button>
  )
}

interface QualityToggleProps {
  quality: PreviewQuality
  onChange: (quality: PreviewQuality) => void
}

/** 预览质量档切换（P1b）：fast/accurate 两档，切换即重取预览 */
export function QualityToggle({ quality, onChange }: QualityToggleProps) {
  const t = useT()
  return (
    <>
      <button
        type="button"
        className={`viewport-action-button${quality === 'fast' ? ' active' : ''}`}
        onClick={() => onChange('fast')}
        title={t('preview.quality.toggleTitle')}
      >
        {t('preview.quality.fast')}
      </button>
      <button
        type="button"
        className={`viewport-action-button${quality === 'accurate' ? ' active' : ''}`}
        onClick={() => onChange('accurate')}
        title={t('preview.quality.toggleTitle')}
      >
        {t('preview.quality.accurate')}
      </button>
    </>
  )
}
