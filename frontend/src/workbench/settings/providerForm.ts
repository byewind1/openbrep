// 卡06：Provider 表单纯函数（draft 状态、模板/既有条目预填、dirty 判定、草稿序列化）。
// 组件层只做渲染与事件绑定；此模块全部为纯函数，便于单测。

import type { LlmProviderDraft, LlmProviderInfo, LlmProviderModelEntry, LlmProviderTemplate } from '../../api/types'

export interface ProviderFormState {
  name: string
  api: string
  apiMode: string
  /** write-only 输入值；'' 且未触碰 = 不提交（key 保持） */
  apiKeyDraft: string
  apiKeyTouched: boolean
  defaultModel: string
  models: LlmProviderModelEntry[]
  /** 卡09 预留：models 是否被用户改过（改过才随 Save 提交，避免覆盖手输对） */
  modelsTouched: boolean
  defaultModelTouched: boolean
}

export const PROVIDER_API_MODES = ['chat_completions', 'anthropic_messages', 'responses'] as const

export function emptyProviderForm(): ProviderFormState {
  return {
    name: '',
    api: '',
    apiMode: 'chat_completions',
    apiKeyDraft: '',
    apiKeyTouched: false,
    defaultModel: '',
    models: [],
    modelsTouched: false,
    defaultModelTouched: false,
  }
}

export function formFromTemplate(template: LlmProviderTemplate): ProviderFormState {
  const models = (template.models ?? []).map((m) => ({ alias: m, model: m }))
  return {
    // 模板名是保留名（POST 拒绝）：name 留空由用户起（如 zhipu-main）
    name: '',
    api: template.api ?? '',
    apiMode: template.api_mode ?? 'chat_completions',
    apiKeyDraft: '',
    apiKeyTouched: false,
    defaultModel: models[0]?.alias ?? '',
    models,
    modelsTouched: false,
    defaultModelTouched: false,
  }
}

export function formFromProvider(info: LlmProviderInfo): ProviderFormState {
  const models = (info.model_entries ?? []).map((e) => ({ alias: e.alias, model: e.model }))
  return {
    name: info.name,
    api: info.api,
    apiMode: info.api_mode,
    apiKeyDraft: '',
    apiKeyTouched: false,
    defaultModel: info.default_model,
    models,
    modelsTouched: false,
    defaultModelTouched: false,
  }
}

export function formToDraft(form: ProviderFormState): LlmProviderDraft {
  const draft: LlmProviderDraft = {
    api: form.api,
    api_mode: form.apiMode,
  }
  if (form.name.trim()) draft.name = form.name.trim()
  // key 三态：未触碰 = 缺省（保持）；触碰过 = 显式值（含空串 = 清除）
  if (form.apiKeyTouched) draft.api_key = form.apiKeyDraft
  if (form.defaultModelTouched) draft.default_model = form.defaultModel.trim()
  // models 只在用户改过时提交——避免把投影 alias 覆盖回手输 alias/model 对
  if (form.modelsTouched) draft.models = form.models.map((e) => ({ alias: e.alias, model: e.model }))
  return draft
}

export function modelsEquivalent(a: LlmProviderModelEntry[], b: LlmProviderModelEntry[]): boolean {
  if (a.length !== b.length) return false
  return a.every((entry, i) => entry.alias === b[i].alias && entry.model === b[i].model)
}

export function isProviderFormDirty(form: ProviderFormState, initial: ProviderFormState): boolean {
  if (form.name !== initial.name) return true
  if (form.api !== initial.api) return true
  if (form.apiMode !== initial.apiMode) return true
  if (form.apiKeyTouched) return true
  if (form.defaultModel !== initial.defaultModel) return true
  if (form.modelsTouched) return true
  return false
}

export function addModelEntry(
  form: ProviderFormState,
  model: string,
  alias?: string,
): ProviderFormState {
  const trimmedModel = model.trim()
  if (!trimmedModel) return form
  const trimmedAlias = (alias ?? '').trim() || trimmedModel
  if (form.models.some((e) => e.alias === trimmedAlias && e.model === trimmedModel)) return form
  return {
    ...form,
    models: [...form.models, { alias: trimmedAlias, model: trimmedModel }],
    modelsTouched: true,
  }
}

export function removeModelEntry(form: ProviderFormState, index: number): ProviderFormState {
  return {
    ...form,
    models: form.models.filter((_, i) => i !== index),
    modelsTouched: true,
  }
}

/** 草稿测试用的模型引用：default_model 优先，其次第一个模型 */
export function draftTestModel(form: ProviderFormState): string {
  const name = form.name.trim() || 'draft'
  const target = form.defaultModel.trim() || form.models[0]?.alias || ''
  return target ? `${name}/${target}` : ''
}

/** 凭据徽标文案：返回 i18n 键（providerForm 不直接依赖 useT）。 */
export function credentialBadgeKey(status: LlmProviderInfo['credential']): import('../../i18n').LocaleKey {
  if (status.form === 'pool') return 'providerPanel.credential.pool'
  switch (status.location) {
    case 'entry':
      return 'providerPanel.credential.entry'
    case 'provider_keys':
      return 'providerPanel.credential.providerKeys'
    case 'top_level':
      return 'providerPanel.credential.topLevel'
    case 'env':
      return 'providerPanel.credential.env'
    default:
      return 'providerPanel.credential.none'
  }
}
