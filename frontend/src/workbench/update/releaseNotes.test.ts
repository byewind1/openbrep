import { describe, expect, test } from 'vitest'
import { parseReleaseHighlights } from './releaseNotes'

describe('parseReleaseHighlights', () => {
  test('returns empty list for null/empty notes', () => {
    expect(parseReleaseHighlights(null)).toEqual([])
    expect(parseReleaseHighlights('')).toEqual([])
    expect(parseReleaseHighlights('## What\'s Changed\nno bullets here')).toEqual([])
  })

  test('parses GitHub auto-generated notes into classified highlights', () => {
    const notes = [
      "## What's Changed",
      '* feat: add auto-update dialog by @dev in https://github.com/o/r/pull/12',
      '* fix: repair preview crash on empty script by @dev in https://github.com/o/r/pull/13',
      '* docs: update install guide by @dev in https://github.com/o/r/pull/14',
      '',
      '**Full Changelog**: https://github.com/o/r/compare/v1...v2',
    ].join('\n')
    expect(parseReleaseHighlights(notes)).toEqual([
      { kind: 'feature', text: 'add auto-update dialog' },
      { kind: 'fix', text: 'repair preview crash on empty script' },
      { kind: 'other', text: 'update install guide' },
    ])
  })

  test('strips markdown links and code ticks', () => {
    const notes = '- feat: support [GDL](https://example.com) `CALL` macros'
    expect(parseReleaseHighlights(notes)).toEqual([
      { kind: 'feature', text: 'support GDL CALL macros' },
    ])
  })

  test('bullets without conventional prefix fall back to other', () => {
    expect(parseReleaseHighlights('- 改进预览稳定性')).toEqual([
      { kind: 'other', text: '改进预览稳定性' },
    ])
  })

  test('dedupes and caps the list', () => {
    const notes = Array.from({ length: 12 }, (_, i) => `- fix: issue ${i % 6}`).join('\n')
    const highlights = parseReleaseHighlights(notes)
    expect(highlights.length).toBe(6)
  })
})
