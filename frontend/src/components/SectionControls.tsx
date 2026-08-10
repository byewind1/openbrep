import { useT } from '../i18n'
import type { SectionAxis } from './previewSection'

interface SectionControlsProps {
  active: boolean
  axis: SectionAxis
  t: number
  onToggle: () => void
  onAxisChange: (axis: SectionAxis) => void
  onTChange: (t: number) => void
}

const SECTION_AXES: SectionAxis[] = ['x', 'y', 'z']

/**
 * 剖切面工具栏控件（P1c）：开关 + 轴向三选 + 位置滑杆。
 * 开关常驻；开启后才显示轴向与滑杆（滑杆是精调，主交互在视口手柄）。
 */
export function SectionControls({ active, axis, t, onToggle, onAxisChange, onTChange }: SectionControlsProps) {
  const t_ = useT()
  return (
    <>
      <button
        type="button"
        className={`viewport-action-button${active ? ' active' : ''}`}
        onClick={onToggle}
        title={t_('preview.section.toggleTitle')}
      >
        {t_('preview.section.toggle')}
      </button>
      {active ? (
        <>
          {SECTION_AXES.map((item) => (
            <button
              key={item}
              type="button"
              className={`viewport-action-button${axis === item ? ' active' : ''}`}
              onClick={() => onAxisChange(item)}
              title={t_('preview.section.axisTitle')}
            >
              {item.toUpperCase()}
            </button>
          ))}
          <input
            type="range"
            className="section-slider"
            min={0}
            max={1}
            step={0.005}
            value={t}
            onChange={(event) => onTChange(Number(event.target.value))}
            aria-label={t_('preview.section.sliderAria')}
            title={t_('preview.section.sliderTitle')}
          />
        </>
      ) : null}
    </>
  )
}
