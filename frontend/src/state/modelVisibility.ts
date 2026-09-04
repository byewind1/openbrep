/**
 * 模型可见性 store（D16）：三态 + hide-all 哨兵状态机移植自
 * Hermes Agent（Nous Research，MIT License）
 * apps/desktop/src/store/model-visibility.ts。
 * 逻辑移植、按 OpenBrep 目录模型适配（无 family 折叠 / featured_models /
 * 目录缓存——见设计稿 §3.4）；持久化用 zustand persist（对齐 uiPrefsStore 风格），
 * localStorage key `openbrep.visible-models`，键 `provider::model`。
 *
 * 可见性是纯 UI 策展：不进 config.toml，不触发 draft+save 纪律。
 */
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import type { StateStorage } from 'zustand/middleware'
import type { CodexModelInfo, LlmModelOption, LlmSettings } from '../api/types'

export const MODEL_VISIBILITY_STORAGE_KEY = 'openbrep.visible-models'

/** provider/模型对的稳定键（`::` 避免与模型 id 里单个冒号冲突，如 `model:tag`）。 */
export const modelVisibilityKey = (provider: string, model: string): string => `${provider}::${model}`

/** 用户显式全关某 provider 时写入的哨兵键后缀（空模型名）。 */
export const EMPTY_PROVIDER_SENTINEL = ''

export const emptyProviderSentinelKey = (provider: string): string =>
  modelVisibilityKey(provider, EMPTY_PROVIDER_SENTINEL)

export const isProviderSentinel = (key: string): boolean => key.endsWith('::')

/** 显式全关（哨兵键存在）的 provider slug 集。stored=null（从未自定义）不适用，
 *  返回空集——默认隐藏的官方预设不算显式关闭（pill 搜索可发现性不变）。 */
export function sentinelHiddenProviders(stored: Set<string> | null): Set<string> {
  const hidden = new Set<string>()
  if (!stored) return hidden
  for (const key of stored) {
    if (isProviderSentinel(key)) {
      hidden.add(key.slice(0, -2))
    }
  }
  return hidden
}

export interface VisibilityModelEntry {
  id: string
  label: string
}

/** 可见性面板/pill 的 provider 目录条目。 */
export interface VisibilityProvider {
  slug: string
  label: string
  kind: 'custom' | 'official' | 'codex'
  models: VisibilityModelEntry[]
  /** 默认可见规则（用户从未自定义时生效）：已配置 / Codex 已连接 → true；
   *  仅官方预设未配 key → false（开关面板里可手动打开）。 */
  defaultVisible: boolean
}

/** 默认可见键集：defaultVisible 的 provider 展开全部模型，其余一个不加。
 *  既是 pill 菜单的兜底，也是设置页开关面板的初值。 */
export function defaultVisibleKeys(providers: readonly VisibilityProvider[]): Set<string> {
  const keys = new Set<string>()
  for (const provider of providers) {
    expandProviderDefaults(provider, keys)
  }
  return keys
}

function expandProviderDefaults(provider: VisibilityProvider, target: Set<string>): void {
  if (!provider.defaultVisible) return
  for (const entry of provider.models) {
    target.add(modelVisibilityKey(provider.slug, entry.id))
  }
}

/** 解析工作集：用户已存键 + 未自定义 provider 的默认展开。hide-all 哨兵在此
 *  保留——这是 toggle 处理器改写并持久化的集合，丢哨兵会把用户清空的 provider
 *  静默重新打开。展示用 effectiveVisibleKeys（哨兵剔除）。 */
export function resolveVisibleKeys(
  stored: Set<string> | null,
  providers: readonly VisibilityProvider[],
): Set<string> {
  if (!stored) {
    return defaultVisibleKeys(providers)
  }
  if (stored.size === 0) {
    return new Set()
  }
  const next = new Set(stored)
  for (const provider of providers) {
    const providerPrefix = `${provider.slug}::`
    const hasStoredProvider = [...stored].some(
      (key) => key.startsWith(providerPrefix) && !isProviderSentinel(key),
    )
    const hasSentinel = stored.has(emptyProviderSentinelKey(provider.slug))
    if (hasStoredProvider || hasSentinel) {
      continue
    }
    expandProviderDefaults(provider, next)
  }
  return next
}

/** 展示用可见键集：工作集剔除簿记哨兵（哨兵不是真实模型）。 */
export function effectiveVisibleKeys(
  stored: Set<string> | null,
  providers: readonly VisibilityProvider[],
): Set<string> {
  const next = resolveVisibleKeys(stored, providers)
  for (const key of [...next]) {
    if (isProviderSentinel(key)) {
      next.delete(key)
    }
  }
  return next
}

/** 单个模型行开关后的下一个持久化集合。从 resolveVisibleKeys 取种子（不是
 *  effectiveVisibleKeys），其他 provider 的哨兵才能随持久化存活。某 provider
 *  最后一个可见模型被关掉时写入哨兵；重新开启一个模型只清该 provider 哨兵，
 *  不恢复默认集（"你全关了，就只拿回复开的那一个"）。 */
export function toggleModelVisibility(
  stored: Set<string> | null,
  providers: readonly VisibilityProvider[],
  providerSlug: string,
  model: string,
): Set<string> {
  const next = resolveVisibleKeys(stored, providers)
  const key = modelVisibilityKey(providerSlug, model)
  const sentinel = emptyProviderSentinelKey(providerSlug)

  if (next.has(key)) {
    next.delete(key)
    const remainingForProvider = [...next].some(
      (k) => k.startsWith(`${providerSlug}::`) && !isProviderSentinel(k),
    )
    if (!remainingForProvider) {
      next.add(sentinel)
    }
  } else {
    next.delete(sentinel)
    next.add(key)
  }
  return next
}

/** provider 总开关：visible=true 打开该 provider 全部模型（并清哨兵）；
 *  visible=false 全部移除并写哨兵，防止默认值被静默重新展开。 */
export function setProviderVisibility(
  stored: Set<string> | null,
  providers: readonly VisibilityProvider[],
  providerSlug: string,
  visible: boolean,
): Set<string> {
  const next = resolveVisibleKeys(stored, providers)
  const sentinel = emptyProviderSentinelKey(providerSlug)
  const provider = providers.find((p) => p.slug === providerSlug)

  for (const key of [...next]) {
    if (key.startsWith(`${providerSlug}::`)) {
      next.delete(key)
    }
  }

  if (visible) {
    for (const entry of provider?.models ?? []) {
      next.add(modelVisibilityKey(providerSlug, entry.id))
    }
    if ((provider?.models ?? []).length === 0) {
      next.delete(sentinel)
    }
  } else {
    next.add(sentinel)
  }
  return next
}

/** 读取 localStorage 里的可见性键集；损坏 JSON / 非字符串数组一律回退 null
 *  （= 从未自定义，走默认可见规则）。键只含 provider::model 标识符，无任何秘密。 */
export function readStoredVisibilityKeys(storage: Pick<Storage, 'getItem'>): string[] | null {
  try {
    const raw = storage.getItem(MODEL_VISIBILITY_STORAGE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    const keys = (parsed as { state?: { keys?: unknown } } | null)?.state?.keys
    if (keys === null || keys === undefined) return null
    if (!Array.isArray(keys)) return null
    return keys.filter((k): k is string => typeof k === 'string')
  } catch {
    return null
  }
}

/** 损坏容错 storage：getItem 先过一遍 readStoredVisibilityKeys 的校验，
 *  非法内容当作"从未存储"，persist 中间件回落初始 state（keys=null）。 */
const tolerantStorage: StateStorage = {
  getItem: (name) => {
    const keys = readStoredVisibilityKeys(localStorage)
    if (keys === null && localStorage.getItem(name) === null) return null
    return JSON.stringify({ state: { keys }, version: 0 })
  },
  setItem: (name, value) => {
    localStorage.setItem(name, value)
  },
  removeItem: (name) => {
    localStorage.removeItem(name)
  },
}

interface ModelVisibilityState {
  /** 显式可见键集（含 hide-all 哨兵键）；null = 从未自定义 → 默认可见规则。 */
  keys: string[] | null
  setKeys: (keys: Set<string> | null) => void
}

export const useModelVisibilityStore = create<ModelVisibilityState>()(
  persist(
    (set) => ({
      keys: null,
      setKeys: (keys) => set({ keys: keys === null ? null : [...keys] }),
    }),
    {
      name: MODEL_VISIBILITY_STORAGE_KEY,
      storage: createJSONStorage(() => tolerantStorage),
      partialize: (state) => ({ keys: state.keys }),
    },
  ),
)

/** 从 llmSettings 的模型目录 + codex 动态目录构建可见性 provider 目录。
 *  codex 未连接时不进目录（pill 菜单/开关面板都不出现）。 */
export function buildVisibilityCatalog(
  llmSettings: Pick<LlmSettings, 'model_groups'>,
  codex: { connected: boolean; models: CodexModelInfo[] },
): VisibilityProvider[] {
  const providers: VisibilityProvider[] = []
  const custom = llmSettings.model_groups?.custom ?? []
  const official = llmSettings.model_groups?.official ?? []

  for (const [providerName, models] of groupByProvider(custom)) {
    providers.push({
      slug: providerName,
      label: providerName,
      kind: 'custom',
      models: models.map((m) => ({ id: m.id, label: m.label })),
      // 自定义 provider 有条目即已配置 → 默认可见
      defaultVisible: true,
    })
  }
  for (const [providerName, models] of groupByProvider(official)) {
    providers.push({
      slug: providerName,
      label: providerName,
      kind: 'official',
      models: models.map((m) => ({ id: m.id, label: m.label })),
      // ollama 本地模型无需 key 即已配置；其余官方预设需 provider_keys 有 key
      defaultVisible: providerName === 'ollama' || models.some((m) => m.has_api_key),
    })
  }
  if (codex.connected && codex.models.length > 0) {
    providers.push({
      slug: 'openai-codex',
      label: 'openai-codex',
      kind: 'codex',
      models: codex.models.map((m) => ({ id: m.id, label: m.label })),
      defaultVisible: true,
    })
  }
  return providers
}

function groupByProvider(models: LlmModelOption[]): [string, LlmModelOption[]][] {
  const groups = new Map<string, LlmModelOption[]>()
  for (const model of models) {
    const list = groups.get(model.provider) ?? []
    list.push(model)
    groups.set(model.provider, list)
  }
  return [...groups.entries()]
}
