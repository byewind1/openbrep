/**
 * 中文字典 — 键的源头（source of truth）。
 * en.ts 的键集合必须与此文件完全一致，由 i18n.test.ts 与 TypeScript 双重校验。
 */
export const zh = {
  // TopMenu
  'topMenu.save': '保存',
  'topMenu.compile': '编译',
  'topMenu.build': '构建',
  'topMenu.mockCompile': '模拟编译',
  'topMenu.settings': '设置',
  'topMenu.apply': '应用',
  'topMenu.status.saved': '已保存',
  'topMenu.status.unsaved': '未保存',
  'topMenu.status.empty': '空项目',
  'topMenu.status.dirty': '有改动',
  'topMenu.status.clean': '无改动',
  'topMenu.status.params': '参数改动',
  'topMenu.status.stable': '参数稳定',
  'topMenu.status.savedAt': '已保存 {time}',

  // SettingsDrawer header
  'settings.header.title': '设置',
  'settings.header.saving': '保存中',
  'settings.header.unsaved': '未保存',
  'settings.header.saved': '已保存',
  'settings.header.error': '错误',
  'settings.header.reloadTitle': '从磁盘重新加载配置',
  'settings.header.saveButton': '保存',
  'settings.header.closeTitle': '关闭',
  'settings.header.closeAriaLabel': '关闭设置',
  'settings.header.drawerAriaLabel': '工作台设置',
  'settings.header.resizeAriaLabel': '调整设置面板宽度',

  // SettingsDrawer section titles
  'settings.section.interface': '界面',
  'settings.section.ai': 'AI',
  'settings.section.compiler': '编译器',
  'settings.section.workspace': '工作区',
  'settings.section.git': 'Git',
  'settings.section.memory': '记忆',
  'settings.section.knowledge': '知识库',

  // SettingsDrawer section summaries
  'settings.summary.compilerLp': 'LP',
  'settings.summary.compilerMock': 'Mock',
  'settings.summary.aiNoModel': '无模型',
  'settings.summary.workspaceRecentCount': '最近 {count} 个',
  'settings.summary.gitNotInitialized': '未初始化',
  'settings.summary.gitEnabled': '已启用',
  'settings.summary.gitDisabled': '已禁用',
  'settings.summary.memoryLessonCount': '{count} 条错题',
  'settings.summary.knowledgeDash': '—',
  'settings.summary.knowledgeFreePro': '免费 {free} · 专业 {pro}',
  'settings.summary.knowledgeFreeNoPro': '免费 {free} · 无专业版',

  // InterfaceSettingsPanel
  'interfacePanel.languageLabel': '语言',
  'interfacePanel.description': '选择工作台显示语言，切换后立即生效',
} as const

export type LocaleKey = keyof typeof zh
