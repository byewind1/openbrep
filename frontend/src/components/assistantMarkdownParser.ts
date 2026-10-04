/**
 * P0-B：助手消息受限 Markdown 解析器（纯函数，无依赖、无 HTML）。
 *
 * 背景（2026-10-04 漏窗诊断 R6）：助手正文此前用 <p>{content}</p> 直出，
 * 搜索参考图回复里的 ![...](url) 与来源链接全部退化为纯文本，用户只能
 * 复制地址去浏览器。本解析器覆盖聊天回复实际出现的结构：
 * 段落 / 无序有序列表 / 围栏代码块 / 行内代码 / 粗体 / 链接 / 图片。
 *
 * 安全契约（与渲染器共同成立）：
 * - 永不产生 HTML——所有文本由 React 转义，组件不使用 dangerouslySetInnerHTML；
 * - 链接与图片 URL 限 http/https（图片另放行 data:image/*），
 *   其余协议整个 token 降级为纯文本；
 * - 解析失败的非法结构一律按原始文本呈现，不猜测。
 */

export type InlineToken =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'bold'; text: string }
  | { kind: 'link'; text: string; url: string; safe: boolean }
  | { kind: 'image'; alt: string; url: string; safe: boolean }

export type Block =
  | { kind: 'paragraph'; tokens: InlineToken[] }
  | { kind: 'list'; ordered: boolean; items: InlineToken[][] }
  | { kind: 'code'; text: string; lang: string }

const IMAGE_RE = /^!\[([^\]]*)\]\(([^)\s]+)\)/
const LINK_RE = /^\[([^\]]*)\]\(([^)\s]+)\)/
const CODE_RE = /^`([^`]+)`/
const BOLD_RE = /^\*\*([^*]+)\*\*/
const TOKEN_SPLIT_RE = /(!\[[^\]]*\]\([^)\s]+\)|\[[^\]]*\]\([^)\s]+\)|`[^`]+`|\*\*[^*]+\*\*)/g

function isSafeHttpUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    return false
  }
}

function isSafeImageUrl(url: string): boolean {
  if (url.startsWith('data:image/')) return true
  return isSafeHttpUrl(url)
}

export function parseInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = []
  let plain = ''
  const pushPlain = () => {
    if (plain) {
      tokens.push({ kind: 'text', text: plain })
      plain = ''
    }
  }
  for (const part of text.split(TOKEN_SPLIT_RE)) {
    if (!part) continue
    let matched = false
    for (const [re, build] of [
      [IMAGE_RE, (m: RegExpMatchArray) => ({
        kind: 'image', alt: m[1], url: m[2], safe: isSafeImageUrl(m[2]),
      })],
      [LINK_RE, (m: RegExpMatchArray) => ({
        kind: 'link', text: m[1] || m[2], url: m[2], safe: isSafeHttpUrl(m[2]),
      })],
      [CODE_RE, (m: RegExpMatchArray) => ({ kind: 'code', text: m[1] })],
      [BOLD_RE, (m: RegExpMatchArray) => ({ kind: 'bold', text: m[1] })],
    ] as const) {
      const m = part.match(re)
      if (m) {
        pushPlain()
        tokens.push(build(m) as InlineToken)
        matched = true
        break
      }
    }
    if (!matched) plain += part
  }
  pushPlain()
  return tokens
}

/** 解析受限 Markdown 为块列表；不抛异常，任何输入都有输出。 */
export function parseAssistantMarkdown(content: string): Block[] {
  const blocks: Block[] = []
  const lines = (content ?? '').replace(/\r\n/g, '\n').split('\n')
  let paragraph: string[] = []
  let list: { ordered: boolean; items: string[] } | null = null

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ kind: 'paragraph', tokens: parseInline(paragraph.join('\n')) })
      paragraph = []
    }
  }
  const flushList = () => {
    if (list) {
      blocks.push({ kind: 'list', ordered: list.ordered, items: list.items.map(parseInline) })
      list = null
    }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const fence = line.match(/^```(\w*)\s*$/)
    if (fence) {
      flushParagraph()
      flushList()
      const lang = fence[1] ?? ''
      const body: string[] = []
      i++
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        body.push(lines[i])
        i++
      }
      blocks.push({ kind: 'code', text: body.join('\n'), lang })
      continue
    }
    const unordered = line.match(/^\s*[-*]\s+(.*)$/)
    if (unordered) {
      flushParagraph()
      if (list && list.ordered) flushList()
      if (!list) list = { ordered: false, items: [] }
      list.items.push(unordered[1])
      continue
    }
    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (ordered) {
      flushParagraph()
      if (list && !list.ordered) flushList()
      if (!list) list = { ordered: true, items: [] }
      list.items.push(ordered[1])
      continue
    }
    if (!line.trim()) {
      flushParagraph()
      flushList()
      continue
    }
    flushList()
    paragraph.push(line)
  }
  flushParagraph()
  flushList()
  return blocks
}

/** 从消息 Markdown 中提取全部可展示图片（图库/缩略栏数据源）。 */
export function extractMarkdownImages(content: string): { alt: string; url: string; safe: boolean }[] {
  const images: { alt: string; url: string; safe: boolean }[] = []
  for (const block of parseAssistantMarkdown(content)) {
    const tokens = block.kind === 'paragraph'
      ? block.tokens
      : block.kind === 'list'
        ? block.items.flat()
        : []
    for (const token of tokens) {
      if (token.kind === 'image') images.push({ alt: token.alt, url: token.url, safe: token.safe })
    }
  }
  return images
}
