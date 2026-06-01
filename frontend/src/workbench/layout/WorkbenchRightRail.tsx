import { AssistantPanel } from '../../components/AssistantPanel'
import { Preview2DViewport } from '../../components/Preview2DViewport'
import { PreviewViewport } from '../../components/PreviewViewport'
import type {
  AssistantImageAttachment,
  AssistantMessage,
  Preview2DPayload,
  PreviewPayload,
  TapirStatus,
} from '../../api/types'
import { TapirPanel } from '../tapir/TapirPanel'

type ActiveRailPanel = '3d' | '2d' | 'inspect' | 'ai'

interface WorkbenchRightRailProps {
  activeRailPanel: ActiveRailPanel
  preview: PreviewPayload | null
  preview2d: Preview2DPayload | null
  warnings: string[]
  tapirStatus: TapirStatus | null
  tapirBusy: boolean
  assistantMessages: AssistantMessage[]
  assistantBusy: boolean
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
  onSendAssistantMessage: (message: string) => void
  onCreateProjectFromPrompt: (message: string, image?: AssistantImageAttachment | null) => void
  onGenerateAssistantChanges: (message: string, image?: AssistantImageAttachment | null) => void
  onClearAssistantHistory: () => void
  onAdoptAssistantCode: (index: number) => void
}

export function WorkbenchRightRail({
  activeRailPanel,
  preview,
  preview2d,
  warnings,
  tapirStatus,
  tapirBusy,
  assistantMessages,
  assistantBusy,
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
  onSendAssistantMessage,
  onCreateProjectFromPrompt,
  onGenerateAssistantChanges,
  onClearAssistantHistory,
  onAdoptAssistantCode,
}: WorkbenchRightRailProps) {
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
          <PreviewViewport
            preview={preview}
            warnings={warnings}
            onExpand={onExpandPreview}
            onFloat={onFloatPreview}
            actions={(
              <button type="button" className="viewport-action-button" onClick={onLoadPreview3D} title="Update preview from current editor buffer">
                Update
              </button>
            )}
          />
        ) : activeRailPanel === '2d' ? (
          <Preview2DViewport preview={preview2d} warnings={warnings} />
        ) : activeRailPanel === 'inspect' ? (
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
        ) : (
          <AssistantPanel
            messages={assistantMessages}
            busy={assistantBusy}
            onSend={onSendAssistantMessage}
            onCreate={onCreateProjectFromPrompt}
            onGenerate={onGenerateAssistantChanges}
            onClearHistory={onClearAssistantHistory}
            onAdoptCode={onAdoptAssistantCode}
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
