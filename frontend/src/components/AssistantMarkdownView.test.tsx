import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AssistantMarkdown } from './AssistantMarkdownView'
import { AssistantReferenceGallery, assistantMessageImages } from './AssistantReferenceGallery'
import { extractMarkdownImages, parseAssistantMarkdown } from './assistantMarkdownParser'
import { openExternalUrl } from '../api/externalLinks'

// P0-B fixture：复刻搜索参考图回复的真实结构（三张 Markdown 图 + 来源链接 +
// 短建议），URL 用合成占位域——不把用户真实聊天/图片数据入库。
const SEARCH_REPLY = [
  '找了几张回纹参考：',
  '',
  '![回纹参考1](https://images.example.com/hw1.jpg)',
  '',
  '![回纹参考2](https://images.example.com/hw2.jpg)',
  '',
  '![回纹参考3](https://images.example.com/hw3.jpg)',
  '',
  '来源：[纹样图库](https://patterns.example.com/huiwen)',
  '',
  '建议：按**第1张**处理，连续方折、`90度`转角、等宽线脚。',
].join('\n')

describe('parseAssistantMarkdown', () => {
  it('parses paragraphs, lists, code fences and inline styles', () => {
    const blocks = parseAssistantMarkdown(
      '结论文字\n\n- 甲\n- 乙\n\n1. 一\n2. 二\n\n```gdl\nBLOCK A, B, ZZYZX\n```\n',
    )
    expect(blocks.map((b) => b.kind)).toEqual(['paragraph', 'list', 'list', 'code'])
    const [para, ul, ol, code] = blocks as unknown as [
      { tokens: { kind: string; text: string }[] },
      { ordered: boolean },
      { ordered: boolean },
      { text: string },
    ]
    expect(para.tokens[0].text).toBe('结论文字')
    expect(ul.ordered).toBe(false)
    expect(ol.ordered).toBe(true)
    expect(code.text).toBe('BLOCK A, B, ZZYZX')
  })

  it('never parses raw HTML into elements (escaped by renderer)', () => {
    const blocks = parseAssistantMarkdown('<img src=x onerror=alert(1)> **hi**')
    const para = blocks[0] as { kind: string; tokens: { kind: string; text: string }[] }
    expect(para.kind).toBe('paragraph')
    expect(para.tokens.some((t) => t.kind === 'text' && t.text.includes('<img src=x'))).toBe(true)
  })

  it('flags unsafe link and image protocols instead of dropping tokens', () => {
    const blocks = parseAssistantMarkdown('![a](javascript:alert(1)) [b](file:///etc/passwd) [ok](https://x.example.com)')
    const para = blocks[0] as { tokens: { kind: string; safe?: boolean; url: string }[] }
    const image = para.tokens.find((t) => t.kind === 'image')!
    const links = para.tokens.filter((t) => t.kind === 'link') as { url: string; safe: boolean }[]
    expect(image.safe).toBe(false)
    expect(links[0].safe).toBe(false)
    expect(links[1].safe).toBe(true)
  })

  it('extracts markdown images in order', () => {
    expect(extractMarkdownImages(SEARCH_REPLY).map((i) => i.url)).toEqual([
      'https://images.example.com/hw1.jpg',
      'https://images.example.com/hw2.jpg',
      'https://images.example.com/hw3.jpg',
    ])
  })
})

describe('AssistantMarkdown rendering', () => {
  beforeEach(() => {
    vi.mock('../api/externalLinks', async (importOriginal) => ({
      ...(await importOriginal<typeof import('../api/externalLinks')>()),
      openExternalUrl: vi.fn().mockResolvedValue(true),
    }))
  })
  afterEach(() => cleanup())

  it('renders links as clickable anchors that delegate to the safe opener', async () => {
    render(<AssistantMarkdown content={'来源：[纹样图库](https://patterns.example.com/huiwen)'} />)
    const link = screen.getByText('纹样图库') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('https://patterns.example.com/huiwen')
    fireEvent.click(link)
    await Promise.resolve()
    expect(openExternalUrl).toHaveBeenCalledWith('https://patterns.example.com/huiwen')
  })

  it('renders unsafe links as inert text', () => {
    render(<AssistantMarkdown content='[坏链接](javascript:alert(1))' />)
    const span = screen.getByText('坏链接')
    expect(span.tagName).toBe('SPAN')
  })

  it('renders markdown images as thumbnails with alt text', () => {
    render(<AssistantMarkdown content='![回纹参考1](https://images.example.com/hw1.jpg)' />)
    const img = screen.getByRole('img') as HTMLImageElement
    expect(img.getAttribute('src')).toBe('https://images.example.com/hw1.jpg')
    expect(img.getAttribute('alt')).toBe('回纹参考1')
  })

  it('shows explicit failure state for broken images instead of silent emptiness', () => {
    render(<AssistantMarkdown content='![参考](https://images.example.com/dead.jpg)' />)
    const img = screen.getByRole('img')
    fireEvent.error(img)
    expect(screen.getByText('图片加载失败')).toBeTruthy()
    expect(screen.getByText('打开原始链接')).toBeTruthy()
  })

  it('renders the full search-reply fixture with images and source link', () => {
    render(<AssistantMarkdown content={SEARCH_REPLY} />)
    expect(screen.getAllByRole('img').length).toBe(3)
    expect(screen.getByText('纹样图库')).toBeTruthy()
    expect(screen.getByText('第1张')).toBeTruthy()
  })
})

describe('AssistantReferenceGallery', () => {
  afterEach(() => cleanup())

  it('is collapsed until opened and lists adopt actions per image', () => {
    const images = assistantMessageImages(SEARCH_REPLY)
    render(<AssistantReferenceGallery images={images} adoptedUrl={null} onAdopt={() => {}} />)
    const summary = screen.getByText('参考图（3）· 点击展开比较 / 采用')
    fireEvent.click(summary)
    expect(screen.getAllByText('采用').length).toBe(3)
    expect(screen.getAllByText('浏览器打开').length).toBe(3)
  })

  it('thumbnail click opens the in-app lightbox instead of window.open (F7)', () => {
    const images = assistantMessageImages(SEARCH_REPLY)
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(<AssistantReferenceGallery images={images} adoptedUrl={null} onAdopt={() => {}} />)
    fireEvent.click(screen.getByText('参考图（3）· 点击展开比较 / 采用'))
    fireEvent.click(screen.getAllByTitle('点击放大（应用内）')[0])
    expect(openSpy).not.toHaveBeenCalled()
    const lightbox = screen.getByRole('dialog')
    expect(lightbox).toBeTruthy()
    expect(lightbox.querySelector('img')?.getAttribute('src')).toBe('https://images.example.com/hw1.jpg')
    fireEvent.click(lightbox)
    expect(screen.queryByRole('dialog')).toBeNull()
    openSpy.mockRestore()
  })

  it('adopting is an explicit toggle and highlights the chosen image', () => {
    const images = assistantMessageImages(SEARCH_REPLY)
    const onAdopt = vi.fn()
    render(<AssistantReferenceGallery images={images} adoptedUrl={null} onAdopt={onAdopt} />)
    fireEvent.click(screen.getByText('参考图（3）· 点击展开比较 / 采用'))
    fireEvent.click(screen.getAllByText('采用')[0])
    expect(onAdopt).toHaveBeenCalledWith('https://images.example.com/hw1.jpg', '回纹参考1')

    render(<AssistantReferenceGallery images={images} adoptedUrl='https://images.example.com/hw1.jpg' onAdopt={onAdopt} />)
    expect(screen.getByText('✓ 已采用')).toBeTruthy()
    fireEvent.click(screen.getByText('✓ 已采用'))
    expect(onAdopt).toHaveBeenLastCalledWith(null, '回纹参考1')
  })
})
