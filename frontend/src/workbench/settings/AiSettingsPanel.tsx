import { useEffect, useMemo, useState } from 'react'
import type { LlmConnectionTestResult, LlmModelOption, LlmSettings } from '../../api/types'
import { useT } from '../../i18n'

interface AiSettingsPanelProps {
  llmSettings: LlmSettings
  onOpenConfig: () => void
  onTestConnection: () => Promise<LlmConnectionTestResult>
  onModelChange?: (model: string) => Promise<void>
  onSaveApiKey?: (model: string, apiKey: string) => Promise<unknown>
}

export function AiSettingsPanel({ llmSettings, onOpenConfig, onTestConnection, onModelChange, onSaveApiKey }: AiSettingsPanelProps) {
  const t = useT()
  const [testResult, setTestResult] = useState<LlmConnectionTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [modelQuery, setModelQuery] = useState('')
  const [switching, setSwitching] = useState(false)
  const [pendingModel, setPendingModel] = useState<string | null>(null)
  const [switchError, setSwitchError] = useState<string | null>(null)
  const [errorCopied, setErrorCopied] = useState(false)
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [savingKey, setSavingKey] = useState(false)
  const [keyFeedback, setKeyFeedback] = useState<{ ok: boolean; text: string } | null>(null)

  const groups = llmSettings.model_groups
  const customModels = groups?.custom ?? []
  const officialModels = groups?.official ?? []
  const modelAvailable = llmSettings.model_available ?? true

  // 官方模型（内置列表，只需在界面填 API Key）与自定义代理（在 config.toml 里维护）
  // 是两套配置体系：只有官方模型在界面提供 Key 输入，自定义模型的 Key 走可编辑文件。
  const currentOption = [...customModels, ...officialModels].find(
    (m) => m.id === llmSettings.model || m.target_model === llmSettings.model,
  )
  // config.toml 里的 model 可能写 alias 或 custom provider 的真实模型名，
  // 列表高亮必须与 currentOption 用同一套解析，否则 target_model 命中时列表不亮。
  const currentId = currentOption?.id ?? llmSettings.model
  const isCustomModel = currentOption?.kind === 'custom'
  const showKeyEditor = Boolean(onSaveApiKey) && !isCustomModel && Boolean(llmSettings.model)

  useEffect(() => {
    setApiKeyInput('')
    setKeyFeedback(null)
  }, [llmSettings.model])

  const filteredCustom = useFilteredModels(customModels, modelQuery)
  const filteredOfficial = useFilteredModels(officialModels, modelQuery)
  const hasModels = customModels.length > 0 || officialModels.length > 0

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    setErrorCopied(false)
    const result = await onTestConnection()
    setTestResult(result)
    setTesting(false)
  }

  async function copyTestError() {
    const text = testErrorText(testResult)
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setErrorCopied(true)
    } catch {
      // 剪贴板 API 不可用时回退到隐藏 textarea + execCommand
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(textarea)
      setErrorCopied(ok)
    }
  }

  function requestModelSwitch(model: string) {
    if (!onModelChange || model === currentId || switching) return
    setSwitchError(null)
    setPendingModel(model)
  }

  function cancelModelSwitch() {
    setPendingModel(null)
    setSwitchError(null)
  }

  async function confirmModelSwitch() {
    const model = pendingModel
    if (!onModelChange || !model || switching) return
    setSwitching(true)
    setSwitchError(null)
    setTestResult(null)
    setErrorCopied(false)
    try {
      await onModelChange(model)
      setPendingModel(null)
      // 切换成功后立即验证连通性，失败原因原文显示在下方结果区
      setTesting(true)
      try {
        setTestResult(await onTestConnection())
      } finally {
        setTesting(false)
      }
    } catch (error) {
      setSwitchError(error instanceof Error ? error.message : t('settings.ai.switchFailed'))
    } finally {
      setSwitching(false)
    }
  }

  async function saveApiKey() {
    const apiKey = apiKeyInput.trim()
    if (!onSaveApiKey || !apiKey || savingKey) return
    setSavingKey(true)
    setKeyFeedback(null)
    try {
      await onSaveApiKey(llmSettings.model, apiKey)
      setApiKeyInput('')
      // 保存后自动验证连接。key 始终保留——验证结果只是提示，
      // 用户可能在离线环境配置，不能因为验证失败拒绝保存。
      setKeyFeedback({ ok: true, text: t('settings.ai.savingAndVerifying') })
      setTesting(true)
      setTestResult(null)
      setErrorCopied(false)
      try {
        const result = await onTestConnection()
        setTestResult(result)
        if (result.ok) {
          setKeyFeedback({
            ok: true,
            text: t('settings.ai.keySavedConnectionOk', { ms: result.duration_ms ?? '—' }),
          })
        } else {
          setKeyFeedback({ ok: false, text: t('settings.ai.keySavedConnectionFailed') })
        }
      } finally {
        setTesting(false)
      }
    } catch (error) {
      setKeyFeedback({
        ok: false,
        text: error instanceof Error ? error.message : t('settings.ai.keySaveFailed'),
      })
    } finally {
      setSavingKey(false)
    }
  }

  return (
    <div className="settings-panel-form">
      <div className="settings-row">
        <span>{t('settings.ai.modelLabel')}</span>
        <code className={`settings-model-display ${modelAvailable ? 'valid' : 'invalid'}`}>
          {llmSettings.model || '—'}
        </code>
      </div>
      {!modelAvailable ? (
        <p className="settings-test-result error">{t('settings.ai.modelUnavailable')}</p>
      ) : null}
      {showKeyEditor ? (
        <div className="settings-apikey-row">
          <input
            type="password"
            aria-label={t('settings.ai.apiKeyLabel')}
            placeholder={modelAvailable ? t('settings.ai.apiKeyReplacePlaceholder') : t('settings.ai.apiKeyPlaceholder')}
            value={apiKeyInput}
            onChange={(event) => {
              setApiKeyInput(event.target.value)
              setKeyFeedback(null)
            }}
          />
          <button type="button" disabled={!apiKeyInput.trim() || savingKey} onClick={() => void saveApiKey()}>
            {savingKey ? t('settings.ai.savingAndVerifying') : t('settings.ai.saveKey')}
          </button>
        </div>
      ) : null}
      {keyFeedback ? (
        <p className={`settings-test-result ${keyFeedback.ok ? 'success' : 'error'}`}>{keyFeedback.text}</p>
      ) : null}
      {hasModels && onModelChange ? (
        <div className="settings-model-row">
          <input
            type="text"
            placeholder={t('settings.ai.searchPlaceholder')}
            value={modelQuery}
            onChange={(event) => setModelQuery(event.target.value)}
          />
        </div>
      ) : null}
      {hasModels && onModelChange && pendingModel ? (
        <div className="settings-model-confirm" data-testid="model-switch-confirm">
          <span>{t('settings.ai.confirmSwitch', { model: pendingModel })}</span>
          <div className="settings-model-confirm-actions">
            <button type="button" disabled={switching} onClick={() => void confirmModelSwitch()}>
              {switching ? '…' : t('settings.ai.confirmYes')}
            </button>
            <button type="button" disabled={switching} onClick={cancelModelSwitch}>
              {t('settings.ai.confirmNo')}
            </button>
          </div>
        </div>
      ) : null}
      {switchError ? <p className="settings-test-result error">{switchError}</p> : null}
      {hasModels && onModelChange ? (
        <>
          {filteredCustom.length > 0 ? (
            <ModelGroup
              label={t('settings.ai.groupCustom')}
              models={filteredCustom}
              current={currentId}
              pending={pendingModel}
              disabled={switching}
              onSelect={requestModelSwitch}
            />
          ) : null}
          {filteredOfficial.length > 0 ? (
            <ModelGroup
              label={t('settings.ai.groupOfficial')}
              models={filteredOfficial}
              current={currentId}
              pending={pendingModel}
              disabled={switching}
              onSelect={requestModelSwitch}
            />
          ) : null}
          {filteredCustom.length === 0 && filteredOfficial.length === 0 ? (
            <p className="settings-test-result">{t('settings.ai.noMatch')}</p>
          ) : null}
        </>
      ) : null}
      <div className="settings-submit-row">
        <button type="button" className="settings-open-config-btn" onClick={onOpenConfig}>
          Edit config.toml ↗
        </button>
        <button type="button" disabled={testing} onClick={() => void handleTest()}>
          {testing ? 'Testing…' : 'Test connection'}
        </button>
      </div>
      <p className="settings-test-result">{t('settings.ai.officialBaseNote')}</p>
      {testResult ? (
        testResult.ok ? (
          <p className="settings-test-result success">
            {`${testResult.message ?? 'LLM connection OK'}${testResult.duration_ms !== undefined ? ` (${testResult.duration_ms} ms)` : ''}`}
          </p>
        ) : (
          <div className="settings-test-error-block" data-testid="llm-test-error">
            <pre className="settings-test-detail">{testErrorText(testResult)}</pre>
            <button type="button" className="settings-copy-error-btn" onClick={() => void copyTestError()}>
              {errorCopied ? t('settings.ai.copied') : t('settings.ai.copyError')}
            </button>
          </div>
        )
      ) : null}
    </div>
  )
}

function testErrorText(result: LlmConnectionTestResult | null) {
  if (!result || result.ok) return ''
  // detail 是服务器返回的全量原文（异常链+响应体），优先展示；无 detail 时退回摘要
  return result.detail || result.error || 'Connection test failed.'
}

function useFilteredModels(models: LlmModelOption[], query: string) {
  return useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return models
    return models.filter(
      (m) => m.label.toLowerCase().includes(q) || m.provider.toLowerCase().includes(q),
    )
  }, [models, query])
}

function ModelGroup({
  label,
  models,
  current,
  pending,
  disabled,
  onSelect,
}: {
  label: string
  models: LlmModelOption[]
  current: string
  pending: string | null
  disabled: boolean
  onSelect: (model: string) => void
}) {
  return (
    <>
      <div className="settings-row-header">{label}</div>
      <div className="settings-model-list">
        {models.map((m) => (
          <button
            key={m.id}
            type="button"
            className={m.id === current ? 'active' : m.id === pending ? 'pending' : ''}
            disabled={disabled}
            title={`${m.label} (${m.provider})`}
            onClick={() => onSelect(m.id)}
          >
            {m.label}
          </button>
        ))}
      </div>
    </>
  )
}
