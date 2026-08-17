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
  // Codex BYOA (D1): ChatGPT subscription login & dynamic models
  'settings.ai.codex.sectionTitle': 'ChatGPT Codex (subscription)',
  'settings.ai.codex.login': 'Connect my ChatGPT',
  'settings.ai.codex.loginPending': 'Signing in…',
  'settings.ai.codex.loginPendingHint': 'Complete the sign-in in the opened browser. The panel refreshes your account and available models automatically.',
  'settings.ai.codex.loginFailed': 'Failed to start browser sign-in',
  'settings.ai.codex.logout': 'Disconnect',
  'settings.ai.codex.logoutFailed': 'Failed to sign out',
  'settings.ai.codex.connectedLabel': 'Connected',
  'settings.ai.codex.notConnectedLabel': 'Not connected',
  'settings.ai.codex.noCli': 'Codex CLI not found. Install it first (npm install -g @openai/codex) and try again.',
  'settings.ai.codex.errorUnknown': 'Codex connection state unknown. Try again later.',
  'settings.ai.codex.modelsLabel': 'Available models (from your ChatGPT account)',
  'settings.ai.codex.noModels': 'No models available for this account.',
  'settings.ai.codex.modelUnavailable': 'Current Codex model is unavailable: connect ChatGPT or sign in again.',
  'settings.ai.codex.cancelLogin': 'Cancel sign-in',
  'settings.ai.codex.loginCancelled': 'Sign-in cancelled.',
  'settings.ai.codex.deviceCode': 'Sign in with device code',
  'settings.ai.codex.deviceCodeHint':
    'If the browser flow did not open or failed, use the device code flow: open the verification URL in any browser and enter the code below.',
  'settings.ai.codex.deviceCodeUrl': 'Verification URL',
  'settings.ai.codex.deviceCodeValue': 'Device code',
  'settings.ai.codex.deviceCodeCopied': 'Copied',
  'settings.ai.codex.copyDeviceCode': 'Copy',
  'settings.ai.codex.crashed':
    'Codex app-server crashed. Click Restart to restore the connection.',
  'settings.ai.codex.restart': 'Restart app-server',
  'settings.ai.codex.restarting': 'Restarting…',
  'settings.ai.codex.restartFailed': 'Failed to restart app-server',
  'settings.ai.codex.versionIncompatible':
    'Codex CLI version is incompatible. Upgrade Codex CLI (npm install -g @openai/codex) and try again.',
  'settings.ai.codex.quotaExhausted':
    'ChatGPT subscription quota is exhausted or usage limit reached. Try again later, wait for reset, or switch to another model/provider.',
  'settings.ai.codex.rateLimits': 'Usage',
  'settings.ai.codex.rateLimitsPlan': 'Plan',
  'settings.ai.codex.rateLimitsUsed': 'Used',
  'settings.ai.codex.rateLimitsUnlimited': 'unlimited',
  'settings.ai.codex.rateLimitsHasCredits': 'has credits',
  'settings.ai.codex.rateLimitsReached': 'quota reached',
  'settings.ai.codex.effortLabel': 'Reasoning effort',
  'settings.ai.codex.effortDefault': 'No override (model default)',
  'settings.ai.codex.effortSave': 'Save effort',
  'settings.ai.codex.effortSaved': 'Saved (Fixed mode will strictly use this combination)',
  'settings.ai.codex.effortSaveFailed': 'Save failed',

  // InterfaceSettingsPanel
  'interfacePanel.languageLabel': 'Language',
  'interfacePanel.description': 'Choose the workbench display language. Applies immediately.',

  // WorkspacePanel
  'workspace.title': 'Workspace',
  'workspace.notAttached': 'No workspace attached',
  'workspace.attachPlaceholder': 'Workspace directory path',
  'workspace.attach': 'Attach',
  'workspace.searchPlaceholder': 'Search across projects',
  'workspace.search': 'Search',
  'workspace.close': 'Detach',
  'workspace.refresh': 'Refresh',
  'workspace.projects': '{count} projects',
  'workspace.noProjects': 'No projects',
  'workspace.searchResults': 'Search results',
  'workspace.searchEmpty': 'No matches',
  'workspace.badgeOrigin': 'import',
  'workspace.badgeArtifacts': '{count} artifacts',
  'workspace.browse': 'Browse…',
  'workspace.initAttach': 'Init & attach',
  'workspace.notWorkspaceHint': 'This directory is not an OpenBrep workspace yet',
  'workspace.dismiss': 'Dismiss',
  'workspace.delete': 'Move to trash',
  'workspace.deleteConfirm': 'Move project {name} to trash? It can be restored from .openbrep/trash/.',
  'workspace.deleteActiveDisabled': 'This project is open: switch to another project first',

  // P11 parameter panel: UI / parameter script view toggle + enum dropdown
  'parameter.view.params': 'Parameters',
  'parameter.view.script': 'Parameter Script',
  'parameter.enumFallback': 'Current: {value} (not in VALUES list)',
  'parameter.script.saved': 'Saved',
  'parameter.script.unsaved': 'Unsaved',
  'parameter.script.save': 'Save',
  'parameter.script.saving': 'Saving',

  // P6a cross-project assistant history import (history drawer)
  'assistant.history.import': 'Import…',
  'assistant.history.importTitle': 'Import chat history from another project',
  'assistant.history.importDisabledTitle': 'Open a project first to import chat history',
  'assistant.history.importHintNoWorkspace': 'Mount a workspace in the workspace panel first',
  'assistant.history.importNoSources': 'No other projects to import from',
  'assistant.history.importSourceAria': 'Import from project {name}',
  'assistant.history.importConfirmTitle': 'Import chat history',
  'assistant.history.importConfirmMessage': 'Import chat history from "{source}" into the current project "{current}"? It will be appended and merged, existing records are kept.',
  'assistant.history.importConfirmLabel': 'Import',

  // P6b distill chat history into an AI-ready instruction (history drawer)
  'assistant.history.distill': 'Distill to instruction',
  'assistant.history.distillTitle': 'Distill chat history into an AI-ready instruction (fills the input draft; review before sending)',
  'assistant.history.distillNoProject': 'Open a project first to distill chat history',
  'assistant.history.distillNoHistory': 'No chat history in the current project to distill',
  'assistant.history.distillBusy': 'Distilling…',

  // Assistant plan confirmation gate (V3)
  'assistant.plan.title': 'Modification plan',
  'assistant.plan.userChanges': 'Changes you will see',
  'assistant.plan.affectedFiles': 'Affected files',
  'assistant.plan.risk': 'Risk',
  'assistant.plan.confirm': 'Confirm changes',
  'assistant.plan.cancel': 'Cancel',

  // Skill harvest proposal (P2-d)
  'assistant.skillProposal.title': 'Save as skill proposal',
  'assistant.skillProposal.evidence': 'Evidence',
  'assistant.skillProposal.project': 'Source project',
  'assistant.skillProposal.approve': 'Approve & save',
  'assistant.skillProposal.ignore': 'Ignore',

  // MODIFY acceptance report (V5)
  'assistant.acceptance.title': 'Acceptance report',
  'assistant.acceptance.geometry': 'Geometry comparison',
  'assistant.acceptance.before': 'Before',
  'assistant.acceptance.after': 'After',
  'assistant.acceptance.meshCount': 'Mesh count',
  'assistant.acceptance.bbox': 'Bounding box',
  'assistant.acceptance.counts2d': '2D elements (lines/polygons/circles/arcs)',

  // Vision extraction card (P5d-1, read-only): schema extraction result
  'vision.extraction.title': 'Image extraction',
  'vision.extraction.degraded': '【Analysis failed — degraded】',
  'vision.extraction.criticDegraded': '【Critic verification degraded】',
  'vision.extraction.lowConfidence': 'low confidence',
  'vision.extraction.evidenceHint': 'critic correction evidence',
  'vision.extraction.reusedFrom': 'reused from',

  // P5d-2 extraction confirmation gate (editable confirm card)
  'assistant.extraction.title': 'Confirm image extraction',
  'assistant.extraction.subtitle': 'What the model read from the reference image; required / critic_checks fields are editable. Confirm to continue generation.',
  'assistant.extraction.confirm': 'Confirm & generate',
  'assistant.extraction.cancel': 'Cancel',
  'assistant.extraction.keepRest': 'other fields keep original values',

  // 3D preview picking (P1a): click mesh to highlight + jump to source
  'preview.pick.noSource': 'No source trace',
  'preview.pick.jumpToSource': 'Jump to source',
  'preview.pick.dismiss': 'Clear selection',
  'preview.pick.barAriaLabel': 'Selected part info',

  // 3D preview parts panel (P1d): Blender-outliner style part list
  'preview.parts.title': 'Parts',
  'preview.parts.toggle': 'Toggle parts panel',
  'preview.parts.hide': 'Hide part',
  'preview.parts.show': 'Show part',

  // 3D preview visuals (P1b): ground shadow + quality tier
  'preview.shadows.toggle': 'Shadows',
  'preview.shadows.toggleTitle': 'Ground contact shadow (off by default in wire/xray modes)',
  'preview.quality.fast': 'Fast',
  'preview.quality.accurate': 'Accurate',
  'preview.quality.toggleTitle': 'Toggle preview quality (accurate doubles tessellation)',

  // 3D preview section plane (P1c): drag the handle to slice
  'preview.section.toggle': 'Section',
  'preview.section.toggleTitle': 'Toggle section plane (drag the handle in the viewport)',
  'preview.section.axisTitle': 'Section axis',
  'preview.section.sliderTitle': 'Section position',
  'preview.section.sliderAria': 'Section position',

  // P2a before/after compare: pre-task version ghost overlay
  'preview.ghost.toggle': 'Compare',
  'preview.ghost.toggleTitle': 'Overlay the pre-task version (translucent)',
  'preview.ghost.unavailableTitle': 'No pre-task version to compare: start an AI task first',
  'preview.ghost.preTask': 'pre-task',
  'preview.ghost.cornerTag': 'Translucent = {label} version',

  // P2b explode view: spread parts along "part centroid − overall centroid"
  'preview.explode.toggle': 'Explode',
  'preview.explode.toggleTitle': 'Spread parts apart (slider controls amount, 0 = off)',
  'preview.explode.sliderAria': 'Explosion amount',
  'preview.explode.sliderTitle': 'Explosion amount (0 = off)',

  // ThemedDialog (P4-B)
  'dialog.confirm': 'Confirm',
  'dialog.cancel': 'Cancel',

  // P7c: naming guidance when saving a new blank project (ThemedDialog on needs_save_as)
  'saveAs.dialogTitle': 'Save project',
  'saveAs.dialogMessage': 'Name the new project. It will be saved automatically to the workspace or your configured output directory.',
  'saveAs.defaultName': 'Untitled Object',
  'saveAs.nameRequired': 'Project name is required.',

  // P0-C: create-intent confirmation when a project is open
  'chat.confirmCreateTitle': 'Generate a new GDL object?',
  'chat.confirmCreateMessage': 'A separate new project will be created. The currently open "{name}" will not be modified. To modify the current project instead, cancel and rephrase with "change ... to ...".',
  'chat.confirmCreateOk': 'Create new project',

  // P4-C empty states
  'preview.empty.title': 'No model to preview yet',
  'preview.empty.hint': 'Open or create a project, or describe a component in the AI panel',
  'editor.empty.title': 'No script loaded',
  'editor.empty.hint': 'Open a project from the workspace, or generate with AI',
  'assistant.empty.title': 'Start your GDL workflow',
  'assistant.empty.hint': 'Generate or modify Archicad components in natural language',
  'assistant.empty.example.generate': 'Generate a parametric bookshelf',
  'assistant.empty.example.modify': 'Change the shelf count to 5',
  'assistant.empty.example.explain': 'Explain what this GDL code does',

  // P4-D column collapse / bottom drawer
  'layout.collapseLeft': 'Collapse left panel',
  'layout.collapseRight': 'Collapse right panel',
  'layout.expandLeft': 'Expand left panel',
  'layout.expandRight': 'Expand right panel',
  'drawer.collapseTitle': 'Collapse bottom drawer',
  'drawer.expandTitle': 'Expand bottom drawer',
}
