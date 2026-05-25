import { useState } from 'react'
import type { FormEvent } from 'react'
import type { AssistantMessage } from '../api/types'

interface AssistantPanelProps {
  messages: AssistantMessage[]
  busy: boolean
  onSend: (message: string) => void
  onGenerate: (message: string) => void
}

export function AssistantPanel({ messages, busy, onSend, onGenerate }: AssistantPanelProps) {
  const [draft, setDraft] = useState('')

  function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    sendDraft('explain')
  }

  function sendDraft(mode: 'explain' | 'generate') {
    const message = draft.trim()
    if (!message) return
    setDraft('')
    if (mode === 'generate') {
      onGenerate(message)
    } else {
      onSend(message)
    }
  }

  return (
    <aside className="assistant-panel">
      <div className="panel-heading">
        <h2>AI 助手</h2>
        <span>{busy ? 'Working' : 'Ready'}</span>
      </div>
      <div className="assistant-thread">
        {messages.length ? (
          messages.map((message, index) => (
            <article className={`assistant-message ${message.role}`} key={`${message.role}-${index}`}>
              <span>{message.role === 'user' ? '你' : 'OpenBrep'}</span>
              <p>{message.content}</p>
            </article>
          ))
        ) : (
          <p className="assistant-empty">No messages</p>
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
          <button type="button" className="primary-action" disabled={busy || draft.trim().length === 0} onClick={() => sendDraft('generate')}>
            生成修改
          </button>
        </div>
      </form>
    </aside>
  )
}
