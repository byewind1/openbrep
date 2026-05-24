import type { WorkbenchProject } from '../api/types'

interface TopMenuProps {
  project: WorkbenchProject | null
  hasDraftChanges: boolean
  onApply: () => void
  applying: boolean
}

export function TopMenu({ project, hasDraftChanges, onApply, applying }: TopMenuProps) {
  return (
    <header className="topbar">
      <div className="brand-lockup">
        <span className="brand-mark">OB</span>
        <div>
          <strong>OpenBrep Studio</strong>
          <span>{project?.name ?? 'No project loaded'}</span>
        </div>
      </div>
      <nav className="menu-row" aria-label="Workbench actions">
        <button>文件</button>
        <button>生成</button>
        <button>编译</button>
        <button>Archicad</button>
        <button>设置</button>
      </nav>
      <div className="topbar-status">
        <span className={hasDraftChanges ? 'status-pill changed' : 'status-pill'}>{hasDraftChanges ? '临时预览' : '已保存'}</span>
        <button className="primary-action" disabled={!hasDraftChanges || applying} onClick={onApply}>
          {applying ? '应用中' : '应用到 HSF'}
        </button>
      </div>
    </header>
  )
}
