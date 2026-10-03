import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  LlmConnectionTestResult,
  LlmDiscoveryRequest,
  LlmDiscoveryResult,
  LlmProviderActivity,
  LlmProviderDraft,
  LlmProviderInfo,
  LlmProviderTemplate,
  LlmProviderWriteResult,
} from '../../api/types'
import { useT } from '../../i18n'
import {
  PROVIDER_API_MODES,
  addModelEntry,
  credentialBadgeKey,
  draftTestModel,
  emptyProviderForm,
  formatActivityTime,
  formFromProvider,
  formFromTemplate,
  formToDraft,
  isProviderFormDirty,
  providerForModel,
  removeModelEntry,
  type ProviderFormState,
} from './providerForm'

// 卡06：服务商管理面板（模板网格 + 草稿表单 + 手输模型 + 草稿测试 + 列表）。
// 合同：全程 draft + 显式 Save；key 输入 write-only 不回显；config_modified
// 冲突时保留本地草稿并提示刷新；发现失败不影响手输（发现按钮属卡09）。

export interface ProviderManagerPanelProps {
  providers: LlmProviderInfo[]
  providerTemplates: LlmProviderTemplate[]
  loaded: boolean
  conflict: string | null
  onLoadProviders: () => Promise<void>
  onCreateProvider: (draft: LlmProviderDraft) => Promise<LlmProviderWriteResult>
  onUpdateProvider: (name: string, draft: LlmProviderDraft) => Promise<LlmProviderWriteResult>
  onDeleteProvider: (name: string) => Promise<LlmProviderWriteResult>
  onTestDraft: (draft: LlmProviderDraft, model: string) => Promise<LlmConnectionTestResult>
  /** 卡09：模型发现（无锁路由；结果由组件按 epoch 守卫后显式勾选合并） */
  onDiscoverModels: (request: LlmDiscoveryRequest) => Promise<LlmDiscoveryResult>
  /** 卡11：discovered/tested 会话级时间戳（configured 来自 provider.credential） */
  activity?: Record<string, LlmProviderActivity>
  /** 卡11：当前生效模型（高亮所属 provider 行） */
  currentModel?: string | null
}

interface DiscoveryState {
  epoch: number
  models: string[]
  rawCount: number
  truncated: boolean
  pageCount: number
}

interface EditorState {
  mode: 'create' | 'edit'
  /** edit 模式下定位既有条目的名字（创建后只读） */
  originalName: string
  form: ProviderFormState
  /** 打开时的表单快照（dirty 判定基准） */
  initial: ProviderFormState
}

export function ProviderManagerPanel({
  providers,
  providerTemplates,
  loaded,
  conflict,
  onLoadProviders,
  onCreateProvider,
  onUpdateProvider,
  onDeleteProvider,
  onTestDraft,
  onDiscoverModels,
  activity,
  currentModel,
}: ProviderManagerPanelProps) {
  const t = useT()
  const [editor, setEditor] = useState<EditorState | null>(null)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null)
  const [deleteRefs, setDeleteRefs] = useState<{ name: string; refs: LlmProviderWriteResult['refs'] } | null>(null)
  const [modelInput, setModelInput] = useState('')
  const [aliasInput, setAliasInput] = useState('')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<LlmConnectionTestResult | null>(null)
  // 卡09：发现状态与草稿 epoch（端点/协议/凭据变化即过期；响应回带比对，不匹配丢弃）
  const [discovery, setDiscovery] = useState<DiscoveryState | null>(null)
  const [discoveryError, setDiscoveryError] = useState<LlmDiscoveryResult | null>(null)
  const [discovering, setDiscovering] = useState(false)
  const [discoverySelection, setDiscoverySelection] = useState<Record<string, boolean>>({})
  const [discoveryEpochRender, setDiscoveryEpochRender] = useState(0)
  const discoveryEpochRef = useRef(0)
  // 卡11：三态徽标与当前 provider 高亮
  const currentProvider = useMemo(() => providerForModel(providers, currentModel), [providers, currentModel])

  useEffect(() => {
    if (!loaded) void onLoadProviders()
  }, [loaded, onLoadProviders])

  const dirty = useMemo(
    () => (editor ? isProviderFormDirty(editor.form, editor.initial) : false),
    [editor],
  )

  function closeEditor() {
    setEditor(null)
    setFeedback(null)
    setTestResult(null)
    setModelInput('')
    setAliasInput('')
    setDiscovery(null)
    setDiscoveryError(null)
    setDiscoverySelection({})
    discoveryEpochRef.current = 0
    setDiscoveryEpochRender(0)
  }

  function confirmDiscard(): boolean {
    if (!dirty) return true
    return window.confirm(t('providerPanel.discardConfirm'))
  }

  function openCreate(form: ProviderFormState) {
    if (editor && dirty && !confirmDiscard()) return
    setFeedback(null)
    setTestResult(null)
    setModelInput('')
    setAliasInput('')
    setDiscovery(null)
    setDiscoveryError(null)
    setDiscoverySelection({})
    discoveryEpochRef.current = 0
    setDiscoveryEpochRender(0)
    setEditor({ mode: 'create', originalName: '', form, initial: form })
  }

  function openEdit(info: LlmProviderInfo) {
    if (editor && dirty && !confirmDiscard()) return
    setFeedback(null)
    setTestResult(null)
    setModelInput('')
    setAliasInput('')
    setDiscovery(null)
    setDiscoveryError(null)
    setDiscoverySelection({})
    discoveryEpochRef.current = 0
    setDiscoveryEpochRender(0)
    const form = formFromProvider(info)
    setEditor({ mode: 'edit', originalName: info.name, form, initial: form })
  }

  function tryClose() {
    if (confirmDiscard()) closeEditor()
  }

  function updateForm(patch: Partial<ProviderFormState>) {
    setEditor((current) => (current ? { ...current, form: { ...current.form, ...patch } } : current))
    // 卡09：端点/协议/凭据变化 → 旧发现结果标记过期（epoch 前移）
    if ('api' in patch || 'apiMode' in patch || 'apiKeyDraft' in patch || 'apiKeyTouched' in patch) {
      discoveryEpochRef.current += 1
      setDiscoveryEpochRender(discoveryEpochRef.current)
    }
  }

  function discoveryRequest(state: EditorState): LlmDiscoveryRequest {
    // 已保存 provider 且端点/协议/凭据未改 → 按 {name} 发现；否则用内联草稿（不先落盘）
    const modified =
      state.form.api !== state.initial.api ||
      state.form.apiMode !== state.initial.apiMode ||
      state.form.apiKeyTouched
    if (state.mode === 'edit' && !modified) return { name: state.originalName }
    const request: LlmDiscoveryRequest = { api: state.form.api, api_mode: state.form.apiMode }
    if (state.form.apiKeyTouched) request.api_key = state.form.apiKeyDraft
    return request
  }

  async function discoverModels() {
    if (!editor || discovering) return
    const requestEpoch = discoveryEpochRef.current
    setDiscoveryError(null)
    setDiscovering(true)
    try {
      const result = await onDiscoverModels(discoveryRequest(editor))
      if (!result.ok) {
        setDiscoveryError(result)
        return
      }
      if (requestEpoch !== discoveryEpochRef.current) {
        // 版本守卫：请求期间草稿端点/协议/凭据已变 → 丢弃过期结果，只标过期
        setDiscovery(null)
        setDiscoverySelection({})
        return
      }
      const models = result.models ?? []
      setDiscovery({
        epoch: requestEpoch,
        models,
        rawCount: result.raw_count ?? models.length,
        truncated: Boolean(result.truncated),
        pageCount: result.page_count ?? 1,
      })
      setDiscoverySelection(Object.fromEntries(models.map((m) => [m, false])))
    } finally {
      setDiscovering(false)
    }
  }

  function applyDiscovery() {
    if (!editor || !discovery) return
    let form = editor.form
    for (const model of discovery.models) {
      if (!discoverySelection[model]) continue
      // 增量并入：已有手输/alias/默认模型条目原样保留，重复（大小写不敏感）跳过
      const duplicate = form.models.some(
        (e) => e.alias.toLowerCase() === model.toLowerCase() || e.model.toLowerCase() === model.toLowerCase(),
      )
      if (duplicate) continue
      form = { ...form, models: [...form.models, { alias: model, model }], modelsTouched: true }
    }
    setEditor({ ...editor, form })
    setDiscovery(null)
    setDiscoverySelection({})
  }

  function setAllDiscoverySelection(checked: boolean) {
    if (!discovery) return
    setDiscoverySelection(Object.fromEntries(discovery.models.map((m) => [m, checked])))
  }

  function invertDiscoverySelection() {
    if (!discovery) return
    setDiscoverySelection((current) => {
      const next: Record<string, boolean> = { ...current }
      for (const model of discovery.models) next[model] = !next[model]
      return next
    })
  }

  async function save() {
    if (!editor || saving) return
    setSaving(true)
    setFeedback(null)
    try {
      const draft = formToDraft(editor.form)
      const result =
        editor.mode === 'create'
          ? await onCreateProvider(draft)
          : await onUpdateProvider(editor.originalName, draft)
      if (result.ok) {
        setFeedback({ ok: true, text: t('providerPanel.saved') })
        setTestResult(null)
        setEditor(null)
        setModelInput('')
        setAliasInput('')
        return
      }
      // config_modified：保留草稿，横幅（conflict prop）+ 内联提示双通道
      setFeedback({ ok: false, text: result.error ?? t('providerPanel.saveFailed') })
    } finally {
      setSaving(false)
    }
  }

  async function testDraft() {
    if (!editor || testing) return
    setTestResult(null)
    const model = draftTestModel(editor.form)
    if (!model) {
      setFeedback({ ok: false, text: t('providerPanel.testNeedsModel') })
      return
    }
    setTesting(true)
    try {
      const result = await onTestDraft(formToDraft(editor.form), model)
      setTestResult(result)
    } finally {
      setTesting(false)
    }
  }

  async function remove(name: string) {
    if (!window.confirm(t('providerPanel.deleteConfirm', { name }))) return
    setDeleteRefs(null)
    const result = await onDeleteProvider(name)
    if (!result.ok && result.code === 'in_use') {
      setDeleteRefs({ name, refs: result.refs ?? [] })
    }
  }

  return (
    <div className="provider-manager" data-testid="provider-manager">
      <h4>{t('providerPanel.title')}</h4>
      {conflict ? (
        <div className="provider-conflict-banner" data-testid="provider-conflict" role="alert">
          <span>{conflict}</span>
          <button type="button" onClick={() => void onLoadProviders()}>
            {t('providerPanel.refresh')}
          </button>
        </div>
      ) : null}

      {/* ── 模板网格 ── */}
      <div className="provider-template-grid" data-testid="provider-template-grid">
        {providerTemplates.map((template) => (
          <button
            key={template.name}
            type="button"
            className="provider-template-card"
            data-testid={`provider-template-${template.name}`}
            title={template.api}
            onClick={() => openCreate(formFromTemplate(template))}
          >
            <strong>{template.name}</strong>
            <small>{template.api_mode}</small>
          </button>
        ))}
        <button
          type="button"
          className="provider-template-card custom"
          data-testid="provider-template-custom"
          onClick={() => openCreate(emptyProviderForm())}
        >
          <strong>{t('providerPanel.customTemplate')}</strong>
          <small>{t('providerPanel.customTemplateHint')}</small>
        </button>
      </div>

      {/* ── 列表 ── */}
      <ul className="provider-list" data-testid="provider-list">
        {providers.map((info) => {
          const rowActivity = activity?.[info.name]
          return (
          <li
            key={info.name}
            className={`provider-row${info.name === currentProvider ? ' current' : ''}`}
            data-testid={`provider-row-${info.name}`}
            data-current={info.name === currentProvider || undefined}
          >
            <strong>{info.name}</strong>
            {info.name === currentProvider ? (
              <span className="provider-current-badge" data-testid={`provider-current-${info.name}`}>
                {t('providerPanel.currentBadge')}
              </span>
            ) : null}
            <small className="provider-api">{info.api || '—'}</small>
            <small>{t('providerPanel.modelCount', { count: info.model_count })}</small>
            <small className="provider-key-display">{info.key_display || '—'}</small>
            <span className="provider-credential-badge" data-testid={`provider-credential-${info.name}`}>
              {t(credentialBadgeKey(info.credential))}
            </span>
            <span
              className={`provider-state-badge${info.credential.resolvable ? ' ok' : ''}`}
              data-testid={`provider-configured-${info.name}`}
            >
              {info.credential.resolvable ? t('providerPanel.stateConfigured') : t('providerPanel.stateUnconfigured')}
            </span>
            {rowActivity?.discovered ? (
              <span className="provider-state-badge" data-testid={`provider-discovered-${info.name}`}>
                {t('providerPanel.stateDiscovered', { time: formatActivityTime(rowActivity.discovered) })}
              </span>
            ) : null}
            {rowActivity?.tested ? (
              <span className="provider-state-badge ok" data-testid={`provider-tested-${info.name}`}>
                {t('providerPanel.stateTested', { time: formatActivityTime(rowActivity.tested) })}
              </span>
            ) : null}
            {info.is_codex ? null : (
              <>
                <button type="button" data-testid={`provider-edit-${info.name}`} onClick={() => openEdit(info)}>
                  {t('providerPanel.edit')}
                </button>
                <button type="button" data-testid={`provider-delete-${info.name}`} onClick={() => void remove(info.name)}>
                  {t('providerPanel.delete')}
                </button>
              </>
            )}
          </li>
          )
        })}
        {providers.length === 0 ? <li className="provider-empty">{t('providerPanel.empty')}</li> : null}
      </ul>

      {deleteRefs ? (
        <div className="provider-delete-refs" data-testid="provider-delete-error" role="alert">
          <p>{t('providerPanel.deleteBlocked', { name: deleteRefs.name })}</p>
          <ul>
            {(deleteRefs.refs ?? []).map((ref, i) => (
              <li key={`${ref.location}-${i}`}>
                <code>{ref.location}</code>
                {ref.context ? <span> · {ref.context}</span> : null}
                {ref.detail ? <small> — {ref.detail}</small> : null}
              </li>
            ))}
          </ul>
          <button type="button" onClick={() => setDeleteRefs(null)}>
            {t('providerPanel.dismiss')}
          </button>
        </div>
      ) : null}

      {/* ── 草稿表单 ── */}
      {editor ? (
        <div className="provider-form" data-testid="provider-form">
          <h5>{editor.mode === 'create' ? t('providerPanel.addTitle') : t('providerPanel.editTitle')}</h5>
          <label className="settings-field">
            <span>{t('providerPanel.nameLabel')}</span>
            <input
              value={editor.form.name}
              data-testid="provider-name"
              disabled={editor.mode === 'edit'}
              placeholder={t('providerPanel.namePlaceholder')}
              onChange={(e) => updateForm({ name: e.target.value })}
            />
          </label>
          <label className="settings-field">
            <span>{t('providerPanel.apiLabel')}</span>
            <input
              value={editor.form.api}
              data-testid="provider-api"
              placeholder="https://api.example.com/v1"
              onChange={(e) => updateForm({ api: e.target.value })}
            />
          </label>
          <label className="settings-field">
            <span>{t('providerPanel.apiModeLabel')}</span>
            <select
              value={editor.form.apiMode}
              data-testid="provider-api-mode"
              onChange={(e) => updateForm({ apiMode: e.target.value })}
            >
              {PROVIDER_API_MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {mode}
                </option>
              ))}
            </select>
          </label>
          <label className="settings-field">
            <span>{t('providerPanel.apiKeyLabel')}</span>
            <input
              type="password"
              value={editor.form.apiKeyDraft}
              data-testid="provider-api-key"
              placeholder={t('providerPanel.apiKeyPlaceholder')}
              autoComplete="new-password"
              onChange={(e) => updateForm({ apiKeyDraft: e.target.value, apiKeyTouched: true })}
            />
            <small>{t('providerPanel.apiKeyHint')}</small>
          </label>
          <label className="settings-field">
            <span>{t('providerPanel.defaultModelLabel')}</span>
            <input
              value={editor.form.defaultModel}
              data-testid="provider-default-model"
              onChange={(e) => updateForm({ defaultModel: e.target.value, defaultModelTouched: true })}
            />
          </label>

          {/* 手输模型（第一公民）：标签列表 + alias/model 对 */}
          <div className="settings-field">
            <span>{t('providerPanel.modelsLabel')}</span>
            <div className="provider-model-tags" data-testid="provider-model-tags">
              {editor.form.models.map((entry, i) => (
                <span key={`${entry.alias}-${i}`} className="provider-model-tag">
                  {entry.alias === entry.model ? entry.alias : `${entry.alias} (${entry.model})`}
                  <button
                    type="button"
                    data-testid={`provider-model-remove-${i}`}
                    onClick={() => updateForm(removeModelEntry(editor.form, i))}
                  >
                    ×
                  </button>
                </span>
              ))}
              {editor.form.models.length === 0 ? <small>{t('providerPanel.modelsEmpty')}</small> : null}
            </div>
            <div className="provider-model-input-row">
              <input
                value={modelInput}
                data-testid="provider-model-input"
                placeholder={t('providerPanel.modelInputPlaceholder')}
                onChange={(e) => setModelInput(e.target.value)}
              />
              <input
                value={aliasInput}
                data-testid="provider-alias-input"
                placeholder={t('providerPanel.aliasInputPlaceholder')}
                onChange={(e) => setAliasInput(e.target.value)}
              />
              <button
                type="button"
                data-testid="provider-model-add"
                onClick={() => {
                  updateForm(addModelEntry(editor.form, modelInput, aliasInput))
                  setModelInput('')
                  setAliasInput('')
                }}
              >
                {t('providerPanel.modelAdd')}
              </button>
            </div>
            <small>{t('providerPanel.modelsHint')}</small>
          </div>

          <div className="settings-actions inline">
            <button
              type="button"
              className="primary-action"
              data-testid="provider-save"
              disabled={saving}
              onClick={() => void save()}
            >
              {saving ? t('providerPanel.saving') : t('providerPanel.save')}
            </button>
            <button type="button" data-testid="provider-cancel" onClick={tryClose}>
              {t('providerPanel.cancel')}
            </button>
            <button type="button" data-testid="provider-test" disabled={testing} onClick={() => void testDraft()}>
              {testing ? t('providerPanel.testing') : t('providerPanel.test')}
            </button>
            <button
              type="button"
              data-testid="provider-discover"
              disabled={discovering}
              onClick={() => void discoverModels()}
            >
              {discovering ? t('providerPanel.discovering') : t('providerPanel.discover')}
            </button>
            <small>{t('providerPanel.testHint')}</small>
          </div>
          <small>{t('providerPanel.discoverHint')}</small>

          {discoveryError ? (
            <div className="provider-discovery-error" data-testid="provider-discovery-error" role="alert">
              <p>{t('providerPanel.discoverFail', { category: discoveryError.category ?? '' })}</p>
              {discoveryError.message ? <p>{discoveryError.message}</p> : null}
              {discoveryError.fix_hint ? <p>{discoveryError.fix_hint}</p> : null}
              <small>{t('providerPanel.discoverFailManual')}</small>
            </div>
          ) : null}

          {discovery ? (
            <div className="provider-discovery-panel" data-testid="provider-discovery-panel">
              {discovery.epoch !== discoveryEpochRender ? (
                <p className="provider-discovery-stale" data-testid="provider-discovery-stale">
                  {t('providerPanel.discoverStale')}
                </p>
              ) : null}
              <p>
                {t('providerPanel.discoverMeta', {
                  count: String(discovery.rawCount),
                  kept: String(discovery.models.length),
                  pages: String(discovery.pageCount),
                })}
                {discovery.truncated ? ` · ${t('providerPanel.discoverTruncated')}` : ''}
              </p>
              <div className="settings-actions inline">
                <button type="button" data-testid="provider-discovery-select-all" onClick={() => setAllDiscoverySelection(true)}>
                  {t('providerPanel.discoverySelectAll')}
                </button>
                <button type="button" data-testid="provider-discovery-invert" onClick={() => invertDiscoverySelection()}>
                  {t('providerPanel.discoveryInvert')}
                </button>
                <button type="button" className="primary-action" data-testid="provider-discovery-apply" onClick={applyDiscovery}>
                  {t('providerPanel.discoveryApply')}
                </button>
              </div>
              <ul className="provider-discovery-models">
                {discovery.models.map((model) => (
                  <li key={model}>
                    <label>
                      <input
                        type="checkbox"
                        data-testid={`provider-discovery-model-${model}`}
                        checked={Boolean(discoverySelection[model])}
                        onChange={(e) =>
                          setDiscoverySelection((current) => ({ ...current, [model]: e.target.checked }))
                        }
                      />
                      {model}
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {feedback && !feedback.ok ? (
            <p className="provider-form-error" data-testid="provider-form-error" role="alert">
              {feedback.text}
            </p>
          ) : null}
          {testResult ? (
            <div
              className={`provider-test-result ${testResult.ok ? 'ok' : 'fail'}`}
              data-testid="provider-test-result"
            >
              {testResult.ok
                ? t('providerPanel.testOk', { model: testResult.model ?? '', duration: String(testResult.duration_ms ?? '') })
                : `${t('providerPanel.testFail')}: ${testResult.error ?? ''}`}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
