import { useMemo, useRef, useState } from 'react'
import type { CodexModelInfo, LlmSettings } from '../api/types'
import { useT } from '../i18n'
import {
  buildVisibilityCatalog,
  effectiveVisibleKeys,
  modelVisibilityKey,
  useModelVisibilityStore,
} from '../state/modelVisibility'

interface ModelPillProps {
  /** 生效模型 / session_model 覆盖态 / model_available / 模型目录的事实源 */
  llmSettings: LlmSettings
  codex: { connected: boolean; models: CodexModelInfo[]; loaded: boolean }
  disabled?: boolean
  /** D16：会话级切换（不写 config.toml）；失败抛错，菜单内原文展示 */
  onSwitch: (model: string) => Promise<void>
  /** 清除会话覆盖，回到 config 默认 */
  onReset: () => Promise<void>
  /** 打开设置抽屉（AI 面板默认展开，即可见性开关面板） */
  onEditVisibility: () => void
  /** 菜单打开时懒拉 codex 动态目录 */
  onOpen?: () => void
}

/** D16 对话框输入框下方的模型 pill（Hermes 式两层之二）：
 *  菜单只列可见模型（搜索时跨越可见性搜全部目录）；当前模型 pin 永远显示；
 *  切换只作用当前会话；覆盖态显示圆点 + 「恢复默认」。 */
export function ModelPill({ llmSettings, codex, disabled, onSwitch, onReset, onEditVisibility, onOpen }: ModelPillProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [switching, setSwitching] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [switchError, setSwitchError] = useState<string | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const storedKeys = useModelVisibilityStore((state) => state.keys)

  const effectiveModel = llmSettings.model || ''
  const overrideModel = llmSettings.session_model ?? null
  const modelAvailable = llmSettings.model_available ?? true

  const catalog = useMemo(() => buildVisibilityCatalog(llmSettings, codex), [llmSettings, codex])
  const storedSet = useMemo(() => (storedKeys === null ? null : new Set(storedKeys)), [storedKeys])
  const visibleSet = useMemo(() => effectiveVisibleKeys(storedSet, catalog), [storedSet, catalog])

  const q = query.trim().toLowerCase()

  // 当前模型 pin：永远显示（即使被隐藏或不在目录里）
  const currentEntry = useMemo(() => {
    if (!effectiveModel) return null
    for (const provider of catalog) {
      const found = provider.models.find((m) => m.id === effectiveModel)
      if (found) return { provider, entry: found }
    }
    return null
  }, [catalog, effectiveModel])

  // 浏览模式：只列可见模型；搜索模式：跨越可见性搜全部目录
  const groups = useMemo(() => {
    return catalog
      .map((provider) => {
        const models = provider.models.filter((m) => {
          if (m.id === effectiveModel) return false // 当前模型走 pin 行
          if (q) {
            return m.id.toLowerCase().includes(q) || m.label.toLowerCase().includes(q) || provider.slug.toLowerCase().includes(q)
          }
          return visibleSet.has(modelVisibilityKey(provider.slug, m.id))
        })
        return { provider, models }
      })
      .filter((group) => group.models.length > 0)
  }, [catalog, q, visibleSet, effectiveModel])

  function toggleOpen() {
    if (disabled) return
    setOpen((prev) => {
      const next = !prev
      if (next) {
        setSwitchError(null)
        onOpen?.()
        setTimeout(() => searchRef.current?.focus(), 0)
      }
      return next
    })
  }

  async function handleSwitch(model: string) {
    if (switching || model === effectiveModel) return
    setSwitching(true)
    setSwitchError(null)
    try {
      await onSwitch(model)
      setOpen(false)
      setQuery('')
    } catch (error) {
      // 切换失败错误原文显示（不静默失败）
      setSwitchError(error instanceof Error ? error.message : String(error))
    } finally {
      setSwitching(false)
    }
  }

  async function handleReset() {
    if (resetting) return
    setResetting(true)
    setSwitchError(null)
    try {
      await onReset()
    } catch (error) {
      setSwitchError(error instanceof Error ? error.message : String(error))
    } finally {
      setResetting(false)
    }
  }

  return (
    <div className="model-pill-wrap" data-testid="model-pill">
      {open ? (
        <div className="model-pill-menu" role="listbox" aria-label={t('assistant.modelPill.menuLabel')}>
          <input
            ref={searchRef}
            type="text"
            className="model-pill-search"
            placeholder={t('assistant.modelPill.searchPlaceholder')}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {currentEntry ? (
            <div className="model-pill-group">
              <button
                type="button"
                role="option"
                aria-selected
                className="model-pill-item is-current"
                title={currentEntry.entry.id}
                onClick={() => setOpen(false)}
              >
                {currentEntry.entry.label}
                <span className="model-pill-item-provider">{currentEntry.provider.label}</span>
              </button>
            </div>
          ) : null}
          {groups.length === 0 && !currentEntry ? (
            <p className="model-pill-empty">{t('settings.ai.noMatch')}</p>
          ) : null}
          {groups.map(({ provider, models }) => (
            <div className="model-pill-group" key={provider.slug}>
              <div className="model-pill-group-header">{provider.label}</div>
              {models.map((m) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={false}
                  className="model-pill-item"
                  key={m.id}
                  disabled={switching}
                  title={m.id}
                  onClick={() => void handleSwitch(m.id)}
                >
                  {m.label}
                  <span className="model-pill-item-provider">{provider.label}</span>
                </button>
              ))}
            </div>
          ))}
          {groups.length === 0 && currentEntry && q ? (
            <p className="model-pill-empty">{t('settings.ai.noMatch')}</p>
          ) : null}
          {switchError ? <p className="model-pill-error">{switchError}</p> : null}
          <button
            type="button"
            className="model-pill-edit"
            onClick={() => {
              setOpen(false)
              onEditVisibility()
            }}
          >
            {t('assistant.modelPill.editVisibility')}
          </button>
        </div>
      ) : null}
      <button
        type="button"
        className={`model-pill${modelAvailable ? '' : ' is-unavailable'}`}
        disabled={disabled}
        title={modelAvailable ? effectiveModel : `${effectiveModel} — ${t('assistant.modelPill.unavailable')}`}
        aria-expanded={open}
        onClick={toggleOpen}
      >
        {overrideModel ? <span className="model-pill-dot" aria-label={t('assistant.modelPill.override')} /> : null}
        {modelAvailable ? null : <span className="model-pill-warn">⚠</span>}
        <span className="model-pill-name">{effectiveModel || '—'}</span>
        <span className="model-pill-caret">▾</span>
      </button>
      {overrideModel ? (
        <button
          type="button"
          className="model-pill-reset"
          disabled={resetting || disabled}
          title={t('assistant.modelPill.override')}
          onClick={() => void handleReset()}
        >
          {resetting ? '…' : t('assistant.modelPill.resetDefault')}
        </button>
      ) : null}
    </div>
  )
}
