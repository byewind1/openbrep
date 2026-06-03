import { useState } from 'react'
import type { LlmConnectionTestResult, LlmSettings } from '../../api/types'

interface AiSettingsPanelProps {
  draft: LlmSettings
  testResult: LlmConnectionTestResult | null
  testing: boolean
  onChange: (settings: LlmSettings) => void
  onTestConnection: () => void
}

export function AiSettingsPanel({ draft, testResult, testing, onChange, onTestConnection }: AiSettingsPanelProps) {
  const [manualModelMode, setManualModelMode] = useState(false)
  const customModelOptions = draft.model_groups?.custom ?? []
  const officialModelOptions = draft.model_groups?.official ?? []
  const groupedModelIds = new Set([...customModelOptions, ...officialModelOptions].map((option) => option.id))
  const fallbackModelOptions = (draft.models ?? [])
    .filter((model) => model && !groupedModelIds.has(model))
    .map((model) => ({ id: model, label: model, kind: 'official' as const, provider: '', has_api_key: false }))
  const knownModelIds = new Set([...groupedModelIds, ...fallbackModelOptions.map((option) => option.id)])
  const allModelOptions = [...customModelOptions, ...officialModelOptions, ...fallbackModelOptions]
  const selectedModelMeta = allModelOptions.find((option) => option.id === draft.model)
  const activeModelCategory = manualModelMode ? 'exact' : selectedModelMeta?.kind ?? (knownModelIds.has(draft.model) ? 'official' : 'exact')
  const visibleModelOptions =
    activeModelCategory === 'custom'
      ? customModelOptions
      : activeModelCategory === 'official'
        ? [...officialModelOptions, ...fallbackModelOptions]
        : []

  function selectModelCategory(category: 'official' | 'custom' | 'exact') {
    if (category === 'custom') {
      const next = customModelOptions[0]
      if (next) {
        setManualModelMode(false)
        onChange({ ...draft, model: next.id, api_base: next.api_base ?? draft.api_base })
      }
      return
    }
    if (category === 'official') {
      const next = officialModelOptions[0] ?? fallbackModelOptions[0]
      if (next) {
        setManualModelMode(false)
        onChange({ ...draft, model: next.id, api_key: '', api_base: '' })
      }
      return
    }
    setManualModelMode(true)
    onChange({ ...draft, model: selectedModelMeta?.id ?? draft.model })
  }

  return (
    <form className="settings-panel-form" onSubmit={(event) => event.preventDefault()}>
      <div className="settings-field">
        <span>Model</span>
        <div className="settings-segmented" aria-label="AI source">
          <button
            type="button"
            className={activeModelCategory === 'official' ? 'active' : ''}
            disabled={!officialModelOptions.length && !fallbackModelOptions.length}
            onClick={() => selectModelCategory('official')}
          >
            Official
          </button>
          <button
            type="button"
            className={activeModelCategory === 'custom' ? 'active' : ''}
            disabled={!customModelOptions.length}
            onClick={() => selectModelCategory('custom')}
          >
            Custom
          </button>
          <button
            type="button"
            className={activeModelCategory === 'exact' ? 'active' : ''}
            onClick={() => selectModelCategory('exact')}
          >
            Exact ID
          </button>
        </div>
        <div className="settings-model-row">
          <div className="settings-model-list" role="listbox" aria-label="Model">
            {activeModelCategory === 'exact' ? (
              <span className="settings-empty">Manual model ID</span>
            ) : (
              visibleModelOptions.map((model) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={draft.model === model.id}
                  className={draft.model === model.id ? 'active' : ''}
                  onClick={() => {
                    onChange({
                      ...draft,
                      model: model.id,
                      api_key: model.kind === 'custom' ? draft.api_key : '',
                      api_base: model.kind === 'custom' ? model.api_base ?? draft.api_base : '',
                    })
                    setManualModelMode(false)
                  }}
                  key={model.id}
                >
                  {model.kind === 'custom' ? `${model.label} (${model.provider})` : model.label}
                </button>
              ))
            )}
          </div>
          <input
            type="text"
            value={draft.model}
            placeholder="Exact model id"
            onChange={(event) => {
              setManualModelMode(true)
              onChange({ ...draft, model: event.currentTarget.value })
            }}
          />
        </div>
        {selectedModelMeta?.provider && activeModelCategory !== 'exact' ? (
          <span className="settings-provider-badge">{selectedModelMeta.provider}</span>
        ) : null}
      </div>
      <label className="settings-field">
        <span>API Key</span>
        <input
          type="password"
          value={draft.api_key}
          placeholder="Provider API key"
          onChange={(event) => onChange({ ...draft, api_key: event.currentTarget.value })}
        />
      </label>
      <label className="settings-field">
        <span>API Base URL</span>
        <input
          type="text"
          value={draft.api_base}
          placeholder="Optional endpoint override"
          onChange={(event) => onChange({ ...draft, api_base: event.currentTarget.value })}
        />
      </label>
      <label className="settings-row">
        <span>Max retries</span>
        <input
          type="number"
          min={1}
          max={10}
          value={draft.max_retries}
          onChange={(event) =>
            onChange({
              ...draft,
              max_retries: Number(event.currentTarget.value),
            })
          }
        />
      </label>
      <label className="settings-field">
        <span>System prompt</span>
        <textarea
          value={draft.assistant_settings}
          placeholder="例如：先解释再给最小修改；优先保证可编译；不要大改结构。"
          onChange={(event) => onChange({ ...draft, assistant_settings: event.currentTarget.value })}
        />
      </label>
      <div className="settings-submit-row">
        <button type="button" disabled={testing} onClick={onTestConnection}>
          {testing ? 'Testing...' : 'Test connection'}
        </button>
      </div>
      {testResult ? (
        <p className={`settings-test-result ${testResult.ok ? 'success' : 'error'}`}>
          {testResult.ok
            ? `${testResult.message ?? 'LLM connection OK'}${testResult.duration_ms !== undefined ? ` (${testResult.duration_ms} ms)` : ''}`
            : `LLM settings error: ${testResult.error ?? 'Connection test failed.'}`}
        </p>
      ) : null}
    </form>
  )
}
