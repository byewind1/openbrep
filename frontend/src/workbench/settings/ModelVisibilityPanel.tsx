import { useMemo, useState } from 'react'
import type { LlmSettings, CodexModelInfo } from '../../api/types'
import { useT } from '../../i18n'
import {
  buildVisibilityCatalog,
  effectiveVisibleKeys,
  modelVisibilityKey,
  toggleModelVisibility,
  useModelVisibilityStore,
  type VisibilityProvider,
} from '../../state/modelVisibility'

interface ModelVisibilityPanelProps {
  llmSettings: Pick<LlmSettings, 'model_groups'>
  codex: { connected: boolean; models: CodexModelInfo[] }
  query: string
  currentId: string
  pendingModel: string | null
  disabled: boolean
  /** 点击模型名（非开关）→ 既有「设为默认」确认流；kind 区分 codex 走 codex 确认流 */
  onSelect: (model: string, kind: VisibilityProvider['kind']) => void
}

/** D16 设置页模型可见性开关面板（Hermes 式两层之一）：
 *  按 provider 分组、每模型一个开关；开关只管可见性（localStorage），
 *  绝不触发模型切换或配置写入；点击模型名走既有确认流显式保存默认。 */
export function ModelVisibilityPanel({
  llmSettings,
  codex,
  query,
  currentId,
  pendingModel,
  disabled,
  onSelect,
}: ModelVisibilityPanelProps) {
  const t = useT()
  const storedKeys = useModelVisibilityStore((state) => state.keys)
  const setKeys = useModelVisibilityStore((state) => state.setKeys)
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())

  const catalog = useMemo(() => buildVisibilityCatalog(llmSettings, codex), [llmSettings, codex])
  const storedSet = useMemo(() => (storedKeys === null ? null : new Set(storedKeys)), [storedKeys])
  const visibleSet = useMemo(() => effectiveVisibleKeys(storedSet, catalog), [storedSet, catalog])

  const q = query.trim().toLowerCase()
  const matches = (provider: VisibilityProvider, label: string) =>
    !q || label.toLowerCase().includes(q) || provider.slug.toLowerCase().includes(q)

  const sections = [
    { id: 'custom', label: t('settings.ai.groupCustom'), providers: catalog.filter((p) => p.kind === 'custom') },
    { id: 'official', label: t('settings.ai.groupOfficial'), providers: catalog.filter((p) => p.kind === 'official') },
    { id: 'codex', label: t('settings.ai.codex.sectionTitle'), providers: catalog.filter((p) => p.kind === 'codex') },
  ]
    .map((section) => ({
      ...section,
      providers: section.providers
        .map((provider) => ({
          provider,
          models: provider.models.filter((m) => matches(provider, m.label) || matches(provider, m.id)),
        }))
        .filter((entry) => entry.models.length > 0),
    }))
    .filter((section) => section.providers.length > 0)

  function toggleCollapse(slug: string) {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(slug)) next.delete(slug)
      else next.add(slug)
      return next
    })
  }

  if (sections.length === 0) {
    return <p className="settings-test-result">{t('settings.ai.noMatch')}</p>
  }

  return (
    <div data-testid="model-visibility-panel">
      <p className="settings-hint">{t('settings.ai.visibility.hint')}</p>
      {sections.map((section) => (
        <div className="settings-visibility-section" key={section.id}>
          <div className="settings-row-header">{section.label}</div>
          {section.providers.map(({ provider, models }) => {
            const isCollapsed = collapsed.has(provider.slug)
            const visibleCount = provider.models.filter(
              (m) => visibleSet.has(modelVisibilityKey(provider.slug, m.id)),
            ).length
            return (
              <div className="settings-visibility-group" key={provider.slug}>
                <button
                  type="button"
                  className="settings-visibility-group-header"
                  aria-expanded={!isCollapsed}
                  onClick={() => toggleCollapse(provider.slug)}
                >
                  {isCollapsed ? '▸' : '▾'} {provider.label}
                  <span className="settings-hint">
                    {t('settings.ai.visibility.count', { visible: visibleCount, total: provider.models.length })}
                  </span>
                </button>
                {!isCollapsed ? (
                  <div className="settings-model-list">
                    {models.map((m) => {
                      const key = modelVisibilityKey(provider.slug, m.id)
                      const isVisible = visibleSet.has(key)
                      const isCurrent = m.id === currentId
                      return (
                        <div className="settings-visibility-row" key={m.id}>
                          <button
                            type="button"
                            role="switch"
                            aria-checked={isVisible}
                            aria-label={t('settings.ai.visibility.toggle', { model: m.id })}
                            className={`visibility-toggle${isVisible ? ' is-on' : ''}`}
                            onClick={() =>
                              setKeys(toggleModelVisibility(storedSet, catalog, provider.slug, m.id))
                            }
                          />
                          <button
                            type="button"
                            className={isCurrent ? 'active' : m.id === pendingModel ? 'pending' : ''}
                            disabled={disabled}
                            title={m.label !== m.id ? `${m.label} (${m.id})` : m.id}
                            onClick={() => onSelect(m.id, provider.kind)}
                          >
                            {m.id}
                          </button>
                          {isCurrent ? (
                            <span className="settings-visibility-current">
                              {t('settings.ai.visibility.current')}
                            </span>
                          ) : null}
                        </div>
                      )
                    })}
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}
