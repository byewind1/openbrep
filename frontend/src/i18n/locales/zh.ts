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
  'topMenu.status.modelTitle': '当前 AI 模型 · 点击切换',

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

  // AiSettingsPanel
  'settings.ai.modelLabel': '模型',
  'settings.ai.searchPlaceholder': '搜索模型…',
  'settings.ai.groupCustom': '自定义',
  'settings.ai.groupOfficial': '官方',
  'settings.ai.noMatch': '无匹配模型',
  'settings.ai.modelUnavailable': '当前模型不可用（缺少 API Key 或配置不完整）。请点击下方「Edit config.toml」设置该模型。',
  'settings.ai.officialBaseNote': '说明：官方模型的 api_base 固定为官方端点（顶层 api_base 配置对它们不生效）；需要代理端点时请在 config.toml 的 [[llm.custom_providers]] 里配置。',
  'settings.ai.apiKeyLabel': 'API Key',
  'settings.ai.apiKeyPlaceholder': '输入该模型的 API Key',
  'settings.ai.apiKeyReplacePlaceholder': '已保存 Key，输入新值可替换',
  'settings.ai.saveKey': '保存 Key',
  'settings.ai.savingAndVerifying': '保存并验证中…',
  'settings.ai.keySaved': 'API Key 已保存',
  'settings.ai.keySavedConnectionOk': '✅ Key 已保存，连接正常 ({ms} ms)',
  'settings.ai.keySavedConnectionFailed': '❌ Key 已保存，但连接失败（详情见下方错误信息）',
  'settings.ai.keySaveFailed': 'API Key 保存失败',
  'settings.ai.confirmSwitch': '切换到 {model}？',
  'settings.ai.confirmYes': '确认切换',
  'settings.ai.confirmNo': '取消',
  'settings.ai.switchFailed': '模型切换失败',
  'settings.ai.copyError': '复制错误信息',
  'settings.ai.copied': '已复制',

  // InterfaceSettingsPanel
  'interfacePanel.languageLabel': '语言',
  'interfacePanel.description': '选择工作台显示语言，切换后立即生效',

  // WorkspacePanel
  'workspace.title': '工作区',
  'workspace.notAttached': '未附着工作区',
  'workspace.attachPlaceholder': '工作区目录路径',
  'workspace.attach': '附着',
  'workspace.searchPlaceholder': '跨项目搜索',
  'workspace.search': '搜索',
  'workspace.close': '解除附着',
  'workspace.refresh': '刷新',
  'workspace.projects': '{count} 个项目',
  'workspace.noProjects': '暂无项目',
  'workspace.searchResults': '搜索结果',
  'workspace.searchEmpty': '无命中',
  'workspace.badgeOrigin': '导入',
  'workspace.badgeArtifacts': '{count} 成品',
  'workspace.browse': '浏览…',
  'workspace.initAttach': '初始化并附着',
  'workspace.notWorkspaceHint': '该目录还不是 OpenBrep 工作区',
  'workspace.dismiss': '关闭',
  'workspace.delete': '移入回收站',
  'workspace.deleteConfirm': '把项目 {name} 移入回收站？可从 .openbrep/trash/ 手动恢复。',
  'workspace.deleteActiveDisabled': '当前打开的项目：请先切换到其他项目再删除',

  // Assistant plan confirmation gate (V3)
  'assistant.plan.title': '修改计划',
  'assistant.plan.userChanges': '将要发生的改动',
  'assistant.plan.affectedFiles': '影响文件',
  'assistant.plan.risk': '风险',
  'assistant.plan.confirm': '确认修改',
  'assistant.plan.cancel': '取消',

  // 模式级 skill 提案（P2-d）
  'assistant.skillProposal.title': '沉淀为 skill 提案',
  'assistant.skillProposal.evidence': '证据',
  'assistant.skillProposal.project': '来源项目',
  'assistant.skillProposal.approve': '批准沉淀',
  'assistant.skillProposal.ignore': '忽略',

  // MODIFY acceptance report (V5)
  'assistant.acceptance.title': '验收报告',
  'assistant.acceptance.geometry': '几何对比',
  'assistant.acceptance.before': '修改前',
  'assistant.acceptance.after': '修改后',
  'assistant.acceptance.meshCount': '几何体数量',
  'assistant.acceptance.bbox': '包围盒尺寸',
  'assistant.acceptance.counts2d': '平面元素（线/多边形/圆/弧）',

  // 3D 预览拾取（P1a）：点击 mesh 高亮 + 溯源跳转
  'preview.pick.noSource': '无源码溯源',
  'preview.pick.jumpToSource': '跳转到源码',
  'preview.pick.dismiss': '取消选中',
  'preview.pick.barAriaLabel': '选中构件信息',

  // 3D 预览部件面板（P1d）：Blender outliner 式部件列表
  'preview.parts.title': '部件',
  'preview.parts.toggle': '切换部件面板',
  'preview.parts.hide': '隐藏部件',
  'preview.parts.show': '显示部件',

  // 3D 预览视觉（P1b）：接地阴影 + 质量档
  'preview.shadows.toggle': '阴影',
  'preview.shadows.toggleTitle': '接地软阴影（wire/xray 模式默认关）',
  'preview.quality.fast': '快速',
  'preview.quality.accurate': '精细',
  'preview.quality.toggleTitle': '切换预览质量档（accurate 分段翻倍）',

  // 3D 预览剖切（P1c）：剖切面推拉
  'preview.section.toggle': '剖切',
  'preview.section.toggleTitle': '开启/关闭剖切面（视口内拖手柄推拉）',
  'preview.section.axisTitle': '剖切轴向',
  'preview.section.sliderTitle': '剖切面位置',
  'preview.section.sliderAria': '剖切面位置',

  // P2a 修改前后对比：任务前版本 ghost 叠加
  'preview.ghost.toggle': '对比',
  'preview.ghost.toggleTitle': '叠加任务前版本（半透明）',
  'preview.ghost.unavailableTitle': '无任务前版本可对比：先发起一次 AI 修改',
  'preview.ghost.preTask': '任务前',
  'preview.ghost.cornerTag': '半透明 = {label} 版本',

  // P2b 爆炸图：按部件沿"部件质心 − 整体质心"方向散开
  'preview.explode.toggle': '爆炸',
  'preview.explode.toggleTitle': '按部件散开模型（滑杆控制程度，0 关闭）',
  'preview.explode.sliderAria': '爆炸程度',
  'preview.explode.sliderTitle': '爆炸程度（0 = 关闭）',

  // ThemedDialog (P4-B)
  'dialog.confirm': '确定',
  'dialog.cancel': '取消',

  // P4-C 空态
  'preview.empty.title': '还没有可预览的模型',
  'preview.empty.hint': '打开/新建项目，或在 AI 面板描述一个构件',
  'editor.empty.title': '未打开脚本',
  'editor.empty.hint': '从工作区打开项目，或用 AI 生成',
  'assistant.empty.title': '开始你的 GDL 工作流',
  'assistant.empty.hint': '用自然语言生成或修改 Archicad 构件',
  'assistant.empty.example.generate': '生成一个参数化书架',
  'assistant.empty.example.modify': '把层板数改成 5',
  'assistant.empty.example.explain': '解释这段 GDL 代码是什么意思',

  // P4-D 左右栏折叠 / 底部抽屉
  'layout.collapseLeft': '收起左栏',
  'layout.collapseRight': '收起右栏',
  'layout.expandLeft': '展开左栏',
  'layout.expandRight': '展开右栏',
  'drawer.collapseTitle': '收起底部抽屉',
  'drawer.expandTitle': '展开底部抽屉',
} as const

export type LocaleKey = keyof typeof zh
