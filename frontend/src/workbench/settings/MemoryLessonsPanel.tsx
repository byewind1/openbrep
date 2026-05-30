import type { ErrorLesson, ProjectMemoryStatus } from '../../api/types'

interface MemoryLessonsPanelProps {
  memoryStatus: ProjectMemoryStatus | null
  lessons: ErrorLesson[]
  skillPreview: string
  busy: boolean
  formatBytes: (bytes: number) => string
  onRefresh: () => void
  onSummarize: () => void
  onClear: () => void
}

export function MemoryLessonsPanel({
  memoryStatus,
  lessons,
  skillPreview,
  busy,
  formatBytes,
  onRefresh,
  onSummarize,
  onClear,
}: MemoryLessonsPanelProps) {
  return (
    <section className="settings-section memory-lessons-panel">
      <div className="settings-section-heading">
        <strong>Memory</strong>
        <span>Project chat and learning records</span>
      </div>
      <div className="memory-status-grid">
        <span>Chats</span>
        <strong>{memoryStatus?.chat_count ?? 0}</strong>
        <span>Lessons</span>
        <strong>{memoryStatus?.lesson_count ?? lessons.length}</strong>
        <span>Size</span>
        <strong>{formatBytes(memoryStatus?.total_bytes ?? 0)}</strong>
        <span>Skill</span>
        <strong>{memoryStatus?.has_learned_skill ? 'Yes' : 'No'}</strong>
      </div>
      <div className="settings-path-note" title={memoryStatus?.memory_root || ''}>
        {memoryStatus?.memory_root || 'No project memory directory'}
      </div>
      <div className="settings-submit-row memory-actions">
        <button type="button" onClick={onRefresh} disabled={busy}>
          Refresh
        </button>
        <button type="button" onClick={onSummarize} disabled={busy}>
          {busy ? 'Summarizing' : 'Summarize'}
        </button>
        <button type="button" className="danger-action" onClick={onClear} disabled={busy}>
          Clear
        </button>
      </div>

      <div className="memory-lessons-list" aria-label="Project memory lessons">
        {lessons.length === 0 ? (
          <span className="settings-empty">No learned error lessons</span>
        ) : (
          lessons.slice(0, 8).map((lesson) => (
            <article className="memory-lesson-item" key={lesson.fingerprint}>
              <div className="memory-lesson-meta">
                <span>{lesson.category || 'general'}</span>
                <strong>{lesson.count}x</strong>
              </div>
              <p>{lesson.summary}</p>
              {lesson.guidance ? <small>{lesson.guidance}</small> : null}
            </article>
          ))
        )}
      </div>

      {skillPreview ? (
        <details className="memory-skill-preview">
          <summary>Skill preview</summary>
          <pre>{skillPreview}</pre>
        </details>
      ) : null}
    </section>
  )
}
