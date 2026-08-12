import { lazy, Suspense, useState } from 'react'
import type { ChangeEvent } from 'react'
import { AddParameterInlineForm } from './AddParameterInlineForm'
import { ParameterMetadataEditor } from './ParameterMetadataEditor'
import { useT } from '../i18n'
import type { AddParameterRequest, UpdateParameterRequest, WorkbenchParameter } from '../api/types'

// P11：参数面板「参数脚本」tab 复用现有脚本编辑器组件（Monaco），
// 与主编辑器一致走 lazy 加载，避免启动即拉 monaco 体积。
const ScriptEditor = lazy(() => import('./ScriptEditor').then((m) => ({ default: m.ScriptEditor })))

interface ParameterRailProps {
  title: string
  parameters?: WorkbenchParameter[]
  sections?: Array<{ title: string; parameters: WorkbenchParameter[] }>
  parameterIssues: string[]
  draftParameters: Record<string, unknown>
  onChange: (name: string, value: unknown) => void
  onApply: () => void
  onReset: () => void
  onAddParameter: (parameter: AddParameterRequest) => Promise<boolean>
  onUpdateParameter: (parameter: UpdateParameterRequest) => Promise<boolean>
  onDeleteParameter: (name: string) => Promise<boolean>
  onValidateParameters: () => void
  applying: boolean
  /** P11：参数脚本（vl.gdl）视图所需状态与回调，复用既有脚本保存/脏状态链路 */
  paramScriptContent?: string
  paramScriptDirty?: boolean
  paramScriptSaving?: boolean
  onParamScriptChange?: (content: string) => void
  onParamScriptSave?: () => void
}

type ParameterPanelView = 'params' | 'script'

export function ParameterRail({
  title,
  parameters = [],
  sections,
  parameterIssues,
  draftParameters,
  onChange,
  onApply,
  onReset,
  onAddParameter,
  onUpdateParameter,
  onDeleteParameter,
  onValidateParameters,
  applying,
  paramScriptContent,
  paramScriptDirty,
  paramScriptSaving,
  onParamScriptChange,
  onParamScriptSave,
}: ParameterRailProps) {
  const t = useT()
  const [view, setView] = useState<ParameterPanelView>('params')
  const renderedSections = sections ?? [{ title, parameters }]
  const count = renderedSections.reduce((total, section) => total + section.parameters.length, 0)
  const dirtyCount = Object.keys(draftParameters).length

  return (
    <aside className="parameter-rail">
      <div className="rail-tabs" role="tablist" aria-label="Parameter panel views">
        <button
          type="button"
          role="tab"
          aria-selected={view === 'params'}
          className={`rail-tab${view === 'params' ? ' active' : ''}`}
          onClick={() => setView('params')}
        >
          {t('parameter.view.params')}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'script'}
          className={`rail-tab${view === 'script' ? ' active' : ''}`}
          onClick={() => setView('script')}
        >
          {t('parameter.view.script')}
        </button>
      </div>
      {view === 'params' ? (
        <>
          <div className="panel-heading">
            <div>
              <h2>{title}</h2>
              <span>{dirtyCount ? `${dirtyCount} changed / ${count}` : `${count}`}</span>
            </div>
            <div className="panel-actions">
              <button type="button" disabled={!dirtyCount || applying} onClick={onReset}>
                Reset
              </button>
              <button type="button" disabled={!dirtyCount || applying} onClick={onApply}>
                {applying ? 'Applying' : 'Apply'}
              </button>
            </div>
          </div>
          <AddParameterInlineForm
            parameters={renderedSections.flatMap((section) => section.parameters)}
            issues={parameterIssues}
            applying={applying}
            onAdd={onAddParameter}
            onValidate={onValidateParameters}
          />
          <ParameterMetadataEditor
            parameters={renderedSections.flatMap((section) => section.parameters)}
            applying={applying}
            onUpdate={onUpdateParameter}
            onDelete={onDeleteParameter}
          />
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
        </>
      ) : (
        <div className="parameter-script-view">
          <div className="panel-heading">
            <div>
              <h2>vl.gdl</h2>
              <span>{paramScriptDirty ? t('parameter.script.unsaved') : t('parameter.script.saved')}</span>
            </div>
            <div className="panel-actions">
              <button
                type="button"
                disabled={!paramScriptDirty || paramScriptSaving}
                onClick={onParamScriptSave}
              >
                {paramScriptSaving ? t('parameter.script.saving') : t('parameter.script.save')}
              </button>
            </div>
          </div>
          <Suspense fallback={<div className="editor-loading" />}>
            <ScriptEditor
              scriptName="vl.gdl"
              content={paramScriptContent ?? ''}
              onChange={(content) => onParamScriptChange?.(content)}
              isDirty={Boolean(paramScriptDirty)}
            />
          </Suspense>
        </div>
      )}
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
        <span className="parameter-name" title={label}>{label}</span>
        <input
          className="toggle-input"
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(parameter.name, event.currentTarget.checked)}
        />
      </label>
    )
  }

  // P11：VALUES 枚举参数（字符串/数值）用下拉，取代自由文本框；
  // 当前值不在枚举内时顶部追加「当前值：X（不在 VALUES 列表）」项，不静默改写。
  // Boolean 枚举（如 VALUES "show_in_3d" 0, 1）保持 checkbox（0/1 二值体验已完备）。
  if (parameter.options && parameter.options.length > 0) {
    return (
      <label className="parameter-control compact-control">
        <span className="parameter-name" title={label}>{label}</span>
        <EnumSelect parameter={parameter} value={value} onChange={onChange} />
      </label>
    )
  }

  if (parameter.type_tag === 'Integer') {
    return (
      <label className="parameter-control compact-control">
        <span className="parameter-name" title={label}>{label}</span>
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
        <span className="parameter-name" title={label}>{label}</span>
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
      <span className="parameter-name" title={label}>{label}</span>
      <input
        className="text-input"
        type="text"
        value={String(value)}
        onChange={(event) => onChange(parameter.name, event.currentTarget.value)}
      />
    </label>
  )
}

const NOT_IN_VALUES = '__not_in_values__'

function EnumSelect({
  parameter,
  value,
  onChange,
}: {
  parameter: WorkbenchParameter
  value: unknown
  onChange: (name: string, value: unknown) => void
}) {
  const t = useT()
  const options = parameter.options ?? []
  const current = String(value)
  const matched = options.some((option) => String(option) === current)

  function handleChange(event: ChangeEvent<HTMLSelectElement>) {
    const selected = event.currentTarget.value
    if (selected === NOT_IN_VALUES) return
    const raw = options.find((option) => String(option) === selected)
    if (raw === undefined) return
    onChange(parameter.name, raw)
  }

  return (
    <select className="enum-select" value={matched ? current : NOT_IN_VALUES} onChange={handleChange}>
      {!matched ? (
        <option value={NOT_IN_VALUES}>{t('parameter.enumFallback', { value: current })}</option>
      ) : null}
      {options.map((option) => (
        <option key={String(option)} value={String(option)}>
          {String(option)}
        </option>
      ))}
    </select>
  )
}

function parseParameterValue(parameter: WorkbenchParameter): unknown {
  if (parameter.type_tag === 'Boolean') return parameter.value === '1'
  if (['Length', 'Angle', 'RealNum', 'Integer'].includes(parameter.type_tag)) return Number(parameter.value)
  return parameter.value
}
