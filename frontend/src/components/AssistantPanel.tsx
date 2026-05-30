import { useState } from 'react'
import type { FormEvent } from 'react'
import type { AssistantMessage } from '../api/types'

interface AssistantPanelProps {
  messages: AssistantMessage[]
  busy: boolean
  onSend: (message: string) => void
  onCreate: (message: string) => void
  onGenerate: (message: string) => void
  onClearHistory: () => void
  onAdoptCode: (index: number) => void
}

export function AssistantPanel({ messages, busy, onSend, onCreate, onGenerate, onClearHistory, onAdoptCode }: AssistantPanelProps) {
  const [draft, setDraft] = useState('')
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
      onCreate(message)
    } else if (mode === 'generate') {
      onGenerate(message)
    } else {
      onSend(message)
    }
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
              <span>{message.role === 'user' ? '你' : 'OpenBrep'}</span>
              <p>{message.content}</p>
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
        <div className="assistant-actions">
          <button type="submit" disabled={busy || draft.trim().length === 0}>
            解释
          </button>
          <button type="button" disabled={busy || draft.trim().length === 0} onClick={() => sendDraft('create')}>
            新建项目
          </button>
          <button type="button" className="primary-action" disabled={busy || draft.trim().length === 0} onClick={() => sendDraft('generate')}>
            生成修改
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
