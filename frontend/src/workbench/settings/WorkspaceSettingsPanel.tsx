import type { RecentProject } from '../../api/types'

interface WorkspaceSettingsPanelProps {
  recentProjects: RecentProject[]
  onOpenProjectPath: (path: string) => void
  onExportHsfProject: () => void
  onResetCurrentProject: () => void
}

export function WorkspaceSettingsPanel({
  recentProjects,
  onOpenProjectPath,
  onExportHsfProject,
  onResetCurrentProject,
}: WorkspaceSettingsPanelProps) {
  return (
    <>
      <div className="settings-field">
        <span>Recent HSF projects</span>
        <div className="recent-project-list">
          {recentProjects.length ? (
            recentProjects.map((project) => (
              <button
                type="button"
                className="recent-project-item"
                disabled={!project.exists}
                key={project.path}
                onClick={() => onOpenProjectPath(project.path)}
                title={project.path}
              >
                <span>{project.name || project.path}</span>
                {project.parent_dir ? <small>{project.parent_dir}</small> : null}
                {!project.exists ? <em>missing</em> : null}
              </button>
            ))
          ) : (
            <span className="settings-empty">No recent HSF projects</span>
          )}
        </div>
      </div>
      <div className="settings-submit-row">
        <button type="button" onClick={onExportHsfProject}>
          Export HSF
        </button>
        <button
          type="button"
          onClick={onResetCurrentProject}
          title="Reset the current workbench session without deleting files on disk"
        >
          Reset Current Project
        </button>
      </div>
    </>
  )
}
