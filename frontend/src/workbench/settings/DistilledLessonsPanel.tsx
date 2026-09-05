import type { DistilledLesson, LessonEvidenceRef } from '../../api/types'
import { useT, type LocaleKey } from '../../i18n'

interface DistilledLessonsPanelProps {
  lessons: DistilledLesson[]
  busy: boolean
  projectName: string | null
  message: { kind: 'error' | 'info'; text: string } | null
  onRefresh: () => void
  onDistill: () => void
  onSetStatus: (fingerprint: string, decision: 'promote' | 'reject' | 'demote') => void
}

/** G4 蒸馏教训确认卡（lesson ≠ skill）：proposed 待审 → 采纳(promote)/忽略(reject)；
 *  active 可撤回(demote)。证据只展示 run/check/revision 引用，不展示脚本内容。 */
export function DistilledLessonsPanel({
  lessons,
  busy,
  projectName,
  message,
  onRefresh,
  onDistill,
  onSetStatus,
}: DistilledLessonsPanelProps) {
  const t = useT()
  return (
    <div className="distilled-lessons-panel">
      {projectName ? (
        <div className="settings-path-note" title={projectName}>
          {t('lessons.project', { name: projectName })}
        </div>
      ) : null}
      <div className="settings-submit-row distilled-lessons-toolbar">
        <button type="button" onClick={onRefresh} disabled={busy}>
          {t('lessons.refresh')}
        </button>
        <button type="button" onClick={onDistill} disabled={busy}>
          {busy ? t('lessons.distilling') : t('lessons.distill')}
        </button>
      </div>
      {message ? (
        <div className={`distilled-lessons-message ${message.kind}`} role="status">
          {message.text}
        </div>
      ) : null}
      <div className="distilled-lessons-list" aria-label={t('lessons.ariaList')}>
        {lessons.length === 0 ? (
          <span className="settings-empty">{t('lessons.empty')}</span>
        ) : (
          lessons.map((lesson) => (
            <DistilledLessonCard
              key={lesson.fingerprint}
              lesson={lesson}
              busy={busy}
              onSetStatus={onSetStatus}
            />
          ))
        )}
      </div>
    </div>
  )
}

function DistilledLessonCard({
  lesson,
  busy,
  onSetStatus,
}: {
  lesson: DistilledLesson
  busy: boolean
  onSetStatus: (fingerprint: string, decision: 'promote' | 'reject' | 'demote') => void
}) {
  const t = useT()
  const status = lesson.status ?? 'proposed'
  const statusKey = lessonStatusKey(status)
  const refs = lesson.evidence_refs ?? []
  return (
    <article className="distilled-lesson-card" data-status={status}>
      <div className="distilled-lesson-meta">
        {statusKey ? <span className="distilled-lesson-status">{t(statusKey)}</span> : <span />}
        <span className="distilled-lesson-count" title={lesson.fingerprint}>
          {t('lessons.count', { count: lesson.count ?? 1 })}
        </span>
      </div>
      <strong className="distilled-lesson-pattern">{lesson.pattern}</strong>
      {lesson.guidance ? <p className="distilled-lesson-guidance">{lesson.guidance}</p> : null}
      {refs.length > 0 ? (
        <div className="distilled-lesson-evidence">
          <span className="distilled-lesson-evidence-label">{t('lessons.evidence')}</span>
          {refs.map((ref) => (
            <div className="distilled-lesson-evidence-row" key={`${ref.run_id}:${ref.check_type}`}>
              <code className="distilled-lesson-check">{ref.check_type}</code>
              <code className="distilled-lesson-run" title={ref.run_id}>
                {ref.run_id}
              </code>
              {revisionText(ref) ? <span className="distilled-lesson-revs">{revisionText(ref)}</span> : null}
            </div>
          ))}
        </div>
      ) : null}
      {status === 'proposed' ? (
        <div className="distilled-lesson-actions">
          <button
            type="button"
            className="distilled-lesson-approve"
            onClick={() => onSetStatus(lesson.fingerprint, 'promote')}
            disabled={busy}
          >
            {t('lessons.approve')}
          </button>
          <button
            type="button"
            className="distilled-lesson-ignore"
            onClick={() => onSetStatus(lesson.fingerprint, 'reject')}
            disabled={busy}
          >
            {t('lessons.ignore')}
          </button>
        </div>
      ) : null}
      {status === 'active' ? (
        <div className="distilled-lesson-actions">
          <button
            type="button"
            className="distilled-lesson-demote"
            onClick={() => onSetStatus(lesson.fingerprint, 'demote')}
            disabled={busy}
          >
            {t('lessons.demote')}
          </button>
        </div>
      ) : null}
    </article>
  )
}

function lessonStatusKey(status: string): LocaleKey | null {
  if (status === 'proposed') return 'lessons.statusProposed'
  if (status === 'active') return 'lessons.statusActive'
  if (status === 'rejected') return 'lessons.statusRejected'
  return null
}

function revisionText(ref: LessonEvidenceRef): string {
  const before = ref.before_revision
  const after = ref.after_revision
  if (before && after) return `${before} → ${after}`
  return before || after || ''
}
