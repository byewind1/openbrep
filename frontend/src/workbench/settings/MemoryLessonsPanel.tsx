import { useState } from 'react'
import type { ErrorLesson, ProjectMemoryStatus, UpdateMemoryLessonRequest } from '../../api/types'

interface MemoryLessonsPanelProps {
  memoryStatus: ProjectMemoryStatus | null
  lessons: ErrorLesson[]
  skillPreview: string
  busy: boolean
  formatBytes: (bytes: number) => string
  onRefresh: () => void
  onSummarize: () => void
  onUpdateLesson: (fingerprint: string, updates: UpdateMemoryLessonRequest) => void
  onDeleteLesson: (fingerprint: string) => void
  onIgnoreLesson: (fingerprint: string) => void
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
  onUpdateLesson,
  onDeleteLesson,
  onIgnoreLesson,
  onClear,
}: MemoryLessonsPanelProps) {
  const [editingFingerprint, setEditingFingerprint] = useState<string | null>(null)
  const [draft, setDraft] = useState<UpdateMemoryLessonRequest>({})

  function startEdit(lesson: ErrorLesson) {
    setEditingFingerprint(lesson.fingerprint)
    setDraft({
      category: lesson.category,
      summary: lesson.summary,
      guidance: lesson.guidance,
      example: lesson.example,
    })
  }

  function cancelEdit() {
    setEditingFingerprint(null)
    setDraft({})
  }

  function submitEdit(fingerprint: string) {
    onUpdateLesson(fingerprint, draft)
    cancelEdit()
  }

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
                <div className="memory-lesson-controls">
                  <strong>{lesson.count}x</strong>
                  <button
                    type="button"
                    className="memory-lesson-edit"
                    onClick={() => startEdit(lesson)}
                    disabled={busy}
                    aria-label={`Edit lesson ${lesson.category}`}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="memory-lesson-ignore"
                    onClick={() => onIgnoreLesson(lesson.fingerprint)}
                    disabled={busy}
                    aria-label={`Ignore lesson ${lesson.category}`}
                  >
                    Ignore
                  </button>
                  <button
                    type="button"
                    className="memory-lesson-delete"
                    onClick={() => onDeleteLesson(lesson.fingerprint)}
                    disabled={busy}
                    aria-label={`Delete lesson ${lesson.category}`}
                  >
                    Delete
                  </button>
                </div>
              </div>
              <p>{lesson.summary}</p>
              {lesson.guidance ? <small>{lesson.guidance}</small> : null}
              {editingFingerprint === lesson.fingerprint ? (
                <div className="memory-lesson-editor">
                  <input
                    type="text"
                    value={draft.category ?? ''}
                    placeholder="Category"
                    onChange={(event) => setDraft({ ...draft, category: event.currentTarget.value })}
                  />
                  <textarea
                    value={draft.summary ?? ''}
                    placeholder="Summary"
                    rows={2}
                    onChange={(event) => setDraft({ ...draft, summary: event.currentTarget.value })}
                  />
                  <textarea
                    value={draft.guidance ?? ''}
                    placeholder="Guidance"
                    rows={3}
                    onChange={(event) => setDraft({ ...draft, guidance: event.currentTarget.value })}
                  />
                  <textarea
                    value={draft.example ?? ''}
                    placeholder="Example"
                    rows={2}
                    onChange={(event) => setDraft({ ...draft, example: event.currentTarget.value })}
                  />
                  <div className="memory-lesson-editor-actions">
                    <button type="button" onClick={() => submitEdit(lesson.fingerprint)} disabled={busy}>
                      Save
                    </button>
                    <button type="button" onClick={cancelEdit} disabled={busy}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : null}
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
