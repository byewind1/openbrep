import { useState } from 'react'
import type { FormEvent } from 'react'
import type { AssistantMessage } from '../api/types'

interface AssistantPanelProps {
  messages: AssistantMessage[]
  busy: boolean
  onSend: (message: string) => void
}

export function AssistantPanel({ messages, busy, onSend }: AssistantPanelProps) {
  const [draft, setDraft] = useState('')

  function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const message = draft.trim()
    if (!message) return
    setDraft('')
    onSend(message)
  }

  return (
    <aside className="assistant-panel">
      <div className="panel-heading">
        <h2>AI 助手</h2>
        <span>{busy ? '分析中' : 'Explain'}</span>
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
          <p className="assistant-empty">加载 HSF 后，可以问我解释构件、分析 3D 脚本，或说明某个参数。</p>
        )}
      </div>
      <form className="assistant-input" onSubmit={submitMessage}>
        <textarea
          rows={3}
          placeholder="例如：详细解释 A 参数"
          value={draft}
          onChange={(event) => setDraft(event.currentTarget.value)}
        />
        <button type="submit" disabled={busy || draft.trim().length === 0}>
          发送
        </button>
      </form>
    </aside>
  )
}
