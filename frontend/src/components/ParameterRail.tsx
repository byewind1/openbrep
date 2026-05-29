import type { WorkbenchParameter } from '../api/types'

interface ParameterRailProps {
  title: string
  parameters?: WorkbenchParameter[]
  sections?: Array<{ title: string; parameters: WorkbenchParameter[] }>
  draftParameters: Record<string, unknown>
  onChange: (name: string, value: unknown) => void
  onApply: () => void
  onReset: () => void
  applying: boolean
}

export function ParameterRail({
  title,
  parameters = [],
  sections,
  draftParameters,
  onChange,
  onApply,
  onReset,
  applying,
}: ParameterRailProps) {
  const renderedSections = sections ?? [{ title, parameters }]
  const count = renderedSections.reduce((total, section) => total + section.parameters.length, 0)
  const dirtyCount = Object.keys(draftParameters).length

  return (
    <aside className="parameter-rail">
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          <span>{dirtyCount ? `${dirtyCount} changed / ${count}` : `${count}`}</span>
        </div>
        <div className="panel-actions">
          <button type="button" disabled={!dirtyCount || applying} onClick={onReset}>
            Reset
          </button>
          <button type="button" className="primary-action" disabled={!dirtyCount || applying} onClick={onApply}>
            {applying ? 'Applying' : 'Apply'}
          </button>
        </div>
      </div>
      {renderedSections.map((section) => (
        <section className="parameter-section" key={section.title}>
          <div className="section-label">{section.title}</div>
          <div className="parameter-list">
            {section.parameters.map((parameter) => (
              <ParameterControl
                key={parameter.name}
                parameter={parameter}
                value={draftParameters[parameter.name] ?? parseParameterValue(parameter)}
                onChange={onChange}
              />
            ))}
          </div>
        </section>
      ))}
    </aside>
  )
}

function ParameterControl({
  parameter,
  value,
  onChange,
}: {
  parameter: WorkbenchParameter
  value: unknown
  onChange: (name: string, value: unknown) => void
}) {
  const label = parameter.name
  if (parameter.type_tag === 'Boolean') {
    return (
      <label className="parameter-control compact-control">
        <span className="parameter-name">{label}</span>
        <input
          className="toggle-input"
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(parameter.name, event.currentTarget.checked)}
        />
      </label>
    )
  }

  if (parameter.type_tag === 'Integer') {
    return (
      <label className="parameter-control compact-control">
        <span className="parameter-name">{label}</span>
        <input
          className="numeric-input"
          type="number"
          min={0}
          step={1}
          value={Number(value)}
          onChange={(event) => onChange(parameter.name, Number(event.currentTarget.value))}
        />
      </label>
    )
  }

  if (['Length', 'Angle', 'RealNum'].includes(parameter.type_tag)) {
    return (
      <label className="parameter-control compact-control">
        <span className="parameter-name">{label}</span>
        <input
          className="numeric-input"
          type="number"
          step={parameter.type_tag === 'Angle' ? 1 : 0.01}
          value={Number(value)}
          onChange={(event) => onChange(parameter.name, Number(event.currentTarget.value))}
        />
      </label>
    )
  }

  return (
    <label className="parameter-control compact-control">
      <span className="parameter-name">{label}</span>
      <input
        className="text-input"
        type="text"
        value={String(value)}
        onChange={(event) => onChange(parameter.name, event.currentTarget.value)}
      />
    </label>
  )
}

function parseParameterValue(parameter: WorkbenchParameter): unknown {
  if (parameter.type_tag === 'Boolean') return parameter.value === '1'
  if (['Length', 'Angle', 'RealNum', 'Integer'].includes(parameter.type_tag)) return Number(parameter.value)
  return parameter.value
}
