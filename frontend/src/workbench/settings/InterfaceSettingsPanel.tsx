import type { Locale } from '../../i18n'
import { useT } from '../../i18n'

interface InterfaceSettingsPanelProps {
  locale: Locale
  onLocaleChange: (locale: Locale) => void
}

// Language names are each language's own native name, not translated —
// the "中文" option must read "中文" even while the UI is in English.
const LANGUAGE_OPTIONS: Array<{ value: Locale; label: string }> = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'English' },
]

export function InterfaceSettingsPanel({ locale, onLocaleChange }: InterfaceSettingsPanelProps) {
  const t = useT()

  return (
    <>
      <p className="settings-description">{t('interfacePanel.description')}</p>
      <fieldset className="settings-row" role="radiogroup" aria-label={t('interfacePanel.languageLabel')}>
        <span>{t('interfacePanel.languageLabel')}</span>
        <div className="settings-radio-group">
          {LANGUAGE_OPTIONS.map((option) => (
            <label key={option.value} className="settings-radio-option">
              <input
                type="radio"
                name="ui-language"
                value={option.value}
                checked={locale === option.value}
                onChange={() => onLocaleChange(option.value)}
              />
              {option.label}
            </label>
          ))}
        </div>
      </fieldset>
    </>
  )
}

export function interfaceSummary(locale: Locale): string {
  return LANGUAGE_OPTIONS.find((option) => option.value === locale)?.label ?? locale
}
