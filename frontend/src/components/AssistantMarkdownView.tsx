import { useState } from 'react'
import type { ReactNode } from 'react'
import { parseAssistantMarkdown } from './assistantMarkdownParser'
import type { Block, InlineToken } from './assistantMarkdownParser'
import { isSafeExternalUrl, openExternalUrl } from '../api/externalLinks'

/**
 * P0-B：助手正文与历史视图共用的受限 Markdown 渲染器。
 *
 * - 文本全部经 React 转义，绝不注入 HTML；
 * - 链接点击走 openExternalUrl（协议白名单 + Tauri 系统浏览器），
 *   非法协议的链接渲染为不可点文本；
 * - Markdown 图片渲染为参考图卡：缩略图、点击大图、失败状态、来源链接；
 *   渲染图片不把它设为执行参考，也不触碰 HSF——"采用"是显式按钮动作。
 */

export interface MarkdownImageInfo {
  alt: string
  url: string
  safe: boolean
}

function LinkToken({ text, url, safe }: { text: string; url: string; safe: boolean }) {
  if (!safe || !isSafeExternalUrl(url)) {
    return <span className="assistant-md-link-unsafe">{text || url}</span>
  }
  return (
    <a
      className="assistant-md-link"
      href={url}
      title={url}
      onClick={(event) => {
        event.preventDefault()
        void openExternalUrl(url)
      }}
    >
      {text || url}
    </a>
  )
}

function ImageCard({ alt, url, safe, onOpen }: { alt: string; url: string; safe: boolean; onOpen: (info: MarkdownImageInfo) => void }) {
  const [status, setStatus] = useState<'loading' | 'ok' | 'failed'>(safe ? 'loading' : 'failed')
  if (!safe) {
    return (
      <figure className="assistant-ref-image is-unsafe">
        <div className="assistant-ref-image-failed">
          不支持展示的图片地址
          <span className="assistant-ref-image-url">{url}</span>
        </div>
        {alt ? <figcaption>{alt}</figcaption> : null}
      </figure>
    )
  }
  return (
    <figure className="assistant-ref-image">
      {status !== 'failed' ? (
        <button
          type="button"
          className="assistant-ref-image-thumb"
          title={alt || url}
          onClick={() => onOpen({ alt, url, safe })}
        >
          <img
            src={url}
            alt={alt || '参考图'}
            loading="lazy"
            onLoad={() => setStatus('ok')}
            onError={() => setStatus('failed')}
          />
        </button>
      ) : null}
      {status === 'failed' ? (
        <div className="assistant-ref-image-failed">
          图片加载失败
          <LinkToken text="打开原始链接" url={url} safe />
          <span className="assistant-ref-image-url">{url}</span>
        </div>
      ) : null}
      {alt ? <figcaption>{alt}</figcaption> : null}
    </figure>
  )
}

function InlineTokens({ tokens, onOpenImage }: { tokens: InlineToken[]; onOpenImage: (info: MarkdownImageInfo) => void }) {
  const nodes: ReactNode[] = tokens.map((token, i) => {
    switch (token.kind) {
      case 'code':
        return <code key={i}>{token.text}</code>
      case 'bold':
        return <strong key={i}>{token.text}</strong>
      case 'link':
        return <LinkToken key={i} text={token.text} url={token.url} safe={token.safe} />
      case 'image':
        return <ImageCard key={i} alt={token.alt} url={token.url} safe={token.safe} onOpen={onOpenImage} />
      default:
        return <span key={i}>{token.text}</span>
    }
  })
  return <>{nodes}</>
}

function BlockView({ block, onOpenImage }: { block: Block; onOpenImage: (info: MarkdownImageInfo) => void }) {
  if (block.kind === 'code') {
    return (
      <pre className="assistant-md-code">
        <code>{block.text}</code>
      </pre>
    )
  }
  if (block.kind === 'list') {
    const items = block.items.map((tokens, i) => (
      <li key={i}>
        <InlineTokens tokens={tokens} onOpenImage={onOpenImage} />
      </li>
    ))
    return block.ordered ? <ol>{items}</ol> : <ul>{items}</ul>
  }
  // 图片是块级卡片（figure），不能合法嵌套在 <p> 内：按 image token 把段落
  // 拆成 文本段 / 图片卡 / 文本段 的兄弟序列。
  const parts: ReactNode[] = []
  let run: InlineToken[] = []
  let key = 0
  const flushRun = () => {
    if (run.length) {
      parts.push(
        <p key={`p-${key++}`}>
          <InlineTokens tokens={run} onOpenImage={onOpenImage} />
        </p>,
      )
      run = []
    }
  }
  for (const token of block.tokens) {
    if (token.kind === 'image') {
      flushRun()
      parts.push(<ImageCard key={`img-${key++}`} alt={token.alt} url={token.url} safe={token.safe} onOpen={onOpenImage} />)
    } else {
      run.push(token)
    }
  }
  flushRun()
  return <>{parts}</>
}

function ImageLightbox({ image, onClose }: { image: MarkdownImageInfo; onClose: () => void }) {
  return (
    <div className="assistant-md-lightbox" role="dialog" aria-modal="true" onClick={onClose}>
      <img src={image.url} alt={image.alt || '参考图'} />
    </div>
  )
}

export function AssistantMarkdown({ content }: { content: string }) {
  const [lightbox, setLightbox] = useState<MarkdownImageInfo | null>(null)
  const blocks = parseAssistantMarkdown(content)
  return (
    <div className="assistant-md">
      {blocks.map((block, i) => (
        <BlockView key={i} block={block} onOpenImage={setLightbox} />
      ))}
      {lightbox ? <ImageLightbox image={lightbox} onClose={() => setLightbox(null)} /> : null}
    </div>
  )
}
