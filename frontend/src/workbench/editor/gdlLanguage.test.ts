import { describe, expect, test } from 'vitest'
import { GDL_LANGUAGE_ID, scriptLanguageForName } from './gdlLanguage'

describe('gdl language selection', () => {
  test('uses custom GDL language for scripts', () => {
    expect(scriptLanguageForName('3d.gdl')).toBe(GDL_LANGUAGE_ID)
    expect(scriptLanguageForName('2d.gdl')).toBe(GDL_LANGUAGE_ID)
    expect(scriptLanguageForName('pr.gdl')).toBe(GDL_LANGUAGE_ID)
  })

  test('keeps xml scripts on Monaco xml language', () => {
    expect(scriptLanguageForName('paramlist.xml')).toBe('xml')
    expect(scriptLanguageForName('libpartdata.xml')).toBe('xml')
  })

  test('falls back for unknown files', () => {
    expect(scriptLanguageForName('README.md')).toBe('plaintext')
  })
})
