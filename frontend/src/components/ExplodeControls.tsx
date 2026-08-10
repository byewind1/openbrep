import { useT } from '../i18n'

interface ExplodeControlsProps {
  factor: number
  onChange: (factor: number) => void
}

/**
 * 爆炸图工具栏控件（P2b）：开关 + 程度滑杆（0–1，0=关闭）。
 * 纯视图态每视口实例独立；滑杆是主控制，开关只是快捷开/关。
 */
export function ExplodeControls({ factor, onChange }: ExplodeControlsProps) {
  const t = useT()
  const active = factor > 0
  return (
    <>
      <button
        type="button"
        className={`viewport-action-button${active ? ' active' : ''}`}
        onClick={() => onChange(active ? 0 : 0.5)}
        title={t('preview.explode.toggleTitle')}
      >
        {t('preview.explode.toggle')}
      </button>
      {active ? (
        <input
          type="range"
          className="explode-slider"
          min={0}
          max={1}
          step={0.01}
          value={factor}
          onChange={(event) => onChange(Number(event.target.value))}
          aria-label={t('preview.explode.sliderAria')}
          title={t('preview.explode.sliderTitle')}
        />
      ) : null}
    </>
  )
}
