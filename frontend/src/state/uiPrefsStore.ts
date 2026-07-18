import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * Cross-workspace browser view preference, not a project/backend concept —
 * deliberately separate from workbenchStore (project domain state) and
 * config.toml (backend has no locale concept). See i18n 实现计划 §三.2.
 */
export type Locale = 'zh' | 'en'

interface UiPrefsState {
  locale: Locale
  setLocale: (locale: Locale) => void
}

export const useUiPrefsStore = create<UiPrefsState>()(
  persist(
    (set) => ({
      locale: 'zh',
      setLocale: (locale) => set({ locale }),
    }),
    { name: 'openbrep-ui-prefs' },
  ),
)
