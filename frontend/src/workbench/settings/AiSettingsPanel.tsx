import { useEffect, useMemo, useRef, useState } from 'react'
import {
  codexLoginCancel,
  codexLoginDeviceCode,
  codexLoginStart,
  codexLogout,
  codexRestart,
  fetchCodexModels,
  fetchCodexStatus,
} from '../../api/client'
import type {
  CodexDeviceCodeResult,
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
  onModelChange?: (model: string, reasoningEffort?: string) => Promise<void>
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
  // ── D6：Fixed 模式 reasoning effort（draft + 显式 Save，绝不随控件变化隐式写配置）──
  const [effortDraft, setEffortDraft] = useState('')
  const [effortSaving, setEffortSaving] = useState(false)
  const [effortFeedback, setEffortFeedback] = useState<{ ok: boolean; text: string } | null>(null)
  const [pendingEffort, setPendingEffort] = useState('')
  // ── Codex BYOA（D1+D2）：ChatGPT 订阅连接状态 ──
  const [codexStatus, setCodexStatus] = useState<CodexStatus | null>(null)
  const [codexModels, setCodexModels] = useState<CodexModelInfo[]>([])
  const [codexBusy, setCodexBusy] = useState(false)
  const [loginStarted, setLoginStarted] = useState(false)
  const [deviceCode, setDeviceCode] = useState<{ verificationUrl: string; userCode: string } | null>(null)
  const [deviceCodeCopied, setDeviceCodeCopied] = useState(false)
  const [codexError, setCodexError] = useState<string | null>(null)
  const [pendingCodexModel, setPendingCodexModel] = useState<string | null>(null)
  const [codexCancelling, setCodexCancelling] = useState(false)
  const [codexRestarting, setCodexRestarting] = useState(false)
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

  // D6：已保存 effort 是 draft 的事实源；保存/切换模型后回填，
  // 用户编辑未保存时以本地 draft 为准（不隐式写配置）。
  useEffect(() => {
    setEffortDraft(llmSettings.reasoning_effort ?? '')
  }, [llmSettings.reasoning_effort])

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
    setDeviceCode(null)
    try {
      const result = await codexLoginStart()
      if (result.ok) {
        setLoginStarted(true)
        setCodexStatus((prev) => ({ ...(prev ?? emptyCodexStatus()), state: 'login_started', connected: false }))
      } else {
        // D2：browser flow 失败 → 明确提示改用设备码（不静默切换）
        setCodexError(result.error ?? t('settings.ai.codex.loginFailed'))
      }
    } catch (error) {
      setCodexError(error instanceof Error ? error.message : t('settings.ai.codex.loginFailed'))
    } finally {
      setCodexBusy(false)
    }
  }

  // D2：设备码登录——用户显式选择，绝不静默 fallback
  async function handleCodexDeviceCode() {
    setCodexBusy(true)
    setCodexError(null)
    setDeviceCodeCopied(false)
    try {
      const result: CodexDeviceCodeResult = await codexLoginDeviceCode()
      if (result.ok && result.verification_url && result.user_code) {
        setLoginStarted(true)
        setDeviceCode({ verificationUrl: result.verification_url, userCode: result.user_code })
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

  // D2：取消进行中的登录（浏览器或设备码）
  async function handleCodexCancelLogin() {
    setCodexCancelling(true)
    setCodexError(null)
    try {
      const result = await codexLoginCancel()
      if (result.ok) {
        setLoginStarted(false)
        setDeviceCode(null)
        setDeviceCodeCopied(false)
        setCodexStatus({ ...emptyCodexStatus(), state: 'signed_out' })
      } else {
        setCodexError(result.error ?? t('settings.ai.codex.logoutFailed'))
      }
    } catch (error) {
      setCodexError(error instanceof Error ? error.message : t('settings.ai.codex.logoutFailed'))
    } finally {
      setCodexCancelling(false)
    }
  }

  // D2：app-server 崩溃后显式重启
  async function handleCodexRestart() {
    setCodexRestarting(true)
    setCodexError(null)
    try {
      const result = await codexRestart()
      if (result.ok) {
        const status: CodexStatus = {
          ...emptyCodexStatus(),
          state: result.state ?? 'signed_out',
          restartable: false,
          error: result.error,
        }
        setCodexStatus(status)
        if (status.connected) {
          const models = await fetchCodexModels()
          setCodexModels(models.ok ? (models.models ?? []) : [])
          if (!models.ok) setCodexError(models.error ?? null)
        }
      } else {
        setCodexError(result.error ?? t('settings.ai.codex.restartFailed'))
      }
    } catch (error) {
      setCodexError(error instanceof Error ? error.message : t('settings.ai.codex.restartFailed'))
    } finally {
      setCodexRestarting(false)
    }
  }

  async function copyDeviceCode() {
    const code = deviceCode?.userCode
    if (!code) return
    try {
      await navigator.clipboard.writeText(code)
      setDeviceCodeCopied(true)
    } catch {
      const textarea = document.createElement('textarea')
      textarea.value = code
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(textarea)
      setDeviceCodeCopied(ok)
    }
  }

  async function handleCodexLogout() {
    setCodexBusy(true)
    setCodexError(null)
    try {
      const result = await codexLogout()
      if (result.ok) {
        setLoginStarted(false)
        setDeviceCode(null)
        setDeviceCodeCopied(false)
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
    // D6：新模型的 effort 由用户显式选择（draft）；不静默继承旧模型 effort
    setPendingEffort('')
    setSwitchError(null)
  }

  async function confirmCodexModelSwitch() {
    const model = pendingCodexModel
    if (!onModelChange || !model || switching) return
    setSwitching(true)
    setSwitchError(null)
    try {
      // D6：model + effort 一起显式保存；后端按 model/list 校验组合
      // （不支持的 effort 拒绝保存，不会静默替换）。无 effort 时保持
      // 既有调用形状（只传 model），兼容非 codex 模型切换。
      if (pendingEffort) {
        await onModelChange(model, pendingEffort)
      } else {
        await onModelChange(model)
      }
      setPendingCodexModel(null)
      setPendingEffort('')
    } catch (error) {
      setSwitchError(error instanceof Error ? error.message : t('settings.ai.switchFailed'))
    } finally {
      setSwitching(false)
    }
  }

  // D6：当前模型 effort 的显式保存（draft → Save；成功后回填事实源）
  async function saveCodexEffort() {
    if (!onModelChange || effortSaving) return
    setEffortSaving(true)
    setEffortFeedback(null)
    try {
      // 显式 Save：effort 为空 = 清除覆盖（回到模型默认）
      if (effortDraft) {
        await onModelChange(llmSettings.model, effortDraft)
      } else {
        await onModelChange(llmSettings.model)
      }
      setEffortFeedback({ ok: true, text: t('settings.ai.codex.effortSaved') })
    } catch (error) {
      setEffortFeedback({
        ok: false,
        text: error instanceof Error ? error.message : t('settings.ai.codex.effortSaveFailed'),
      })
    } finally {
      setEffortSaving(false)
    }
  }

  // D6：当前/待选模型的支持 effort 目录（只来自 model/list，不硬编码）
  const codexCatalog = useMemo(() => new Map(codexModels.map((m) => [m.id, m])), [codexModels])
  const currentEffortOptions = codexCatalog.get(currentId)?.supported_reasoning_efforts ?? []
  const pendingEffortOptions = pendingCodexModel
    ? (codexCatalog.get(pendingCodexModel)?.supported_reasoning_efforts ?? [])
    : []

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
        deviceCode={deviceCode}
        deviceCodeCopied={deviceCodeCopied}
        cancelling={codexCancelling}
        restarting={codexRestarting}
        error={codexError}
        current={currentId}
        pending={pendingCodexModel}
        switching={switching}
        onLogin={() => void handleCodexLogin()}
        onDeviceCode={() => void handleCodexDeviceCode()}
        onCancelLogin={() => void handleCodexCancelLogin()}
        onRestart={() => void handleCodexRestart()}
        onCopyDeviceCode={() => void copyDeviceCode()}
        onLogout={() => void handleCodexLogout()}
        onSelect={requestCodexModelSwitch}
        onConfirm={() => void confirmCodexModelSwitch()}
        onCancel={() => {
          setPendingCodexModel(null)
          setPendingEffort('')
        }}
        // D6：Fixed 模式 effort（draft + 显式 Save）
        savedEffort={llmSettings.reasoning_effort ?? ''}
        effortDraft={effortDraft}
        effortOptions={currentEffortOptions}
        effortSaving={effortSaving}
        effortFeedback={effortFeedback}
        onEffortDraftChange={(v) => {
          setEffortDraft(v)
          setEffortFeedback(null)
        }}
        onSaveEffort={() => void saveCodexEffort()}
        pendingEffort={pendingEffort}
        pendingEffortOptions={pendingEffortOptions}
        onPendingEffortChange={(v) => {
          setPendingEffort(v)
          setSwitchError(null)
        }}
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
  deviceCode,
  deviceCodeCopied,
  cancelling,
  restarting,
  error,
  current,
  pending,
  switching,
  savedEffort,
  effortDraft,
  effortOptions,
  effortSaving,
  effortFeedback,
  pendingEffort,
  pendingEffortOptions,
  onLogin,
  onDeviceCode,
  onCancelLogin,
  onRestart,
  onCopyDeviceCode,
  onLogout,
  onSelect,
  onConfirm,
  onCancel,
  onEffortDraftChange,
  onSaveEffort,
  onPendingEffortChange,
}: {
  status: CodexStatus | null
  models: CodexModelInfo[]
  busy: boolean
  loginStarted: boolean
  deviceCode: { verificationUrl: string; userCode: string } | null
  deviceCodeCopied: boolean
  cancelling: boolean
  restarting: boolean
  error: string | null
  current: string
  pending: string | null
  switching: boolean
  // D6：Fixed 模式 effort（draft + 显式 Save）
  savedEffort: string
  effortDraft: string
  effortOptions: { effort: string; description?: string }[]
  effortSaving: boolean
  effortFeedback: { ok: boolean; text: string } | null
  pendingEffort: string
  pendingEffortOptions: { effort: string; description?: string }[]
  onLogin: () => void
  onDeviceCode: () => void
  onCancelLogin: () => void
  onRestart: () => void
  onCopyDeviceCode: () => void
  onLogout: () => void
  onSelect: (model: string) => void
  onConfirm: () => void
  onCancel: () => void
  onEffortDraftChange: (value: string) => void
  onSaveEffort: () => void
  onPendingEffortChange: (value: string) => void
}) {
  const t = useT()
  const state = status?.state ?? 'signed_out'
  const connected = status?.connected === true
  const rateLimits = status?.rate_limits

  return (
    <div className="settings-codex-section" data-testid="codex-section">
      <div className="settings-row-header">{t('settings.ai.codex.sectionTitle')}</div>
      <p className="settings-hint" data-testid="codex-modify-note">
        {t('settings.ai.codex.modifyNotOpen')}
      </p>
      {state === 'no_cli' ? (
        <p className="settings-test-result error" data-testid="codex-no-cli">
          {t('settings.ai.codex.noCli')}
        </p>
      ) : null}
      {state === 'version_incompatible' ? (
        <p className="settings-test-result error" data-testid="codex-version-incompatible">
          {t('settings.ai.codex.versionIncompatible')}
        </p>
      ) : null}
      {state === 'crashed' ? (
        <div data-testid="codex-crashed">
          <p className="settings-test-result error">{t('settings.ai.codex.crashed')}</p>
          <div className="settings-row">
            <button
              type="button"
              disabled={restarting}
              onClick={onRestart}
              data-testid="codex-restart-button"
            >
              {restarting ? t('settings.ai.codex.restarting') : t('settings.ai.codex.restart')}
            </button>
          </div>
        </div>
      ) : null}
      {state === 'error' ? (
        <p className="settings-test-result error" data-testid="codex-error">
          {error || t('settings.ai.codex.errorUnknown')}
        </p>
      ) : null}
      {state === 'quota_exhausted' && connected ? (
        <p className="settings-test-result error" data-testid="codex-quota-exhausted">
          {t('settings.ai.codex.quotaExhausted')}
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
      {rateLimits && connected ? (
        <div className="settings-row" data-testid="codex-rate-limits">
          <span>{t('settings.ai.codex.rateLimits')}</span>
          <code className="settings-model-display valid">
            {rateLimits.plan_type ? `${t('settings.ai.codex.rateLimitsPlan')}: ${rateLimits.plan_type} · ` : ''}
            {rateLimits.used_percent !== undefined && rateLimits.used_percent !== null
              ? `${t('settings.ai.codex.rateLimitsUsed')}: ${rateLimits.used_percent}%`
              : rateLimits.credits?.unlimited
                ? t('settings.ai.codex.rateLimitsUnlimited')
                : rateLimits.credits?.has_credits
                  ? t('settings.ai.codex.rateLimitsHasCredits')
                  : ''}
            {rateLimits.reached ? ` · ${t('settings.ai.codex.rateLimitsReached')}` : ''}
          </code>
        </div>
      ) : null}
      {!connected && state !== 'no_cli' && state !== 'error' && state !== 'version_incompatible' && state !== 'crashed' ? (
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
          {!loginStarted ? (
            <button
              type="button"
              disabled={busy}
              onClick={onDeviceCode}
              data-testid="codex-device-code-button"
            >
              {t('settings.ai.codex.deviceCode')}
            </button>
          ) : null}
        </div>
      ) : null}
      {status?.login_error && !loginStarted ? (
        <p className="settings-test-result error" data-testid="codex-login-error">
          {status.login_error}
        </p>
      ) : null}
      {loginStarted ? (
        <div data-testid="codex-login-pending">
          <p className="settings-test-result">{t('settings.ai.codex.loginPendingHint')}</p>
          {deviceCode ? (
            <div className="settings-codex-device-code" data-testid="codex-device-code">
              <p className="settings-test-result">{t('settings.ai.codex.deviceCodeHint')}</p>
              <div className="settings-row">
                <span>{t('settings.ai.codex.deviceCodeUrl')}</span>
                <code className="settings-model-display">{deviceCode.verificationUrl}</code>
              </div>
              <div className="settings-row">
                <span>{t('settings.ai.codex.deviceCodeValue')}</span>
                <code className="settings-model-display valid" data-testid="codex-device-code-value">
                  {deviceCode.userCode}
                </code>
                <button type="button" onClick={onCopyDeviceCode}>
                  {deviceCodeCopied ? t('settings.ai.codex.deviceCodeCopied') : t('settings.ai.codex.copyDeviceCode')}
                </button>
              </div>
            </div>
          ) : null}
          <div className="settings-row">
            <button
              type="button"
              disabled={cancelling}
              onClick={onCancelLogin}
              data-testid="codex-login-cancel"
            >
              {cancelling ? '…' : t('settings.ai.codex.cancelLogin')}
            </button>
          </div>
        </div>
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
          {current.startsWith('openai-codex/') && !pending ? (
            <div className="settings-row" data-testid="codex-effort-row">
              <span>{t('settings.ai.codex.effortLabel')}</span>
              <select
                aria-label={t('settings.ai.codex.effortLabel')}
                value={effortDraft}
                disabled={effortSaving}
                onChange={(event) => onEffortDraftChange(event.target.value)}
              >
                <option value="">{t('settings.ai.codex.effortDefault')}</option>
                {effortOptions.map((opt) => (
                  <option key={opt.effort} value={opt.effort}>
                    {opt.description ? `${opt.effort} — ${opt.description}` : opt.effort}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={effortSaving || effortDraft === savedEffort}
                onClick={onSaveEffort}
                data-testid="codex-effort-save"
              >
                {effortSaving ? '…' : t('settings.ai.codex.effortSave')}
              </button>
            </div>
          ) : null}
          {effortFeedback ? (
            <p
              className={`settings-test-result ${effortFeedback.ok ? 'success' : 'error'}`}
              data-testid="codex-effort-feedback"
            >
              {effortFeedback.text}
            </p>
          ) : null}
          {pending ? (
            <div className="settings-model-confirm" data-testid="codex-model-confirm">
              <span>{t('settings.ai.confirmSwitch', { model: pending })}</span>
              {pendingEffortOptions.length > 0 ? (
                <div className="settings-row">
                  <span>{t('settings.ai.codex.effortLabel')}</span>
                  <select
                    aria-label={t('settings.ai.codex.effortLabel')}
                    value={pendingEffort}
                    disabled={switching}
                    onChange={(event) => onPendingEffortChange(event.target.value)}
                  >
                    <option value="">{t('settings.ai.codex.effortDefault')}</option>
                    {pendingEffortOptions.map((opt) => (
                      <option key={opt.effort} value={opt.effort}>
                        {opt.description ? `${opt.effort} — ${opt.description}` : opt.effort}
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}
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
