import { useEffect, useMemo, useRef, useState } from 'react'
import { codexLoginStart, codexLogout, fetchCodexModels, fetchCodexStatus } from '../../api/client'
import type {
  CodexModelInfo,
  CodexStatus,
  LlmConnectionTestResult,
  LlmModelOption,
  LlmSettings,
} from '../../api/types'
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
  // ── Codex BYOA（D1）：ChatGPT 订阅连接状态 ──
  const [codexStatus, setCodexStatus] = useState<CodexStatus | null>(null)
  const [codexModels, setCodexModels] = useState<CodexModelInfo[]>([])
  const [codexBusy, setCodexBusy] = useState(false)
  const [loginStarted, setLoginStarted] = useState(false)
  const [codexError, setCodexError] = useState<string | null>(null)
  const [pendingCodexModel, setPendingCodexModel] = useState<string | null>(null)
  const loginPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const groups = llmSettings.model_groups
  const customModels = groups?.custom ?? []
  const officialModels = groups?.official ?? []
  const modelAvailable = llmSettings.model_available ?? true
  const isCodexModel = llmSettings.model.startsWith('openai-codex/')
  // 当前模型是 Codex 订阅模型时，可用性以后端为准：已登录 且 模型在当前
  // 账户 model/list 目录中（后端 status 的 model_available 已含目录校验，P0-4）
  const effectiveModelAvailable = isCodexModel
    ? codexStatus?.model_available === true
    : modelAvailable

  // 统一 provider 注册表后，官方模型与自定义 provider 的 Key 都可以在界面保存
  // （写入 config.toml 的 provider_keys / [[llm.providers]]）；ollama 本地模型无需 Key。
  const currentOption = [...customModels, ...officialModels].find(
    (m) => m.id === llmSettings.model || m.target_model === llmSettings.model,
  )
  // config.toml 里的 model 可能写 alias 或 custom provider 的真实模型名，
  // 列表高亮必须与 currentOption 用同一套解析，否则 target_model 命中时列表不亮。
  const currentId = currentOption?.id ?? llmSettings.model
  const isOllamaModel = currentOption?.provider === 'ollama' || llmSettings.model.startsWith('ollama/')
  // Codex 订阅模型没有 API Key 可填（凭据在 app-server 的独立 CODEX_HOME 里）
  const showKeyEditor = Boolean(onSaveApiKey) && !isOllamaModel && !isCodexModel && Boolean(llmSettings.model)

  useEffect(() => {
    setApiKeyInput('')
    setKeyFeedback(null)
  }, [llmSettings.model])

  // ── Codex：挂载时加载状态；登录后加载动态模型目录 ──
  useEffect(() => {
    let cancelled = false
    async function loadCodex() {
      setCodexBusy(true)
      const status = await fetchCodexStatus()
      if (cancelled) return
      setCodexStatus(status)
      setCodexError(status.ok ? null : status.error ?? null)
      if (status.connected) {
        const models = await fetchCodexModels()
        if (!cancelled) {
          setCodexModels(models.ok ? (models.models ?? []) : [])
          if (!models.ok) setCodexError(models.error ?? null)
        }
      }
      setCodexBusy(false)
    }
    void loadCodex()
    return () => {
      cancelled = true
    }
  }, [])

  // 登录中轮询状态（最多 3 分钟），完成后自动拉模型目录
  useEffect(() => {
    if (!loginStarted) return
    const startedAt = Date.now()
    loginPollRef.current = setInterval(() => {
      void (async () => {
        const status = await fetchCodexStatus()
        setCodexStatus(status)
        if (status.connected || status.state === 'error' || Date.now() - startedAt > 180_000) {
          if (loginPollRef.current) clearInterval(loginPollRef.current)
          loginPollRef.current = null
          setLoginStarted(false)
          if (status.connected) {
            const models = await fetchCodexModels()
            setCodexModels(models.ok ? (models.models ?? []) : [])
            if (!models.ok) setCodexError(models.error ?? null)
          }
        }
      })()
    }, 2000)
    return () => {
      if (loginPollRef.current) clearInterval(loginPollRef.current)
      loginPollRef.current = null
    }
  }, [loginStarted])

  async function handleCodexLogin() {
    setCodexBusy(true)
    setCodexError(null)
    try {
      const result = await codexLoginStart()
      if (result.ok) {
        setLoginStarted(true)
        setCodexStatus((prev) => ({ ...(prev ?? emptyCodexStatus()), state: 'login_started', connected: false }))
      } else {
        setCodexError(result.error ?? t('settings.ai.codex.loginFailed'))
      }
    } catch (error) {
      setCodexError(error instanceof Error ? error.message : t('settings.ai.codex.loginFailed'))
    } finally {
      setCodexBusy(false)
    }
  }

  async function handleCodexLogout() {
    setCodexBusy(true)
    setCodexError(null)
    try {
      const result = await codexLogout()
      if (result.ok) {
        setLoginStarted(false)
        setCodexModels([])
        setPendingCodexModel(null)
        setCodexStatus({ ...emptyCodexStatus(), state: 'signed_out' })
      } else {
        setCodexError(result.error ?? t('settings.ai.codex.logoutFailed'))
      }
    } catch (error) {
      setCodexError(error instanceof Error ? error.message : t('settings.ai.codex.logoutFailed'))
    } finally {
      setCodexBusy(false)
    }
  }

  function requestCodexModelSwitch(model: string) {
    if (!onModelChange || model === currentId || switching) return
    setPendingCodexModel(model)
  }

  async function confirmCodexModelSwitch() {
    const model = pendingCodexModel
    if (!onModelChange || !model || switching) return
    setSwitching(true)
    setSwitchError(null)
    try {
      await onModelChange(model)
      setPendingCodexModel(null)
    } catch (error) {
      setSwitchError(error instanceof Error ? error.message : t('settings.ai.switchFailed'))
    } finally {
      setSwitching(false)
    }
  }

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
        <code className={`settings-model-display ${effectiveModelAvailable ? 'valid' : 'invalid'}`}>
          {llmSettings.model || '—'}
        </code>
      </div>
      {!effectiveModelAvailable ? (
        <p className="settings-test-result error">
          {isCodexModel ? t('settings.ai.codex.modelUnavailable') : t('settings.ai.modelUnavailable')}
        </p>
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
      <CodexSection
        status={codexStatus}
        models={codexModels}
        busy={codexBusy}
        loginStarted={loginStarted}
        error={codexError}
        current={currentId}
        pending={pendingCodexModel}
        switching={switching}
        onLogin={() => void handleCodexLogin()}
        onLogout={() => void handleCodexLogout()}
        onSelect={requestCodexModelSwitch}
        onConfirm={() => void confirmCodexModelSwitch()}
        onCancel={() => setPendingCodexModel(null)}
      />
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

function emptyCodexStatus(): CodexStatus {
  return { state: 'signed_out', codex_available: true, connected: false, account: null }
}

function CodexSection({
  status,
  models,
  busy,
  loginStarted,
  error,
  current,
  pending,
  switching,
  onLogin,
  onLogout,
  onSelect,
  onConfirm,
  onCancel,
}: {
  status: CodexStatus | null
  models: CodexModelInfo[]
  busy: boolean
  loginStarted: boolean
  error: string | null
  current: string
  pending: string | null
  switching: boolean
  onLogin: () => void
  onLogout: () => void
  onSelect: (model: string) => void
  onConfirm: () => void
  onCancel: () => void
}) {
  const t = useT()
  const state = status?.state ?? 'signed_out'
  const connected = status?.connected === true

  return (
    <div className="settings-codex-section" data-testid="codex-section">
      <div className="settings-row-header">{t('settings.ai.codex.sectionTitle')}</div>
      {state === 'no_cli' ? (
        <p className="settings-test-result error" data-testid="codex-no-cli">
          {t('settings.ai.codex.noCli')}
        </p>
      ) : null}
      {state === 'error' ? (
        <p className="settings-test-result error" data-testid="codex-error">
          {error || t('settings.ai.codex.errorUnknown')}
        </p>
      ) : null}
      {connected && status?.account ? (
        <div className="settings-row" data-testid="codex-account">
          <span>{t('settings.ai.codex.connectedLabel')}</span>
          <code className="settings-model-display valid">
            {status.account.email_masked ?? '—'}
            {status.account.plan_type ? ` · ${status.account.plan_type}` : ''}
          </code>
          <button type="button" disabled={busy} onClick={onLogout}>
            {t('settings.ai.codex.logout')}
          </button>
        </div>
      ) : null}
      {!connected && state !== 'no_cli' && state !== 'error' ? (
        <div className="settings-row" data-testid="codex-login-row">
          <span>{t('settings.ai.codex.notConnectedLabel')}</span>
          <button
            type="button"
            disabled={busy || loginStarted}
            onClick={onLogin}
            data-testid="codex-login-button"
          >
            {loginStarted ? t('settings.ai.codex.loginPending') : t('settings.ai.codex.login')}
          </button>
        </div>
      ) : null}
      {loginStarted ? (
        <p className="settings-test-result" data-testid="codex-login-pending">
          {t('settings.ai.codex.loginPendingHint')}
        </p>
      ) : null}
      {error ? <p className="settings-test-result error">{error}</p> : null}
      {connected ? (
        <>
          <div className="settings-row-header">{t('settings.ai.codex.modelsLabel')}</div>
          {models.length === 0 ? (
            <p className="settings-test-result">{t('settings.ai.codex.noModels')}</p>
          ) : (
            <div className="settings-model-list">
              {models.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className={m.id === current ? 'active' : m.id === pending ? 'pending' : ''}
                  disabled={switching}
                  title={`${m.label} (${m.model})`}
                  onClick={() => onSelect(m.id)}
                >
                  {m.label}
                </button>
              ))}
            </div>
          )}
          {pending ? (
            <div className="settings-model-confirm" data-testid="codex-model-confirm">
              <span>{t('settings.ai.confirmSwitch', { model: pending })}</span>
              <div className="settings-model-confirm-actions">
                <button type="button" disabled={switching} onClick={onConfirm}>
                  {switching ? '…' : t('settings.ai.confirmYes')}
                </button>
                <button type="button" disabled={switching} onClick={onCancel}>
                  {t('settings.ai.confirmNo')}
                </button>
              </div>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  )
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
