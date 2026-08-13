// CopilotPage：Archicad 窄面板用的精简 GDL 对话页（?mode=copilot）。
//
// 功能集移植自 ADDON 仓 copilot/index.html：消息列表、gdl 代码块复制、
// 剪贴板错误收集（chips + 汇总 + 清空）、图片附件、升级提示横幅。
// 不进 workbench store，直接 fetch 相对路径 /api/copilot/*。
// 错误一律以横幅展示（禁止静默失败）。

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  clearClipboardBuffer,
  fetchClipboardBuffer,
  fetchCopilotStatus,
  getAddonVersionFromUrl,
  isVersionAtLeast,
  sendCopilotChat,
  summarizeClipboardErrors,
  type CopilotHistoryItem,
} from './copilotApi'
import './CopilotPage.css'

const WELCOME_MESSAGE =
  '你好！把 Archicad 的编译报错或有问题的 GDL 代码粘进来，我帮你修。也可以直接粘贴截图或点击📎上传图片。'
const MAX_IMAGES = 3
const CLIPBOARD_POLL_MS = 3000
const GDL_FENCE = /```gdl\s*([\s\S]*?)```/gi

export interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  images?: { dataUrl: string; b64: string; mime: string; name: string }[]
}

export interface MessageSegment {
  type: 'text' | 'code'
  content: string
}

/** 把 assistant 回复拆成文本段与 ```gdl 代码段（与参考 UI 同一正则语义）。 */
export function parseMessageWithCodeBlocks(text: string): MessageSegment[] {
  const segments: MessageSegment[] = []
  let lastIndex = 0
  GDL_FENCE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = GDL_FENCE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', content: text.slice(lastIndex, match.index) })
    }
    segments.push({ type: 'code', content: (match[1] || '').trim() })
    lastIndex = GDL_FENCE.lastIndex
  }
  if (lastIndex < text.length) {
    segments.push({ type: 'text', content: text.slice(lastIndex) })
  }
  if (segments.length === 0) {
    segments.push({ type: 'text', content: text })
  }
  return segments
}

async function copyTextToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // fall through to legacy execCommand path
  }
  try {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(textarea)
    return ok
  } catch {
    return false
  }
}

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState<'idle' | 'copied' | 'failed'>('idle')
  return (
    <div className="copilot-code">
      <pre>{code}</pre>
      <button
        type="button"
        className="copilot-copy-btn"
        disabled={copied === 'copied'}
        onClick={() => {
          void copyTextToClipboard(code).then((ok) => {
            setCopied(ok ? 'copied' : 'failed')
            window.setTimeout(() => setCopied('idle'), 2000)
          })
        }}
      >
        {copied === 'copied' ? '✓ 已复制' : copied === 'failed' ? '复制失败' : '复制'}
      </button>
    </div>
  )
}

function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className="copilot-msg-wrap copilot-msg-user">
        <div className="copilot-msg">
          {message.images && message.images.length > 0 && (
            <div className="copilot-msg-images">
              {message.images.map((img, i) => (
                <img key={`${img.name}-${i}`} className="copilot-msg-image" src={img.dataUrl} alt={img.name || `图片${i + 1}`} />
              ))}
            </div>
          )}
          {message.text && <div>{message.text}</div>}
        </div>
      </div>
    )
  }
  return (
    <div className="copilot-msg-wrap copilot-msg-assistant">
      <div className="copilot-msg">
        {parseMessageWithCodeBlocks(message.text).map((segment, i) => {
          if (segment.type === 'code') {
            return <CodeBlock key={`code-${i}`} code={segment.content} />
          }
          return segment.content.trim() ? <div key={`text-${i}`}>{segment.content}</div> : null
        })}
      </div>
    </div>
  )
}

interface ClipboardCollectorProps {
  items: string[]
  expanded: boolean
  busy: boolean
  summarizing: boolean
  onToggle: () => void
  onUseItem: (item: string) => void
  onRemoveItem: (index: number) => void
  onClear: () => void
  onSummarize: () => void
}

function ClipboardCollector({
  items,
  expanded,
  busy,
  summarizing,
  onToggle,
  onUseItem,
  onRemoveItem,
  onClear,
  onSummarize,
}: ClipboardCollectorProps) {
  return (
    <div className="copilot-collector">
      <button type="button" className={`copilot-collector-toggle${items.length ? ' has-items' : ''}`} onClick={onToggle}>
        📋 错误收集区（{items.length}）
      </button>
      {expanded && (
        <div className="copilot-collector-body">
          {items.length === 0 ? (
            <div className="copilot-collector-empty">暂无捕获的错误，在 Archicad 中复制报错文本后会自动出现在这里。</div>
          ) : (
            <div className="copilot-collector-list">
              {items.map((item, i) => (
                <div key={`${i}-${item}`} className="copilot-collector-item">
                  <button type="button" className="copilot-chip" title="填入输入框" onClick={() => onUseItem(item)}>
                    {item}
                  </button>
                  <button type="button" className="copilot-chip-remove" title="删除" onClick={() => onRemoveItem(i)}>
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="copilot-collector-actions">
            <button type="button" onClick={onClear} disabled={items.length === 0 || busy || summarizing}>
              清空
            </button>
            <button type="button" onClick={onSummarize} disabled={items.length === 0 || busy || summarizing}>
              {summarizing ? '总结中…' : '✨ 总结错误'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      resolve(result.includes(',') ? result.split(',')[1] : '')
    }
    reader.onerror = () => reject(new Error('读取图片失败'))
    reader.readAsDataURL(file)
  })
}

export function CopilotPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: 'assistant', text: WELCOME_MESSAGE }])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showUpgradeBanner, setShowUpgradeBanner] = useState(false)
  const [upgradeText, setUpgradeText] = useState('')
  const [clipboardItems, setClipboardItems] = useState<string[]>([])
  const [clipboardExpanded, setClipboardExpanded] = useState(false)
  const [summarizing, setSummarizing] = useState(false)
  const [attachedImages, setAttachedImages] = useState<{ dataUrl: string; b64: string; mime: string; name: string }[]>([])
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const messagesRef = useRef(messages)
  messagesRef.current = messages

  // 启动：status 握手（版本横幅 / 后端不可用横幅）
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const status = await fetchCopilotStatus()
        if (cancelled) return
        if (!status.ok) {
          setError(status.error || 'Copilot 后端未就绪（/api/copilot/status 失败）。')
          return
        }
        const min = status.min_addon_version
        const addonVersion = getAddonVersionFromUrl()
        if (min && addonVersion && !isVersionAtLeast(addonVersion, min)) {
          setShowUpgradeBanner(true)
          setUpgradeText(`请升级 OpenBrep / Add-On：Copilot 面板需要 v${min} 或更高版本，当前为 v${addonVersion}。`)
        }
      } catch (err) {
        if (!cancelled) {
          setError(`无法连接 Copilot 后端：${err instanceof Error ? err.message : String(err)}`)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // 剪贴板错误缓冲：启动拉取 + 3s 轮询
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const resp = await fetchClipboardBuffer()
        if (cancelled) return
        if (resp.ok && Array.isArray(resp.items)) {
          setClipboardItems(resp.items.map((x) => String(x).trim()).filter(Boolean))
        }
      } catch {
        // 轮询失败非致命：保留上次已知数据，不刷错误横幅
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), CLIPBOARD_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const syncClipboardBuffer = useCallback(async (items: string[]) => {
    const resp = await clearClipboardBuffer(items)
    if (!resp.ok) throw new Error(resp.error || '错误收集区同步失败。')
  }, [])

  async function handleSend(overrideText?: string) {
    if (sending) return
    const isSummary = overrideText !== undefined
    const raw = overrideText === undefined ? input : overrideText
    const message = raw.trim()
    const imagesToSend = isSummary ? [] : attachedImages
    const hasImages = imagesToSend.length > 0
    if (!message && !hasImages) return

    setError(null)
    // history = 当前消息之前的所有轮次（与参考 UI 一致：当前消息只放 message 字段）
    const history: CopilotHistoryItem[] = messagesRef.current
      .filter((m) => m.text.trim())
      .map((m) => ({ role: m.role, content: m.text }))

    const images = imagesToSend.map((img) => ({ b64: img.b64, mime: img.mime }))
    const userText = hasImages && !message ? '[图片]' : message
    const userMessage: ChatMessage = {
      role: 'user',
      text: userText,
      images: hasImages ? imagesToSend.map((img) => ({ ...img })) : undefined,
    }
    setMessages((prev) => [...prev, userMessage])
    if (!isSummary) {
      setInput('')
      setAttachedImages([])
    }
    setSending(true)

    try {
      const resp = await sendCopilotChat({
        message: message || '请结合这些截图分析问题并给出修复建议。',
        history,
        images: images.length ? images : undefined,
      })
      if (!resp.ok) {
        setError(resp.error || `请求失败（status=${resp.status ?? '?'}）`)
        return
      }
      const reply = (resp.reply || '').trim() || '（空响应）'
      setMessages((prev) => [...prev, { role: 'assistant', text: reply }])
    } catch (err) {
      setError(`请求失败：${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }

  async function handleSummarize() {
    if (clipboardItems.length === 0 || sending || summarizing) return
    setError(null)
    setSummarizing(true)
    try {
      const resp = await summarizeClipboardErrors()
      if (!resp.ok) {
        setError(resp.error || '错误总结失败。')
        return
      }
      const summary = (resp.summary || '').trim()
      setClipboardItems([])
      if (summary) {
        // 摘要作为用户消息发出（卡片：调 summarize-errors 并作为用户消息发出）
        await handleSend(summary)
      }
    } catch (err) {
      setError(`错误总结失败：${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSummarizing(false)
    }
  }

  async function handleClearClipboard() {
    setError(null)
    try {
      await syncClipboardBuffer([])
      setClipboardItems([])
    } catch (err) {
      setError(`清空错误收集区失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  async function handleRemoveClipboardItem(index: number) {
    const next = clipboardItems.filter((_, i) => i !== index)
    setError(null)
    try {
      await syncClipboardBuffer(next)
      setClipboardItems(next)
    } catch (err) {
      setError(`删除错误记录失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  function handleUseClipboardItem(item: string) {
    setInput(item)
    inputRef.current?.focus()
  }

  async function handleAddFiles(files: FileList | File[] | null) {
    if (!files || files.length === 0) return
    const candidates = Array.from(files).filter((f) => f && f.type && f.type.startsWith('image/'))
    if (candidates.length === 0) return
    const slotsLeft = Math.max(0, MAX_IMAGES - attachedImages.length)
    if (slotsLeft === 0) {
      setError('最多只能附加 3 张图片。')
      return
    }
    const picked = candidates.slice(0, slotsLeft)
    if (candidates.length > slotsLeft) {
      setError('最多发送 3 张图片，已自动截断。')
    }
    try {
      const next: { dataUrl: string; b64: string; mime: string; name: string }[] = []
      for (const file of picked) {
        const b64 = await fileToBase64(file)
        const mime = file.type || 'image/png'
        next.push({ dataUrl: `data:${mime};base64,${b64}`, b64, mime, name: file.name || '图片' })
      }
      setAttachedImages((prev) => [...prev, ...next])
      setError(null)
    } catch (err) {
      setError(`图片加载失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  useEffect(() => {
    function handlePaste(event: ClipboardEvent) {
      const items = event.clipboardData?.items
      if (!items) return
      for (const item of items) {
        if (item.type && item.type.startsWith('image/')) {
          const file = item.getAsFile()
          if (!file) continue
          event.preventDefault()
          void handleAddFiles([file])
          break
        }
      }
    }
    document.addEventListener('paste', handlePaste)
    return () => document.removeEventListener('paste', handlePaste)
  }, [attachedImages.length])

  const busy = sending || summarizing

  return (
    <div className="copilot-page">
      <header className="copilot-header">
        <div className="copilot-title">
          <h1>🤖 GDL Copilot</h1>
          <p>Archicad GDL 修复助手</p>
        </div>
        <button
          type="button"
          className="copilot-clear-chat"
          title="清空对话"
          onClick={() => {
            setMessages([{ role: 'assistant', text: WELCOME_MESSAGE }])
            messagesRef.current = [{ role: 'assistant', text: WELCOME_MESSAGE }]
            setAttachedImages([])
            setError(null)
          }}
        >
          🗑 清空
        </button>
      </header>

      {showUpgradeBanner && (
        <div role="alert" className="copilot-banner copilot-banner-warning">
          ⚠️ {upgradeText}
        </div>
      )}
      {error && (
        <div role="alert" className="copilot-banner copilot-banner-error">
          {error}
        </div>
      )}

      <main className="copilot-chat">
        {messages.map((message, i) => (
          <MessageBubble key={`${message.role}-${i}`} message={message} />
        ))}
        {sending && (
          <div className="copilot-loading-dots" aria-label="发送中">
            <span className="copilot-dot" />
            <span className="copilot-dot" />
            <span className="copilot-dot" />
          </div>
        )}
      </main>

      <footer className="copilot-composer">
        <ClipboardCollector
          items={clipboardItems}
          expanded={clipboardExpanded}
          busy={busy}
          summarizing={summarizing}
          onToggle={() => setClipboardExpanded((v) => !v)}
          onUseItem={handleUseClipboardItem}
          onRemoveItem={(i) => void handleRemoveClipboardItem(i)}
          onClear={() => void handleClearClipboard()}
          onSummarize={() => void handleSummarize()}
        />
        {attachedImages.length > 0 && (
          <div className="copilot-image-box">
            {attachedImages.map((img, i) => (
              <div key={`${img.name}-${i}`} className="copilot-image-thumb">
                <img src={img.dataUrl} alt={img.name || `图片${i + 1}`} />
                <button
                  type="button"
                  title="移除图片"
                  onClick={() => setAttachedImages((prev) => prev.filter((_, idx) => idx !== i))}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="copilot-input-row">
          <textarea
            ref={inputRef}
            value={input}
            placeholder="粘贴报错信息、代码片段，或直接粘贴截图..."
            disabled={sending}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void handleSend()
              }
            }}
          />
          <div className="copilot-input-actions">
            <button
              type="button"
              className="copilot-attach-btn"
              title="选择图片"
              disabled={sending}
              onClick={() => fileInputRef.current?.click()}
            >
              📎
            </button>
            <button type="button" className="copilot-send-btn" disabled={sending} onClick={() => void handleSend()}>
              发送
            </button>
          </div>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(e) => {
            void handleAddFiles(e.target.files)
            e.target.value = ''
          }}
        />
        <div className="copilot-hint">
          <span>Enter 发送，Shift+Enter 换行，Cmd+V 可粘贴截图</span>
        </div>
      </footer>
    </div>
  )
}
