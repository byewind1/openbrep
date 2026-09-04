import { beforeEach, describe, expect, test } from 'vitest'
import {
  MODEL_VISIBILITY_STORAGE_KEY,
  buildVisibilityCatalog,
  defaultVisibleKeys,
  effectiveVisibleKeys,
  emptyProviderSentinelKey,
  modelVisibilityKey,
  readStoredVisibilityKeys,
  resolveVisibleKeys,
  setProviderVisibility,
  toggleModelVisibility,
  useModelVisibilityStore,
  type VisibilityProvider,
} from './modelVisibility'

function provider(slug: string, models: string[], defaultVisible: boolean): VisibilityProvider {
  return {
    slug,
    label: slug,
    kind: 'official',
    models: models.map((id) => ({ id, label: id })),
    defaultVisible,
  }
}

const CATALOG: VisibilityProvider[] = [
  provider('deepseek', ['deepseek-chat', 'deepseek-reasoner'], true),
  provider('zhipu', ['glm-4-flash', 'glm-4-plus'], false),
]

describe('modelVisibility state machine', () => {
  test('never customized (null) → default visible rules expand configured providers only', () => {
    const keys = effectiveVisibleKeys(null, CATALOG)
    expect(keys).toEqual(new Set(['deepseek::deepseek-chat', 'deepseek::deepseek-reasoner']))
  })

  test('explicit empty set = everything hidden, defaults do not re-expand', () => {
    expect(effectiveVisibleKeys(new Set(), CATALOG)).toEqual(new Set())
  })

  test('stored keys + untouched providers still get their defaults', () => {
    const stored = new Set(['zhipu::glm-4-flash'])
    const resolved = effectiveVisibleKeys(stored, CATALOG)
    expect(resolved).toEqual(
      new Set(['zhipu::glm-4-flash', 'deepseek::deepseek-chat', 'deepseek::deepseek-reasoner']),
    )
  })

  test('toggling off the last model of a provider writes the hide-all sentinel', () => {
    let next = toggleModelVisibility(null, CATALOG, 'deepseek', 'deepseek-chat')
    next = toggleModelVisibility(next, CATALOG, 'deepseek', 'deepseek-reasoner')
    expect(next.has(emptyProviderSentinelKey('deepseek'))).toBe(true)
    // 展示集为空（哨兵被剔除）
    expect(effectiveVisibleKeys(next, CATALOG)).toEqual(new Set())
  })

  test('sentinel prevents defaults re-expanding when a new model enters the catalog', () => {
    let next = toggleModelVisibility(null, CATALOG, 'deepseek', 'deepseek-chat')
    next = toggleModelVisibility(next, CATALOG, 'deepseek', 'deepseek-reasoner')
    const grown: VisibilityProvider[] = [
      provider('deepseek', ['deepseek-chat', 'deepseek-reasoner', 'deepseek-v4'], true),
      CATALOG[1],
    ]
    // 新模型 deepseek-v4 不得对该 provider 自动展开
    expect(effectiveVisibleKeys(next, grown)).toEqual(new Set())
  })

  test('re-enabling one model after hide-all returns exactly that one model', () => {
    let next = toggleModelVisibility(null, CATALOG, 'deepseek', 'deepseek-chat')
    next = toggleModelVisibility(next, CATALOG, 'deepseek', 'deepseek-reasoner')
    next = toggleModelVisibility(next, CATALOG, 'deepseek', 'deepseek-reasoner')
    expect(next.has(emptyProviderSentinelKey('deepseek'))).toBe(false)
    expect(effectiveVisibleKeys(next, CATALOG)).toEqual(new Set(['deepseek::deepseek-reasoner']))
  })

  test('toggling a model in an uncustomized provider seeds from defaults of others', () => {
    // zhipu 默认隐藏；打开 glm-4-flash 时 deepseek 默认值不丢
    const next = toggleModelVisibility(null, CATALOG, 'zhipu', 'glm-4-flash')
    expect(next.has('deepseek::deepseek-chat')).toBe(true)
    expect(next.has('zhipu::glm-4-flash')).toBe(true)
    expect(next.has('zhipu::glm-4-plus')).toBe(false)
  })

  test('provider master switch off writes sentinel; on restores all models', () => {
    const off = setProviderVisibility(null, CATALOG, 'deepseek', false)
    expect(off.has(emptyProviderSentinelKey('deepseek'))).toBe(true)
    const on = setProviderVisibility(off, CATALOG, 'deepseek', true)
    expect(on.has(emptyProviderSentinelKey('deepseek'))).toBe(false)
    expect(on.has('deepseek::deepseek-chat')).toBe(true)
    expect(on.has('deepseek::deepseek-reasoner')).toBe(true)
  })

  test('resolveVisibleKeys keeps sentinels (working set), effectiveVisibleKeys strips them', () => {
    const stored = new Set([emptyProviderSentinelKey('deepseek'), 'zhipu::glm-4-flash'])
    expect(resolveVisibleKeys(stored, CATALOG).has(emptyProviderSentinelKey('deepseek'))).toBe(true)
    expect(effectiveVisibleKeys(stored, CATALOG).has(emptyProviderSentinelKey('deepseek'))).toBe(false)
  })

  test('modelVisibilityKey uses :: separator; sentinel ends with ::', () => {
    expect(modelVisibilityKey('ollama', 'qwen3:8b')).toBe('ollama::qwen3:8b')
    expect(emptyProviderSentinelKey('deepseek')).toBe('deepseek::')
  })
})

describe('defaultVisibleKeys', () => {
  test('only defaultVisible providers expand', () => {
    expect(defaultVisibleKeys(CATALOG)).toEqual(
      new Set(['deepseek::deepseek-chat', 'deepseek::deepseek-reasoner']),
    )
  })
})

describe('buildVisibilityCatalog', () => {
  const llmSettings = {
    model_groups: {
      custom: [
        { id: 'ymg/deepseek-v3', label: 'ymg/deepseek-v3', kind: 'custom' as const, provider: 'ymg', has_api_key: true },
      ],
      official: [
        { id: 'deepseek-chat', label: 'deepseek-chat', kind: 'official' as const, provider: 'deepseek', has_api_key: true },
        { id: 'glm-4-flash', label: 'glm-4-flash', kind: 'official' as const, provider: 'zhipu', has_api_key: false },
        { id: 'ollama/qwen3:8b', label: 'ollama/qwen3:8b', kind: 'official' as const, provider: 'ollama', has_api_key: false },
      ],
    },
  }

  test('default rules: custom configured → visible; official with key → visible; without key → hidden; ollama → visible', () => {
    const catalog = buildVisibilityCatalog(llmSettings, { connected: false, models: [] })
    const bySlug = new Map(catalog.map((p) => [p.slug, p]))
    expect(bySlug.get('ymg')?.defaultVisible).toBe(true)
    expect(bySlug.get('ymg')?.kind).toBe('custom')
    expect(bySlug.get('deepseek')?.defaultVisible).toBe(true)
    expect(bySlug.get('zhipu')?.defaultVisible).toBe(false)
    expect(bySlug.get('ollama')?.defaultVisible).toBe(true)
    expect(bySlug.has('openai-codex')).toBe(false)
  })

  test('codex connected → codex group visible; disconnected → absent', () => {
    const codexModels = [
      { id: 'openai-codex/gpt-5.6-luna', label: 'GPT-5.6 Luna', model: 'gpt-5.6-luna' },
    ]
    const connected = buildVisibilityCatalog(llmSettings, { connected: true, models: codexModels })
    const codex = connected.find((p) => p.slug === 'openai-codex')
    expect(codex?.defaultVisible).toBe(true)
    expect(codex?.kind).toBe('codex')
    expect(codex?.models[0]?.label).toBe('GPT-5.6 Luna')
    const disconnected = buildVisibilityCatalog(llmSettings, { connected: false, models: codexModels })
    expect(disconnected.find((p) => p.slug === 'openai-codex')).toBeUndefined()
  })
})

describe('persistence', () => {
  beforeEach(() => {
    localStorage.clear()
    useModelVisibilityStore.setState({ keys: null })
  })

  test('setKeys persists to localStorage under openbrep.visible-models', () => {
    useModelVisibilityStore.getState().setKeys(new Set(['deepseek::deepseek-chat']))
    const raw = localStorage.getItem(MODEL_VISIBILITY_STORAGE_KEY)
    expect(raw).toBeTruthy()
    expect(readStoredVisibilityKeys(localStorage)).toEqual(['deepseek::deepseek-chat'])
  })

  test('setKeys(null) persists null (never customized)', () => {
    useModelVisibilityStore.getState().setKeys(new Set(['a::b']))
    useModelVisibilityStore.getState().setKeys(null)
    expect(localStorage.getItem(MODEL_VISIBILITY_STORAGE_KEY)).toContain('"keys":null')
  })

  test('readStoredVisibilityKeys tolerates corrupted JSON → null (default rules)', () => {
    localStorage.setItem(MODEL_VISIBILITY_STORAGE_KEY, '{not json')
    expect(readStoredVisibilityKeys(localStorage)).toBeNull()
  })

  test('readStoredVisibilityKeys filters non-string entries', () => {
    localStorage.setItem(
      MODEL_VISIBILITY_STORAGE_KEY,
      JSON.stringify({ state: { keys: ['a::b', 42, null, 'c::d'] }, version: 0 }),
    )
    expect(readStoredVisibilityKeys(localStorage)).toEqual(['a::b', 'c::d'])
  })

  test('readStoredVisibilityKeys rejects non-array keys shape → null', () => {
    localStorage.setItem(MODEL_VISIBILITY_STORAGE_KEY, JSON.stringify({ state: { keys: 'a::b' } }))
    expect(readStoredVisibilityKeys(localStorage)).toBeNull()
  })

  test('store rehydrate with corrupted JSON falls back to null (default rules), does not crash', async () => {
    localStorage.setItem(MODEL_VISIBILITY_STORAGE_KEY, '{corrupted!!!')
    await useModelVisibilityStore.persist.rehydrate()
    expect(useModelVisibilityStore.getState().keys).toBeNull()
    // 回落后默认可见规则照常工作
    const keys = useModelVisibilityStore.getState().keys
    expect(effectiveVisibleKeys(keys === null ? null : new Set(keys), CATALOG)).toEqual(
      new Set(['deepseek::deepseek-chat', 'deepseek::deepseek-reasoner']),
    )
  })

  test('stored keys contain only provider::model identifiers (no secrets shape)', () => {
    useModelVisibilityStore.getState().setKeys(new Set(['deepseek::deepseek-chat', 'zhipu::']))
    const raw = localStorage.getItem(MODEL_VISIBILITY_STORAGE_KEY) ?? ''
    // 键集合只由标识符字符构成：字母数字、- _ . / : 与 JSON 结构字符
    const keys = readStoredVisibilityKeys(localStorage) ?? []
    for (const key of keys) {
      expect(key).toMatch(/^[\w./-]+::[\w./:-]*$/)
    }
    expect(raw).not.toMatch(/sk-|api[-_]?key/i)
  })
})
