import { lazy, Suspense } from 'react'
import { AssistantPanel } from '../../components/AssistantPanel'
import type {
  AssistantImageAttachment,
  AssistantMessage,
  Preview2DPayload,
  PreviewPayload,
  TapirStatus,
} from '../../api/types'
import { useWorkbenchStore } from '../../state/useWorkbenchStore'
import { usePreview2DSource, usePreviewSource } from '../preview/usePreviewSource'

const PreviewViewport = lazy(() => import('../../components/PreviewViewport').then((m) => ({ default: m.PreviewViewport })))
const Preview2DViewport = lazy(() => import('../../components/Preview2DViewport').then((m) => ({ default: m.Preview2DViewport })))
const TapirPanel = lazy(() => import('../tapir/TapirPanel').then((m) => ({ default: m.TapirPanel })))

type ActiveRailPanel = '3d' | '2d' | 'inspect' | 'ai'

interface WorkbenchRightRailProps {
  activeRailPanel: ActiveRailPanel
  preview: PreviewPayload | null
  preview2d: Preview2DPayload | null
  warnings: string[]
  hasDirtyScripts: boolean
  tapirStatus: TapirStatus | null
  tapirBusy: boolean
  assistantMessages: AssistantMessage[]
  assistantBusy: boolean
  pendingPlan: import('../../api/types').PendingPlan | null
  onConfirmPlan: (approve: boolean) => void
  pendingExtraction: import('../../api/types').PendingExtraction | null
  onConfirmExtraction: (extractions: import('../../api/types').VisionExtraction[], approve: boolean) => void
  pendingSkillProposal: import('../../api/types').SkillProposal | null
  onConfirmSkillProposal: (approve: boolean) => void
  onSetActiveRailPanel: (panel: ActiveRailPanel) => void
  onLoadPreview3D: () => void
  onLoadPreview2D: () => void
  onExpandPreview: () => void
  onFloatPreview: () => void
  onRefreshTapirStatus: () => void
  onReloadTapirLibraries: () => void
  onSyncTapirSelection: () => void
  onHighlightTapirSelection: () => void
  onLoadTapirParameters: () => void
  onApplyTapirParameters: () => void
  hasProject: boolean
  interruptedContext?: { message: string; intent: string } | null
  onChat: (message: string, images?: AssistantImageAttachment[]) => void
  onStop: () => void
  onClearAssistantHistory: () => void
  onDeleteAssistantMessages?: (indices: number[]) => void | Promise<void>
  onAdoptAssistantCode: (index: number) => void
  onOpenScript?: (scriptName: string) => void
  onSaveRevision?: (message: string) => void
  onRevealLine?: (scriptName: string, lineNumber: number, endLine?: number | null) => void
  modelOptions?: import('../../api/types').LlmModelOption[]
  currentModel?: string
  /** D16：聊天侧模型切换 = 会话级（不写 config.toml） */
  onSessionModelChange?: (model: string) => Promise<void>
  llmSettings?: import('../../api/types').LlmSettings
  codexCatalog?: { connected: boolean; models: import('../../api/types').CodexModelInfo[]; loaded: boolean }
  onResetSessionModel?: () => Promise<void>
  onLoadCodexCatalog?: () => Promise<void>
  onOpenModelSettings?: () => void
  // P6a：跨项目聊天记录导入
  workspace?: import('../../api/types').WorkspaceInfo | null
  currentProjectPath?: string | null
  onImportAssistantHistory?: (sourcePath: string) => void
  // P6b：整理聊天记录为指令 → 草稿通道
  draftSeed?: string | null
  onConsumeDraftSeed?: () => void
  onDistillAssistantHistory?: () => void | Promise<void>
}

export function WorkbenchRightRail({
  activeRailPanel,
  preview,
  preview2d,
  warnings,
  hasDirtyScripts,
  tapirStatus,
  tapirBusy,
  assistantMessages,
  assistantBusy,
  pendingPlan,
  onConfirmPlan,
  pendingExtraction,
  onConfirmExtraction,
  pendingSkillProposal,
  onConfirmSkillProposal,
  onSetActiveRailPanel,
  onLoadPreview3D,
  onLoadPreview2D,
  onExpandPreview,
  onFloatPreview,
  onRefreshTapirStatus,
  onReloadTapirLibraries,
  onSyncTapirSelection,
  onHighlightTapirSelection,
  onLoadTapirParameters,
  onApplyTapirParameters,
  hasProject,
  interruptedContext,
  onChat,
  onStop,
  onClearAssistantHistory,
  onDeleteAssistantMessages,
  onAdoptAssistantCode,
  onOpenScript,
  onSaveRevision,
  onRevealLine,
  modelOptions,
  currentModel,
  onSessionModelChange,
  llmSettings,
  codexCatalog,
  onResetSessionModel,
  onLoadCodexCatalog,
  onOpenModelSettings,
  workspace = null,
  currentProjectPath = null,
  onImportAssistantHistory,
  draftSeed,
  onConsumeDraftSeed,
  onDistillAssistantHistory,
}: WorkbenchRightRailProps) {
  // P1b：预览质量档是 store 会话态，视口只消费，不走 props 倒灌
  const previewQuality = useWorkbenchStore((state) => state.previewQuality)
  const setPreviewQuality = useWorkbenchStore((state) => state.setPreviewQuality)
  // P2a：任务前版本 ghost 快照，视口只读消费
  const previewGhost = useWorkbenchStore((state) => state.previewGhost)
  // ST03：delivery 卡动作直接挂 store，不另建第二套工作台 state
  const recoverDeliveryBefore = useWorkbenchStore((state) => state.recoverDeliveryBefore)
  const viewRevisionDiff = useWorkbenchStore((state) => state.viewRevisionDiff)
  const continueDelivery = useWorkbenchStore((state) => state.continueDelivery)
  const previewGhostLabel = useWorkbenchStore((state) => state.previewGhostLabel)
  // Archicad 权威预览：来源切换/缓存/错误在 store，这里只解析出当前应显示的 payload
  const { preview: displayPreview, sourceControl } = usePreviewSource(preview)
  const { preview: displayPreview2d, sourceControl: sourceControl2d } = usePreview2DSource(preview2d)
  return (
    <aside className="workbench-right-rail right-rail">
      <div className="rail-tabs" role="tablist" aria-label="Right rail panels">
        <RailTab panel="3d" activeRailPanel={activeRailPanel} onSelect={onSetActiveRailPanel}>
          3D
        </RailTab>
        <RailTab
          panel="2d"
          activeRailPanel={activeRailPanel}
          onSelect={(panel) => {
            onSetActiveRailPanel(panel)
            onLoadPreview2D()
          }}
        >
          2D
        </RailTab>
        <RailTab panel="inspect" activeRailPanel={activeRailPanel} onSelect={onSetActiveRailPanel}>
          Inspect
        </RailTab>
        <RailTab panel="ai" activeRailPanel={activeRailPanel} onSelect={onSetActiveRailPanel}>
          AI
        </RailTab>
      </div>
      <div className="rail-panel viewport-panel">
        {activeRailPanel === '3d' ? (
          <Suspense fallback={<div className="viewport-loading" />}>
            <PreviewViewport
              preview={displayPreview}
              warnings={displayPreview?.warnings ?? warnings}
              hasDirtyScripts={hasDirtyScripts}
              onExpand={onExpandPreview}
              onFloat={onFloatPreview}
              onRevealSource={onRevealLine}
              quality={previewQuality}
              onQualityChange={(quality) => void setPreviewQuality(quality)}
              previewGhost={previewGhost}
              previewGhostLabel={previewGhostLabel}
              sourceControl={sourceControl}
              actions={(
                <button type="button" className="viewport-action-button" onClick={onLoadPreview3D} title="Update preview from current editor buffer">
                  Update
                </button>
              )}
            />
          </Suspense>
        ) : activeRailPanel === '2d' ? (
          <Suspense fallback={<div className="viewport-loading" />}>
            <Preview2DViewport
              preview={displayPreview2d}
              warnings={displayPreview2d?.warnings ?? warnings}
              sourceControl={sourceControl2d}
            />
          </Suspense>
        ) : activeRailPanel === 'inspect' ? (
          <Suspense fallback={<div className="viewport-loading" />}>
            <TapirPanel
              status={tapirStatus}
              busy={tapirBusy}
              onRefresh={onRefreshTapirStatus}
              onReloadLibraries={onReloadTapirLibraries}
              onSyncSelection={onSyncTapirSelection}
              onHighlightSelection={onHighlightTapirSelection}
              onLoadParameters={onLoadTapirParameters}
              onApplyParameters={onApplyTapirParameters}
            />
          </Suspense>
        ) : (
          <AssistantPanel
            messages={assistantMessages}
            busy={assistantBusy}
            hasProject={hasProject}
            interruptedContext={interruptedContext}
            onChat={onChat}
            onStop={onStop}
            onClearHistory={onClearAssistantHistory}
            onDeleteMessages={onDeleteAssistantMessages}
            onAdoptCode={onAdoptAssistantCode}
            onOpenScript={onOpenScript}
            onSaveRevision={onSaveRevision}
            onRevealLine={onRevealLine}
            onRecoverDelivery={async (presentation, policy) => {
              await recoverDeliveryBefore(presentation, { draftPolicy: policy, source: 'delivery' })
            }}
            onViewDeliveryDiff={async (presentation) => {
              const fromId = presentation.before_revision_id
              if (!fromId) return null
              // F1：partial 无 after → before→工作源；有 after → before→after；禁止 before→before
              let toId: string | null = presentation.after_revision_id ?? null
              if (presentation.diff_target === 'working') toId = null
              else if (!toId && presentation.diff_target !== 'after') toId = null
              if (toId === fromId) toId = null
              return viewRevisionDiff(fromId, toId)
            }}
            onContinueDelivery={(payload) => {
              void continueDelivery({
                originRunId: payload.originRunId,
                originalInstruction: payload.originalInstruction,
                intent: payload.presentation.state ?? undefined,
              })
            }}
            modelOptions={modelOptions}
            currentModel={currentModel}
            onSessionModelChange={onSessionModelChange}
            llmSettings={llmSettings}
            codexCatalog={codexCatalog}
            onResetSessionModel={onResetSessionModel}
            onLoadCodexCatalog={onLoadCodexCatalog}
            onOpenModelSettings={onOpenModelSettings}
            pendingPlan={pendingPlan}
            onConfirmPlan={onConfirmPlan}
            pendingExtraction={pendingExtraction}
            onConfirmExtraction={onConfirmExtraction}
            pendingSkillProposal={pendingSkillProposal}
            onConfirmSkillProposal={onConfirmSkillProposal}
            workspace={workspace}
            currentProjectPath={currentProjectPath}
            onImportAssistantHistory={onImportAssistantHistory}
            draftSeed={draftSeed}
            onConsumeDraftSeed={onConsumeDraftSeed}
            onDistillAssistantHistory={onDistillAssistantHistory}
          />
        )}
      </div>
    </aside>
  )
}

function RailTab({
  panel,
  activeRailPanel,
  onSelect,
  children,
}: {
  panel: ActiveRailPanel
  activeRailPanel: ActiveRailPanel
  onSelect: (panel: ActiveRailPanel) => void
  children: string
}) {
  return (
    <button
      type="button"
      className={`rail-tab${activeRailPanel === panel ? ' active' : ''}`}
      aria-selected={activeRailPanel === panel}
      onClick={() => onSelect(panel)}
    >
      {children}
    </button>
  )
}
