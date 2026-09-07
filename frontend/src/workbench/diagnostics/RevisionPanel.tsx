import { useState } from 'react'
import type { ProjectRevision } from '../../api/types'
import { useThemedDialog } from '../../components/ThemedDialog'

interface RevisionPanelProps {
  revisions: ProjectRevision[]
  latestRevisionId: string | null
  loading: boolean
  /** SF1：返回是否成功；仅成功后组件清空 message 输入 */
  onSave: (message: string) => Promise<boolean> | boolean
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
  const { confirm, dialogNode } = useThemedDialog()

  async function saveRevision() {
    const ok = await onSave(message)
    // SF1：仅成功后清空输入；失败保留用户填写的版本说明
    if (ok) setMessage('')
  }

  async function restoreRevision(revisionId: string) {
    const ok = await confirm({
      title: 'Restore revision',
      message: `Restore ${revisionId}? Current source files will be replaced. Unsaved script edits and parameter drafts will also be discarded.`,
      danger: true,
    })
    if (!ok) return
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
      {dialogNode}
    </div>
  )
}
