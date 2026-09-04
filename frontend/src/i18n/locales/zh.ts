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
  // D16 模型可见性开关面板（可见性是纯 UI 策展，存 localStorage，不写 config.toml）
  'settings.ai.visibility.hint': '开关只控制模型在聊天模型菜单中的显示；点击模型名设为默认（显式保存）。',
  'settings.ai.visibility.count': '{visible}/{total} 可见',
  'settings.ai.visibility.toggle': '切换 {model} 在选择器中的显示',
  'settings.ai.visibility.current': '当前',
  'settings.ai.copyError': '复制错误信息',
  'settings.ai.copied': '已复制',
  // Codex BYOA（D1）：ChatGPT 订阅登录与动态模型
  'settings.ai.codex.sectionTitle': 'ChatGPT Codex（订阅）',
  'settings.ai.codex.modifyNotOpen': 'MODIFY 已可用，但仍处于观察期；需要本机 Codex CLI 和 ChatGPT 账号。',
  'settings.ai.codex.collapsedSummary': '连接状态：{state}',
  'settings.ai.codex.login': '连接我的 ChatGPT',
  'settings.ai.codex.loginPending': '登录中…',
  'settings.ai.codex.loginPendingHint': '请在打开的浏览器中完成登录。登录成功后本面板会自动刷新账户与可用模型。',
  'settings.ai.codex.loginFailed': '启动浏览器登录失败',
  'settings.ai.codex.logout': '断开连接',
  'settings.ai.codex.logoutFailed': '退出登录失败',
  'settings.ai.codex.connectedLabel': '已连接',
  'settings.ai.codex.notConnectedLabel': '未连接',
  'settings.ai.codex.noCli': '未检测到 Codex CLI。请先安装 Codex CLI（npm install -g @openai/codex）后重试。',
  'settings.ai.codex.errorUnknown': 'Codex 连接状态未知，请稍后重试。',
  'settings.ai.codex.modelsLabel': '可用模型（来自你的 ChatGPT 账户）',
  'settings.ai.codex.noModels': '账户暂无可用模型。',
  'settings.ai.codex.modelUnavailable': '当前 Codex 模型不可用：请先连接 ChatGPT 或重新登录。',
  'settings.ai.codex.cancelLogin': '取消登录',
  'settings.ai.codex.loginCancelled': '已取消登录。',
  'settings.ai.codex.deviceCode': '使用设备码登录',
  'settings.ai.codex.deviceCodeHint':
    '如果浏览器登录未打开或失败，可使用设备码：在任意浏览器打开验证网址并输入下方设备码。',
  'settings.ai.codex.deviceCodeUrl': '验证网址',
  'settings.ai.codex.deviceCodeValue': '设备码',
  'settings.ai.codex.deviceCodeCopied': '已复制',
  'settings.ai.codex.copyDeviceCode': '复制',
  'settings.ai.codex.crashed': 'Codex app-server 进程异常退出。点击「重启」恢复连接。',
  'settings.ai.codex.restart': '重启 app-server',
  'settings.ai.codex.restarting': '重启中…',
  'settings.ai.codex.restartFailed': '重启 app-server 失败',
  'settings.ai.codex.versionIncompatible':
    'Codex CLI 版本与 OpenBrep 不兼容。请升级 Codex CLI（npm install -g @openai/codex）后重试。',
  'settings.ai.codex.quotaExhausted':
    'ChatGPT 订阅额度已耗尽或已达到用量上限。请稍后重试、等待重置，或切换到其他模型/提供商。',
  'settings.ai.codex.rateLimits': '用量',
  'settings.ai.codex.rateLimitsPlan': '套餐',
  'settings.ai.codex.rateLimitsUsed': '已用',
  'settings.ai.codex.rateLimitsUnlimited': '不限量',
  'settings.ai.codex.rateLimitsHasCredits': '有额度',
  'settings.ai.codex.rateLimitsReached': '已触顶',
  'settings.ai.codex.effortLabel': '推理强度（reasoning effort）',
  'settings.ai.codex.effortDefault': '不覆盖（模型默认）',
  'settings.ai.codex.effortSave': '保存 effort',
  'settings.ai.codex.effortSaved': '已保存（Fixed 模式将严格使用该组合）',
  'settings.ai.codex.effortSaveFailed': '保存失败',
  'settings.ai.codex.routingModeLabel': '模型路由',
  'settings.ai.codex.routingModeFixed': 'Fixed（默认）',
  'settings.ai.codex.routingModeAuto': 'Auto（实验性）',
  'settings.ai.codex.routingModeSave': '保存路由模式',
  'settings.ai.codex.routingModeSaved': '路由模式已保存',
  'settings.ai.codex.routingModeSaveFailed': '路由模式保存失败',
  'settings.ai.codex.routingModeFixedHint': '严格使用已保存的模型与 effort；这是默认行为。',
  'settings.ai.codex.routingModeAutoHint': '仅按 D8 实测组合路由；复杂任务可能进行一次 Luna high 未实测升级。',

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

  // P11 参数面板：参数 UI / 参数脚本 视图切换 + 枚举下拉
  'parameter.view.params': '参数',
  'parameter.view.script': '参数脚本',
  'parameter.enumFallback': '当前值：{value}（不在 VALUES 列表）',
  'parameter.script.saved': '已保存',
  'parameter.script.unsaved': '未保存',
  'parameter.script.save': '保存',
  'parameter.script.saving': '保存中',

  // P6a 跨项目聊天记录导入（历史抽屉）
  'assistant.history.import': '导入…',
  'assistant.history.importTitle': '从其他项目导入聊天记录',
  'assistant.history.importDisabledTitle': '先打开一个项目才能导入聊天记录',
  'assistant.history.importHintNoWorkspace': '先在工作区面板挂载工作区',
  'assistant.history.importNoSources': '没有可导入的其他项目',
  'assistant.history.importSourceAria': '从项目 {name} 导入',
  'assistant.history.importConfirmTitle': '导入聊天记录',
  'assistant.history.importConfirmMessage': '从项目「{source}」导入聊天记录到当前项目「{current}」？将追加合并，不覆盖现有记录。',
  'assistant.history.importConfirmLabel': '确认导入',

  // P6b 聊天记录整理成指令（历史抽屉）
  'assistant.history.distill': '整理成指令',
  'assistant.history.distillTitle': '把聊天记录整理成一段可直接发给 AI 的指令（填入输入框草稿，审阅后自己发送）',
  'assistant.history.distillNoProject': '先打开一个项目才能整理聊天记录',
  'assistant.history.distillNoHistory': '当前项目没有聊天记录可整理',
  'assistant.history.distillBusy': '整理中…',

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

  // D16 会话级模型 pill（切换只作用当前会话，不写 config.toml）
  'assistant.modelPill.override': '会话覆盖',
  'assistant.modelPill.resetDefault': '恢复默认',
  'assistant.modelPill.editVisibility': '编辑模型可见性…',
  'assistant.modelPill.searchPlaceholder': '搜索全部模型…',
  'assistant.modelPill.unavailable': '当前模型不可用（缺少 API Key 或配置不完整）',
  'assistant.modelPill.menuLabel': '选择会话模型',

  // MODIFY acceptance report (V5)
  'assistant.acceptance.title': '验收报告',
  'assistant.acceptance.geometry': '几何对比',
  'assistant.acceptance.before': '修改前',
  'assistant.acceptance.after': '修改后',
  'assistant.acceptance.meshCount': '几何体数量',
  'assistant.acceptance.bbox': '包围盒尺寸',
  'assistant.acceptance.counts2d': '平面元素（线/多边形/圆/弧）',

  // 读图提取卡片（P5d-1，只读）：vision 提取结果
  'vision.extraction.title': '读图提取',
  'vision.extraction.degraded': '【分析失败已降级】',
  'vision.extraction.criticDegraded': '【critic 校验已降级】',
  'vision.extraction.lowConfidence': '低置信',
  'vision.extraction.evidenceHint': 'critic 修正依据',
  'vision.extraction.reusedFrom': '复用自',

  // P5d-2 提取确认门（可编辑确认卡）
  'assistant.extraction.title': '读图结果确认',
  'assistant.extraction.subtitle': '模型从参考图读到的信息；required / critic_checks 字段可编辑，确认后继续生成。',
  'assistant.extraction.confirm': '确认并生成',
  'assistant.extraction.cancel': '取消',
  'assistant.extraction.keepRest': '其余字段保持原值',

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

  // P7c 新建空白项目保存时的命名引导（needs_save_as 响应处弹 ThemedDialog）
  'saveAs.dialogTitle': '保存项目',
  'saveAs.dialogMessage': '为新建项目命名，保存后自动放到工作区或设置的输出目录。',
  'saveAs.defaultName': '未命名构件',
  'saveAs.nameRequired': '项目名不能为空。',

  // P0-C：有项目打开时的生成意图确认门
  'chat.confirmCreateTitle': '生成新 GDL 构件？',
  'chat.confirmCreateMessage': '将创建独立的新项目，当前打开的《{name}》不会被修改。想修改当前项目，请取消并改用「把…改成…」的说法。',
  'chat.confirmCreateOk': '新建项目',

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
