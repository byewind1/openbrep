import { useEffect, useMemo, useState } from 'react'
import type {
  LlmConnectionTestResult,
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
  formFromProvider,
  formFromTemplate,
  formToDraft,
  isProviderFormDirty,
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
    setEditor({ mode: 'create', originalName: '', form, initial: form })
  }

  function openEdit(info: LlmProviderInfo) {
    if (editor && dirty && !confirmDiscard()) return
    setFeedback(null)
    setTestResult(null)
    setModelInput('')
    setAliasInput('')
    const form = formFromProvider(info)
    setEditor({ mode: 'edit', originalName: info.name, form, initial: form })
  }

  function tryClose() {
    if (confirmDiscard()) closeEditor()
  }

  function updateForm(patch: Partial<ProviderFormState>) {
    setEditor((current) => (current ? { ...current, form: { ...current.form, ...patch } } : current))
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
        {providers.map((info) => (
          <li key={info.name} className="provider-row" data-testid={`provider-row-${info.name}`}>
            <strong>{info.name}</strong>
            <small className="provider-api">{info.api || '—'}</small>
            <small>{t('providerPanel.modelCount', { count: info.model_count })}</small>
            <small className="provider-key-display">{info.key_display || '—'}</small>
            <span className="provider-credential-badge" data-testid={`provider-credential-${info.name}`}>
              {t(credentialBadgeKey(info.credential))}
            </span>
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
        ))}
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
            <small>{t('providerPanel.testHint')}</small>
          </div>

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
