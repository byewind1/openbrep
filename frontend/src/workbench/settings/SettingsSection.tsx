import type { ReactNode } from 'react'

interface SettingsSectionProps {
  id: string
  title: string
  summary?: string
  modified?: boolean
  expanded: boolean
  onToggle: (id: string) => void
  children: ReactNode
}

export function SettingsSection({ id, title, summary, modified = false, expanded, onToggle, children }: SettingsSectionProps) {
  return (
    <section className={`settings-section${expanded ? ' expanded' : ' collapsed'}`} data-section={id}>
      <button
        type="button"
        className="settings-section-toggle"
        aria-expanded={expanded}
        aria-controls={`settings-section-${id}`}
        onClick={() => onToggle(id)}
      >
        <span>
          <strong>{title}</strong>
          {summary ? <small>{summary}</small> : null}
        </span>
        {modified ? <em>Modified</em> : null}
      </button>
      {expanded ? (
        <div className="settings-section-body" id={`settings-section-${id}`}>
          {children}
        </div>
      ) : null}
    </section>
  )
}
