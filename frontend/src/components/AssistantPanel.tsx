import { useState } from 'react'
import type { FormEvent } from 'react'
import type { AssistantImageAttachment, AssistantMessage } from '../api/types'
import { validateAssistantImageFile } from './assistantImage'

interface AssistantPanelProps {
  messages: AssistantMessage[]
  busy: boolean
  onSend: (message: string) => void
  onCreate: (message: string, image?: AssistantImageAttachment | null) => void
  onGenerate: (message: string, image?: AssistantImageAttachment | null) => void
  onClearHistory: () => void
  onAdoptCode: (index: number) => void
  onOpenScript?: (scriptName: string) => void
  onSaveRevision?: (message: string) => void
}

export function AssistantPanel({
  messages,
  busy,
  onSend,
  onCreate,
  onGenerate,
  onClearHistory,
  onAdoptCode,
  onOpenScript,
  onSaveRevision,
}: AssistantPanelProps) {
  const [draft, setDraft] = useState('')
  const [image, setImage] = useState<AssistantImageAttachment | null>(null)
  const [imageError, setImageError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)

  function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    sendDraft('explain')
  }

  function sendDraft(mode: 'explain' | 'create' | 'generate') {
    const message = draft.trim()
    if (!message) return
    setDraft('')
    if (mode === 'create') {
      onCreate(message, image)
      setImage(null)
    } else if (mode === 'generate') {
      onGenerate(message, image)
      setImage(null)
    } else {
      onSend(message)
    }
  }

  function attachImage(file: File | null) {
    setImageError('')
    if (!file) return
    const error = validateAssistantImageFile(file)
    if (error) {
      setImageError(error)
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      const comma = result.indexOf(',')
      setImage({
        name: file.name,
        mime: file.type || 'image/png',
        b64: comma >= 0 ? result.slice(comma + 1) : result,
      })
    }
    reader.onerror = () => setImageError('Image read failed')
    reader.readAsDataURL(file)
  }

  return (
    <aside className="assistant-panel">
      <div className="panel-heading">
        <h2>AI</h2>
        <div className="assistant-heading-actions">
          <span>{busy ? 'Working' : 'Ready'}</span>
          <button type="button" disabled={messages.length === 0} onClick={() => setHistoryOpen(true)}>
            History
          </button>
          <button type="button" disabled={busy || messages.length === 0} onClick={onClearHistory}>
            Clear
          </button>
        </div>
      </div>
      <div className="assistant-thread">
        {messages.length ? (
          messages.map((message, index) => (
            <article className={`assistant-message ${message.role}`} key={`${message.role}-${index}`}>
              <span>
                {message.role === 'user' ? '你' : 'OpenBrep'}
                {message.errorCategory ? (
                  <em className={`assistant-error-badge assistant-error-${message.errorCategory}`}>
                    {errorCategoryLabel(message.errorCategory)}
                  </em>
                ) : null}
              </span>
              <p>{message.content}</p>
              {message.changedFiles?.length ? (
                <div className="assistant-change-card">
                  <strong>Changed files</strong>
                  <div className="assistant-change-files">
                    {message.changedFiles.map((file) => (
                      <button
                        type="button"
                        key={file}
                        disabled={busy || !onOpenScript}
                        title={`Open ${file} in the editor`}
                        onClick={() => onOpenScript?.(file.split('/').pop() ?? file)}
                      >
                        {file}
                      </button>
                    ))}
                  </div>
                  {onSaveRevision ? (
                    <button
                      type="button"
                      className="assistant-save-revision"
                      disabled={busy}
                      onClick={() => onSaveRevision(revisionMessageFor(messages, index))}
                    >
                      Save revision
                    </button>
                  ) : null}
                </div>
              ) : null}
              {message.role === 'assistant' && message.content.includes('```') ? (
                <button type="button" disabled={busy} onClick={() => onAdoptCode(index)}>
                  Adopt code
                </button>
              ) : null}
            </article>
          ))
        ) : (
          <p className="assistant-empty">Ready</p>
        )}
      </div>
      <form className="assistant-input" onSubmit={submitMessage}>
        <textarea
          rows={3}
          placeholder="Ask or generate..."
          value={draft}
          onChange={(event) => setDraft(event.currentTarget.value)}
        />
        <div className="assistant-attachment-row">
          <label className="assistant-attach-button">
            Attach image
            <input
              type="file"
              aria-label="Attach image"
              accept="image/png,image/jpeg,image/webp"
              disabled={busy}
              onChange={(event) => attachImage(event.currentTarget.files?.[0] ?? null)}
            />
          </label>
          {image ? (
            <button
              type="button"
              className="assistant-image-chip"
              disabled={busy}
              aria-label={`Remove image ${image.name}`}
              onClick={() => setImage(null)}
            >
              {image.name}
            </button>
          ) : (
            <span>{imageError || 'No image'}</span>
          )}
        </div>
        <div className="assistant-actions">
          <button type="submit" disabled={busy || draft.trim().length === 0}>
            Explain
          </button>
          <button type="button" disabled={busy || draft.trim().length === 0} onClick={() => sendDraft('create')}>
            New project
          </button>
          <button type="button" className="primary-action" disabled={busy || draft.trim().length === 0} onClick={() => sendDraft('generate')}>
            Generate changes
          </button>
        </div>
      </form>
      <AssistantHistoryDrawer
        open={historyOpen}
        messages={messages}
        busy={busy}
        onClose={() => setHistoryOpen(false)}
        onAdoptCode={onAdoptCode}
      />
    </aside>
  )
}

function errorCategoryLabel(category: NonNullable<AssistantMessage['errorCategory']>) {
  if (category === 'llm') return 'LLM settings'
  if (category === 'compile') return 'Compile'
  return 'Error'
}

// revision 信息取触发本次生成的用户指令（往前找最近一条 user 消息），截断防止过长
function revisionMessageFor(messages: AssistantMessage[], assistantIndex: number) {
  for (let index = assistantIndex - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'user') {
      const instruction = messages[index].content.trim()
      return `AI: ${instruction.length > 60 ? `${instruction.slice(0, 60)}…` : instruction}`
    }
  }
  return 'AI generated changes'
}

function AssistantHistoryDrawer({
  open,
  messages,
  busy,
  onClose,
  onAdoptCode,
}: {
  open: boolean
  messages: AssistantMessage[]
  busy: boolean
  onClose: () => void
  onAdoptCode: (index: number) => void
}) {
  if (!open) return null

  return (
    <>
      <button className="history-scrim" type="button" aria-label="Close assistant history" onClick={onClose} />
      <aside className="assistant-history-drawer" role="dialog" aria-label="Assistant history">
        <div className="history-header">
          <div>
            <strong>History</strong>
            <span>{messages.length} messages</span>
          </div>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="history-list">
          {messages.map((message, index) => (
            <article className={`history-message ${message.role}`} key={`${message.role}-${index}`}>
              <div className="history-message-meta">
                <span>{message.role === 'user' ? '你' : 'OpenBrep'}</span>
                <em>#{index + 1}</em>
              </div>
              <p>{message.content}</p>
              {message.role === 'assistant' && message.content.includes('```') ? (
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Adopt code from message ${index + 1}`}
                  onClick={() => onAdoptCode(index)}
                >
                  Adopt code
                </button>
              ) : null}
            </article>
          ))}
        </div>
      </aside>
    </>
  )
}
