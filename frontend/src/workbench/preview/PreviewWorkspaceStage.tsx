import { lazy, Suspense } from 'react'
import type { Preview2DPayload, PreviewPayload } from '../../api/types'
import { useWorkbenchStore } from '../../state/useWorkbenchStore'
import { PanelEmpty } from '../../components/PanelEmpty'
import { useT } from '../../i18n'
import { usePreview2DSource, usePreviewSource } from './usePreviewSource'

const ScriptEditor = lazy(() => import('../../components/ScriptEditor').then((m) => ({ default: m.ScriptEditor })))
const PreviewViewport = lazy(() => import('../../components/PreviewViewport').then((m) => ({ default: m.PreviewViewport })))
const Preview2DViewport = lazy(() => import('../../components/Preview2DViewport').then((m) => ({ default: m.Preview2DViewport })))

/** 中间栏三视图：脚本编辑器 / 3D 预览 / 2D 预览 */
export type CenterView = 'editor' | '3d' | '2d'

interface PreviewWorkspaceStageProps {
  centerView: CenterView
  preview: PreviewPayload | null
  preview2d: Preview2DPayload | null
  warnings: string[]
  activeScriptName: string | null
  activeScriptContent: string
  hasDirtyScript: boolean
  hasDirtyScripts: boolean
  activeFocusLine: number | null
  /** P1e：相关代码段末行（整段亮显用），单行定位为 null */
  activeFocusEndLine: number | null
  activeFocusKey: number | null
  onCenterViewChange: (view: CenterView) => void
  onFloatPreview: () => void
  onChangeScript: (content: string) => void
  onRefreshPreview?: () => void
  onLoadPreview2D: () => void
  /** 3D 预览选中 mesh 后跳转 GDL 源码段（scriptName 如 "3d.gdl"；endLine 相关段末行） */
  onRevealSource?: (scriptName: string, lineNumber: number, endLine?: number | null) => void
}

export function PreviewWorkspaceStage({
  centerView,
  preview,
  preview2d,
  warnings,
  activeScriptName,
  activeScriptContent,
  hasDirtyScript,
  hasDirtyScripts,
  activeFocusLine,
  activeFocusEndLine,
  activeFocusKey,
  onCenterViewChange,
  onFloatPreview,
  onChangeScript,
  onRefreshPreview,
  onLoadPreview2D,
  onRevealSource,
}: PreviewWorkspaceStageProps) {
  const t = useT()
  // P1b：质量档从 store 取，不经过 props 倒灌
  const previewQuality = useWorkbenchStore((state) => state.previewQuality)
  const setPreviewQuality = useWorkbenchStore((state) => state.setPreviewQuality)
  // P2a：任务前版本 ghost 快照，视口只读消费
  const previewGhost = useWorkbenchStore((state) => state.previewGhost)
  const previewGhostLabel = useWorkbenchStore((state) => state.previewGhostLabel)
  // Archicad 权威预览：来源切换/缓存/错误在 store，这里只解析出当前应显示的 payload
  const { preview: displayPreview, sourceControl } = usePreviewSource(preview)
  const { preview: displayPreview2d, sourceControl: sourceControl2d } = usePreview2DSource(preview2d)

  function selectView(view: CenterView) {
    // 首次切到 2D 且还没有数据时触发加载（与右栏 2D tab 同一 action）
    if (view === '2d' && preview2d === null) onLoadPreview2D()
    onCenterViewChange(view)
  }

  // 三个舞台常驻 DOM、用 display 切换：保证来回切换不丢 3D 相机视角和编辑器滚动位置
  return (
    <div className="center-stage">
      <div className="rail-tabs center-stage-tabs" role="tablist" aria-label={t('stage.tabsAria')}>
        <StageTab view="editor" centerView={centerView} onSelect={selectView} label={t('stage.view.script')} />
        <StageTab view="3d" centerView={centerView} onSelect={selectView} label={t('stage.view.3d')} />
        <StageTab view="2d" centerView={centerView} onSelect={selectView} label={t('stage.view.2d')} />
      </div>
      <div className="center-stage-body">
        <section className={`workbench-main-stage preview-workspace-stage${centerView === '3d' ? '' : ' stage-hidden'}`}>
          <Suspense fallback={<div className="viewport-loading" />}>
            <PreviewViewport
              preview={displayPreview}
              warnings={displayPreview?.warnings ?? warnings}
              variant="workspace"
              expanded
              hasDirtyScripts={hasDirtyScripts}
              onCollapse={() => onCenterViewChange('editor')}
              onFloat={onFloatPreview}
              onRevealSource={onRevealSource}
              quality={previewQuality}
              onQualityChange={(quality) => void setPreviewQuality(quality)}
              previewGhost={previewGhost}
              previewGhostLabel={previewGhostLabel}
              sourceControl={sourceControl}
              actions={
                onRefreshPreview ? (
                  <button
                    type="button"
                    className="viewport-action-button"
                    onClick={onRefreshPreview}
                    title="Update preview from current editor buffer"
                  >
                    Update
                  </button>
                ) : null
              }
            />
          </Suspense>
        </section>
        <section className={`workbench-main-stage preview-2d-stage${centerView === '2d' ? '' : ' stage-hidden'}`}>
          <Suspense fallback={<div className="viewport-loading" />}>
            <Preview2DViewport
              preview={displayPreview2d}
              warnings={displayPreview2d?.warnings ?? warnings}
              sourceControl={sourceControl2d}
            />
          </Suspense>
        </section>
        <section className={`workbench-main-stage editor-stage${centerView === 'editor' ? '' : ' stage-hidden'}`}>
          {activeScriptName ? (
            <Suspense fallback={<div className="editor-loading" />}>
              <ScriptEditor
                scriptName={activeScriptName}
                content={activeScriptContent}
                onChange={onChangeScript}
                isDirty={hasDirtyScript}
                focusLine={activeFocusLine}
                focusEndLine={activeFocusEndLine}
                focusKey={activeFocusKey}
              />
            </Suspense>
          ) : (
            <PanelEmpty icon="✎" title={t('editor.empty.title')} hint={t('editor.empty.hint')} />
          )}
        </section>
      </div>
    </div>
  )
}

function StageTab({
  view,
  centerView,
  onSelect,
  label,
}: {
  view: CenterView
  centerView: CenterView
  onSelect: (view: CenterView) => void
  label: string
}) {
  return (
    <button
      type="button"
      role="tab"
      className={`rail-tab${centerView === view ? ' active' : ''}`}
      aria-selected={centerView === view}
      onClick={() => onSelect(view)}
    >
      {label}
    </button>
  )
}
