import { ParameterRail } from '../../components/ParameterRail'
import { ScriptTree } from '../../components/ScriptTree'
import type { AddParameterRequest, ProjectScript, UpdateParameterRequest, WorkbenchParameter } from '../../api/types'

interface WorkbenchLeftRailProps {
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
}

export function WorkbenchLeftRail({
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
}: WorkbenchLeftRailProps) {
  return (
    <aside className="left-rail">
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
