import { useState } from 'react'
import type { ProjectRevision } from '../../api/types'
import { useThemedDialog } from '../../components/ThemedDialog'
import { useT } from '../../i18n'

interface RevisionPanelProps {
  revisions: ProjectRevision[]
  latestRevisionId: string | null
  loading: boolean
  /** 是否存在未保存脚本/参数草稿（ST03 恢复前草稿保护） */
  hasUnsavedDrafts?: boolean
  onSave: (message: string) => void
  /** draftPolicy 由本面板在确认后传入：discard / keep */
  onRestore: (revisionId: string, options?: { draftPolicy: 'discard' | 'keep' }) => void
}

export function RevisionPanel({
  revisions,
  latestRevisionId,
  loading,
  hasUnsavedDrafts = false,
  onSave,
  onRestore,
}: RevisionPanelProps) {
  const t = useT()
  const [message, setMessage] = useState('')
  const { confirm, dialogNode } = useThemedDialog()

  function saveRevision() {
    onSave(message)
    setMessage('')
  }

  async function restoreRevision(revisionId: string) {
    // ST03：恢复前走草稿保护；取消则项目与草稿都不变
    if (hasUnsavedDrafts) {
      const keep = await confirm({
        title: t('delivery.recover.keepTitle'),
        message: t('delivery.recover.keepMessage', { revision: revisionId }),
        confirmLabel: t('delivery.recover.keepOk'),
      })
      if (keep) {
        onRestore(revisionId, { draftPolicy: 'keep' })
        return
      }
      const discard = await confirm({
        title: t('delivery.recover.confirmTitle'),
        message: t('delivery.recover.confirmMessage', { revision: revisionId }),
        confirmLabel: t('delivery.recover.confirmOk'),
        danger: true,
      })
      if (!discard) return
      onRestore(revisionId, { draftPolicy: 'discard' })
      return
    }
    const ok = await confirm({
      title: 'Restore revision',
      message: `Restore ${revisionId}? Current source files will be replaced.`,
      danger: true,
    })
    if (!ok) return
    onRestore(revisionId, { draftPolicy: 'discard' })
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
              {revision.delivery?.run_id ? (
                <span className="revision-delivery-run" title={revision.delivery.run_id}>
                  run {String(revision.delivery.run_id).slice(-8)}
                </span>
              ) : null}
              <button type="button" disabled={loading} onClick={() => void restoreRevision(revision.revision_id)}>
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
