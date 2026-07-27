import type { LocaleKey } from './zh'

/**
 * English dictionary. `Record<LocaleKey, string>` forces the compiler to
 * flag any key missing from (or extra to) zh.ts — see also i18n.test.ts
 * for a redundant runtime guard.
 */
export const en: Record<LocaleKey, string> = {
  // TopMenu
  'topMenu.save': 'Save',
  'topMenu.compile': 'Compile',
  'topMenu.build': 'Build',
  'topMenu.mockCompile': 'Mock Compile',
  'topMenu.settings': 'Settings',
  'topMenu.apply': 'Apply',
  'topMenu.status.saved': 'Saved',
  'topMenu.status.unsaved': 'Unsaved',
  'topMenu.status.empty': 'Empty',
  'topMenu.status.dirty': 'Dirty',
  'topMenu.status.clean': 'Clean',
  'topMenu.status.params': 'Params',
  'topMenu.status.stable': 'Stable',
  'topMenu.status.savedAt': 'Saved {time}',
  'topMenu.status.modelTitle': 'Current AI model · click to switch',

  // SettingsDrawer header
  'settings.header.title': 'Settings',
  'settings.header.saving': 'Saving',
  'settings.header.unsaved': 'Unsaved',
  'settings.header.saved': 'Saved',
  'settings.header.error': 'Error',
  'settings.header.reloadTitle': 'Reload config from disk',
  'settings.header.saveButton': 'Save',
  'settings.header.closeTitle': 'Close',
  'settings.header.closeAriaLabel': 'Close settings',
  'settings.header.drawerAriaLabel': 'Workbench settings',
  'settings.header.resizeAriaLabel': 'Resize settings panel',

  // SettingsDrawer section titles
  'settings.section.interface': 'Interface',
  'settings.section.ai': 'AI',
  'settings.section.compiler': 'Compiler',
  'settings.section.workspace': 'Workspace',
  'settings.section.git': 'Git',
  'settings.section.memory': 'Memory',
  'settings.section.knowledge': 'Knowledge',

  // SettingsDrawer section summaries
  'settings.summary.compilerLp': 'LP',
  'settings.summary.compilerMock': 'Mock',
  'settings.summary.aiNoModel': 'No model',
  'settings.summary.workspaceRecentCount': '{count} recent',
  'settings.summary.gitNotInitialized': 'Not initialized',
  'settings.summary.gitEnabled': 'Enabled',
  'settings.summary.gitDisabled': 'Disabled',
  'settings.summary.memoryLessonCount': '{count} lessons',
  'settings.summary.knowledgeDash': '—',
  'settings.summary.knowledgeFreePro': 'Free {free} · Pro {pro}',
  'settings.summary.knowledgeFreeNoPro': 'Free {free} · No Pro',

  // AiSettingsPanel
  'settings.ai.modelLabel': 'Model',
  'settings.ai.searchPlaceholder': 'Search models…',
  'settings.ai.groupCustom': 'Custom',
  'settings.ai.groupOfficial': 'Official',
  'settings.ai.noMatch': 'No matching models',
  'settings.ai.modelUnavailable': 'Current model is unavailable (missing API key or incomplete config). Use “Edit config.toml” below to set it up.',
  'settings.ai.officialBaseNote': 'Note: official models always use their official endpoint (the top-level api_base does not apply). For a proxy endpoint, configure [[llm.custom_providers]] in config.toml.',
  'settings.ai.apiKeyLabel': 'API Key',
  'settings.ai.apiKeyPlaceholder': 'Enter the API key for this model',
  'settings.ai.apiKeyReplacePlaceholder': 'Key saved — enter a new one to replace',
  'settings.ai.saveKey': 'Save key',
  'settings.ai.savingAndVerifying': 'Saving and verifying…',
  'settings.ai.keySaved': 'API key saved.',
  'settings.ai.keySavedConnectionOk': 'Key saved. Connection OK ({ms} ms)',
  'settings.ai.keySavedConnectionFailed': 'Key saved, but the connection failed (details below).',
  'settings.ai.keySaveFailed': 'Failed to save API key.',
  'settings.ai.confirmSwitch': 'Switch to {model}?',
  'settings.ai.confirmYes': 'Confirm',
  'settings.ai.confirmNo': 'Cancel',
  'settings.ai.switchFailed': 'Model switch failed.',
  'settings.ai.copyError': 'Copy error details',
  'settings.ai.copied': 'Copied',

  // InterfaceSettingsPanel
  'interfacePanel.languageLabel': 'Language',
  'interfacePanel.description': 'Choose the workbench display language. Applies immediately.',
}
