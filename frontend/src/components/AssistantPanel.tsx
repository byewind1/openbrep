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
    </aside>
  )
}
