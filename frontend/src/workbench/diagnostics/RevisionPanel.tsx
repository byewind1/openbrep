import { useState } from 'react'
import type { ProjectRevision } from '../../api/types'

interface RevisionPanelProps {
  revisions: ProjectRevision[]
  latestRevisionId: string | null
  loading: boolean
  onSave: (message: string) => void
  onRestore: (revisionId: string) => void
}

export function RevisionPanel({
  revisions,
  latestRevisionId,
  loading,
  onSave,
  onRestore,
}: RevisionPanelProps) {
  const [message, setMessage] = useState('')

  function saveRevision() {
    onSave(message)
    setMessage('')
  }

  function restoreRevision(revisionId: string) {
    if (!window.confirm(`Restore ${revisionId}? Current source files will be replaced.`)) return
    onRestore(revisionId)
  }

  return (
    <div className="revision-panel">
      <div className="revision-actions">
        <input
          type="text"
          placeholder="Revision message"
          value={message}
          onChange={(event) => setMessage(event.currentTarget.value)}
        />
        <button type="button" disabled={loading} onClick={saveRevision}>
          Save Revision
        </button>
      </div>
      {revisions.length === 0 ? <p>暂无版本</p> : null}
      <div className="revision-list">
        {revisions.map((revision) => (
          <article className="revision-item" key={revision.revision_id}>
            <div>
              <strong>
                {revision.revision_id}
                {revision.revision_id === latestRevisionId || revision.is_latest ? ' *' : ''}
              </strong>
              <span>{revision.created_at}</span>
            </div>
            <p>{revision.message || revision.user_instruction || revision.trigger}</p>
            <footer>
              <span>{revision.file_count} files</span>
              <button type="button" disabled={loading} onClick={() => restoreRevision(revision.revision_id)}>
                Restore
              </button>
            </footer>
          </article>
        ))}
      </div>
    </div>
  )
}
