import { ParameterRail } from '../../components/ParameterRail'
import { ScriptTree } from '../../components/ScriptTree'
import { WorkspacePanel } from '../workspace/WorkspacePanel'
import type { WorkspaceInfo, WorkspaceSearchHit } from '../../api/types'
import type { AddParameterRequest, ProjectScript, UpdateParameterRequest, WorkbenchParameter } from '../../api/types'

interface WorkbenchLeftRailProps {
  workspace: WorkspaceInfo | null
  workspaceBusy: boolean
  workspaceInitHint: string | null
  workspaceSearching: boolean
  workspaceSearchQuery: string | null
  workspaceSearchHits: WorkspaceSearchHit[]
  onOpenWorkspace: (path: string) => void
  onInitWorkspace: (path: string) => void
  onCloseWorkspace: () => void
  onRefreshWorkspace: () => void
  onSearchWorkspace: (query: string) => void
  onBrowseDirectory: () => Promise<string | null>
  onDismissInitHint: () => void
  scripts: ProjectScript[]
  activeScriptName: string | null
  dirtyScripts: Record<string, boolean>
  groupedParameters: {
    dimensions: WorkbenchParameter[]
    properties: WorkbenchParameter[]
  }
  parameterIssues: string[]
  draftParameters: Record<string, unknown>
  applying: boolean
  onSelectScript: (name: string) => void
  onChangeParameter: (name: string, value: unknown) => void
  onApplyParameters: () => void
  onResetParameters: () => void
  onAddParameter: (parameter: AddParameterRequest) => Promise<boolean>
  onUpdateParameter: (parameter: UpdateParameterRequest) => Promise<boolean>
  onDeleteParameter: (name: string) => Promise<boolean>
  onValidateParameters: () => void
  onSelectProjectPath: (path: string) => void
}

export function WorkbenchLeftRail({
  workspace,
  workspaceBusy,
  workspaceInitHint,
  workspaceSearching,
  workspaceSearchQuery,
  workspaceSearchHits,
  onOpenWorkspace,
  onInitWorkspace,
  onCloseWorkspace,
  onRefreshWorkspace,
  onSearchWorkspace,
  onBrowseDirectory,
  onDismissInitHint,
  scripts,
  activeScriptName,
  dirtyScripts,
  groupedParameters,
  parameterIssues,
  draftParameters,
  applying,
  onSelectScript,
  onChangeParameter,
  onApplyParameters,
  onResetParameters,
  onAddParameter,
  onUpdateParameter,
  onDeleteParameter,
  onValidateParameters,
  onSelectProjectPath,
}: WorkbenchLeftRailProps) {
  return (
    <aside className="left-rail">
      <WorkspacePanel
        workspace={workspace}
        busy={workspaceBusy}
        workspaceInitHint={workspaceInitHint}
        searching={workspaceSearching}
        searchQuery={workspaceSearchQuery}
        searchHits={workspaceSearchHits}
        onOpenWorkspace={onOpenWorkspace}
        onInitWorkspace={onInitWorkspace}
        onCloseWorkspace={onCloseWorkspace}
        onRefreshWorkspace={onRefreshWorkspace}
        onSearchWorkspace={onSearchWorkspace}
        onBrowseDirectory={onBrowseDirectory}
        onDismissInitHint={onDismissInitHint}
        onLoadProjectPath={onSelectProjectPath}
      />
      <ScriptTree scripts={scripts} activeScript={activeScriptName} dirtyScripts={dirtyScripts} onSelect={onSelectScript} />
      <ParameterRail
        title="参数"
        sections={[
          { title: '尺寸', parameters: groupedParameters.dimensions },
          { title: '属性', parameters: groupedParameters.properties },
        ]}
        parameterIssues={parameterIssues}
        draftParameters={draftParameters}
        onChange={onChangeParameter}
        onApply={onApplyParameters}
        onReset={onResetParameters}
        onAddParameter={onAddParameter}
        onUpdateParameter={onUpdateParameter}
        onDeleteParameter={onDeleteParameter}
        onValidateParameters={onValidateParameters}
        applying={applying}
      />
    </aside>
  )
}
