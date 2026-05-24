import type { WorkbenchParameter } from '../api/types'

interface ParameterRailProps {
  title: string
  parameters: WorkbenchParameter[]
  draftParameters: Record<string, unknown>
  onChange: (name: string, value: unknown) => void
}

export function ParameterRail({ title, parameters, draftParameters, onChange }: ParameterRailProps) {
  return (
    <aside className="parameter-rail">
      <div className="panel-heading">
        <h2>{title}</h2>
        <span>{parameters.length}</span>
      </div>
      <div className="parameter-list">
        {parameters.map((parameter) => (
          <ParameterControl
            key={parameter.name}
            parameter={parameter}
            value={draftParameters[parameter.name] ?? parseParameterValue(parameter)}
            onChange={onChange}
          />
        ))}
      </div>
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
  const label = parameter.description || parameter.name
  if (parameter.type_tag === 'Boolean') {
    return (
      <label className="parameter-control inline-control">
        <span>
          <strong>{label}</strong>
          <small>{parameter.name}</small>
        </span>
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(parameter.name, event.currentTarget.checked)}
        />
      </label>
    )
  }

  if (parameter.type_tag === 'Integer') {
    return (
      <label className="parameter-control">
        <ControlLabel label={label} parameter={parameter} value={String(value)} />
        <input
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
    const numeric = Number(value)
    const max = parameter.type_tag === 'Angle' ? 360 : Math.max(1, numeric * 2.5)
    return (
      <label className="parameter-control">
        <ControlLabel label={label} parameter={parameter} value={formatValue(value)} />
        <input
          type="range"
          min={0}
          max={max}
          step={parameter.type_tag === 'Angle' ? 1 : 0.01}
          value={numeric}
          onChange={(event) => onChange(parameter.name, Number(event.currentTarget.value))}
        />
      </label>
    )
  }

  return (
    <label className="parameter-control">
      <ControlLabel label={label} parameter={parameter} value={String(value)} />
      <input type="text" value={String(value)} onChange={(event) => onChange(parameter.name, event.currentTarget.value)} />
    </label>
  )
}

function ControlLabel({ label, parameter, value }: { label: string; parameter: WorkbenchParameter; value: string }) {
  return (
    <span className="control-label">
      <span>
        <strong>{label}</strong>
        <small>{parameter.name}</small>
      </span>
      <code>{value}</code>
    </span>
  )
}

function parseParameterValue(parameter: WorkbenchParameter): unknown {
  if (parameter.type_tag === 'Boolean') return parameter.value === '1'
  if (['Length', 'Angle', 'RealNum', 'Integer'].includes(parameter.type_tag)) return Number(parameter.value)
  return parameter.value
}

function formatValue(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(2) : String(value)
}
