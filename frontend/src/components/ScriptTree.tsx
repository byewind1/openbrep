import type { ProjectScript } from '../api/types'

const SCRIPT_ORDER = ['3d.gdl', '2d.gdl', '1d.gdl', 'vl.gdl', 'pr.gdl', 'ui.gdl', 'paramlist.xml', 'libpartdata.xml']

interface ScriptTreeProps {
  scripts: ProjectScript[]
  activeScript: string | null
  onSelect: (name: string) => void
  dirtyScripts: Record<string, boolean>
}

export function ScriptTree({ scripts, activeScript, onSelect, dirtyScripts }: ScriptTreeProps) {
  const sorted = [...scripts].sort((left, right) => SCRIPT_ORDER.indexOf(left.name) - SCRIPT_ORDER.indexOf(right.name))

  return (
    <section className="script-tree">
      <div className="script-tree-header">
        <span>Scripts</span>
      </div>
      <div className="script-tree-list">
        {sorted.map((script) => {
          const isActive = script.name === activeScript
          const isDirty = dirtyScripts[script.name]
          return (
            <button
              key={script.name}
              type="button"
              className={`script-tree-item${isActive ? ' active' : ''}${script.exists ? '' : ' empty'}`}
              disabled={!script.exists}
              onClick={() => onSelect(script.name)}
            >
              <span className="script-tree-name">{script.name}</span>
              {isDirty ? <span className="script-tree-dirty">●</span> : null}
              {!script.exists ? <span className="script-tree-meta">empty</span> : null}
            </button>
          )
        })}
      </div>
    </section>
  )
}
