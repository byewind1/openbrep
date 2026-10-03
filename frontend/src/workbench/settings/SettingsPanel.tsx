import type { ReactNode } from 'react'
import { useT } from '../../i18n'

interface SettingsPanelProps {
  id: string
  title: string
  summary?: string
  modified?: boolean
  active: boolean
  visited: boolean
  children: ReactNode
}

/** Lazy first visit; keep draft-bearing panels alive when navigating away. */
export function SettingsPanel({ id, title, summary, modified, active, visited, children }: SettingsPanelProps) {
  const t = useT()
  return (
    <section className="settings-panel" data-section={id} hidden={!active}
      role="tabpanel" id={`settings-panel-${id}`} aria-labelledby={`settings-tab-${id}`}>
      {visited ? <>
        <div className="settings-panel-head">
          <h2>{title}</h2>
          {summary ? <span>{summary}</span> : null}
          {modified ? <em className="settings-dirty-state">{t('settings.header.unsaved')}</em> : null}
        </div>
        <div className="settings-section-body">{children}</div>
      </> : null}
    </section>
  )
}
