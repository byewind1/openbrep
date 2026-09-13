/**
 * 把 GitHub Release 正文（--generate-notes 产物或手写 Markdown）解析成
 * 更新对话框用的简要要点列表：提取 bullet 行、剥离 "by @user in <url>" 尾缀
 * 和 Markdown 链接，按 conventional-commit 前缀分类为 新增 / 修复 / 其他。
 */

export type HighlightKind = 'feature' | 'fix' | 'other'

export interface ReleaseHighlight {
  kind: HighlightKind
  text: string
}

const MAX_HIGHLIGHTS = 8

function stripMarkdown(text: string): string {
  return text
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // [text](url) → text
    .replace(/[`*_]/g, '')
    .trim()
}

function classify(text: string): { kind: HighlightKind; text: string } {
  const match = /^(\w+)(\([^)]*\))?!?:\s*(.+)$/.exec(text)
  if (match) {
    const prefix = match[1].toLowerCase()
    const rest = match[3].trim()
    if (prefix === 'feat' || prefix === 'feature') return { kind: 'feature', text: rest }
    if (prefix === 'fix' || prefix === 'bugfix' || prefix === 'hotfix') {
      return { kind: 'fix', text: rest }
    }
    return { kind: 'other', text: rest }
  }
  return { kind: 'other', text }
}

export function parseReleaseHighlights(notes: string | null | undefined): ReleaseHighlight[] {
  if (!notes) return []
  const highlights: ReleaseHighlight[] = []
  for (const rawLine of notes.split('\n')) {
    const line = rawLine.trim()
    const bullet = /^[-*]\s+(.+)$/.exec(line)
    if (!bullet) continue
    // 剥离 GitHub 自动 notes 的 "by @user in https://.../pull/N" 尾缀
    const cleaned = bullet[1].replace(/\s+by\s+@\S+(\s+in\s+\S+)?\s*$/, '')
    const { kind, text } = classify(stripMarkdown(cleaned))
    if (!text) continue
    if (highlights.some((h) => h.text === text)) continue
    highlights.push({ kind, text })
    if (highlights.length >= MAX_HIGHLIGHTS) break
  }
  return highlights
}
