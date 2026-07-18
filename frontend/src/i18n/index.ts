import { en } from './locales/en'
import { zh, type LocaleKey } from './locales/zh'
import { useUiPrefsStore, type Locale } from '../state/uiPrefsStore'

export type { Locale, LocaleKey }

const dictionaries: Record<Locale, Record<LocaleKey, string>> = { zh, en }

/**
 * Translate `key` for `locale`, filling `{var}` placeholders from `vars`.
 * Missing keys fall back to the key itself (visible signal, not silent
 * English residue) and warn in dev — see i18n 实现计划 §三.5.
 */
export function t(locale: Locale, key: LocaleKey, vars?: Record<string, string | number>): string {
  const template = dictionaries[locale][key]
  if (template === undefined) {
    if (import.meta.env.DEV) {
      console.warn(`[i18n] missing translation for key "${String(key)}" (locale: ${locale})`)
    }
    return String(key)
  }
  if (!vars) {
    return template
  }
  return Object.entries(vars).reduce(
    (result, [varKey, value]) => result.replaceAll(`{${varKey}}`, String(value)),
    template,
  )
}

/** Hook form of `t()`, bound to the current `uiPrefsStore` locale. */
export function useT() {
  const locale = useUiPrefsStore((state) => state.locale)
  return (key: LocaleKey, vars?: Record<string, string | number>) => t(locale, key, vars)
}
