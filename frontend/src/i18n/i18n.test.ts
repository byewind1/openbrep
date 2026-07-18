import { afterEach, describe, expect, test, vi } from 'vitest'
import { en } from './locales/en'
import { zh } from './locales/zh'
import { t } from './index'

describe('i18n dictionaries', () => {
  test('zh and en have exactly the same key set', () => {
    const zhKeys = Object.keys(zh).sort()
    const enKeys = Object.keys(en).sort()
    expect(enKeys).toEqual(zhKeys)
  })

  test('every zh value is a non-empty string', () => {
    for (const [key, value] of Object.entries(zh)) {
      expect(typeof value, `zh["${key}"]`).toBe('string')
      expect(value.length, `zh["${key}"] should not be empty`).toBeGreaterThan(0)
    }
  })

  test('every en value is a non-empty string', () => {
    for (const [key, value] of Object.entries(en)) {
      expect(typeof value, `en["${key}"]`).toBe('string')
      expect(value.length, `en["${key}"] should not be empty`).toBeGreaterThan(0)
    }
  })
})

describe('t()', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  test('returns the plain translation for a key with no placeholders', () => {
    expect(t('zh', 'topMenu.save')).toBe('保存')
    expect(t('en', 'topMenu.save')).toBe('Save')
  })

  test('fills a single {var} placeholder', () => {
    expect(t('zh', 'topMenu.status.savedAt', { time: '10:32' })).toBe('已保存 10:32')
    expect(t('en', 'topMenu.status.savedAt', { time: '10:32' })).toBe('Saved 10:32')
  })

  test('fills multiple placeholders in one template', () => {
    expect(t('en', 'settings.summary.knowledgeFreePro', { free: 12, pro: 3 })).toBe('Free 12 · Pro 3')
    expect(t('zh', 'settings.summary.knowledgeFreePro', { free: 12, pro: 3 })).toBe('免费 12 · 专业 3')
  })

  test('leaves the template untouched when no vars are given for a static key', () => {
    expect(t('zh', 'settings.section.knowledge')).toBe('知识库')
  })

  test('falls back to the key itself and warns in dev when a key is missing', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    // @ts-expect-error — deliberately probing a nonexistent key to test the fallback path
    const result = t('zh', 'this.key.does.not.exist')
    expect(result).toBe('this.key.does.not.exist')
    expect(warnSpy).toHaveBeenCalled()
  })
})
