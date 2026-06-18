import { useState } from 'react'
import type { LlmConnectionTestResult, LlmSettings } from '../../api/types'

interface AiSettingsPanelProps {
  llmSettings: LlmSettings
  onOpenConfig: () => void
  onTestConnection: () => Promise<LlmConnectionTestResult>
}

export function AiSettingsPanel({ llmSettings, onOpenConfig, onTestConnection }: AiSettingsPanelProps) {
  const [testResult, setTestResult] = useState<LlmConnectionTestResult | null>(null)
  const [testing, setTesting] = useState(false)

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    const result = await onTestConnection()
    setTestResult(result)
    setTesting(false)
  }

  return (
    <div className="settings-panel-form">
      <div className="settings-row">
        <span>Model</span>
        <code className="settings-model-display">{llmSettings.model || '—'}</code>
      </div>
      <div className="settings-submit-row">
        <button type="button" className="settings-open-config-btn" onClick={onOpenConfig}>
          Edit config.toml ↗
        </button>
        <button type="button" disabled={testing} onClick={() => void handleTest()}>
          {testing ? 'Testing…' : 'Test connection'}
        </button>
      </div>
      {testResult ? (
        <p className={`settings-test-result ${testResult.ok ? 'success' : 'error'}`}>
          {testResult.ok
            ? `${testResult.message ?? 'LLM connection OK'}${testResult.duration_ms !== undefined ? ` (${testResult.duration_ms} ms)` : ''}`
            : `${testResult.error ?? 'Connection test failed.'}`}
        </p>
      ) : null}
    </div>
  )
}
