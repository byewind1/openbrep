import type { CompilerSettings } from '../../api/types'

interface CompilerSettingsPanelProps {
  draft: CompilerSettings
  onChange: (settings: CompilerSettings) => void
  onBrowseCompilerFile: () => void
  onBrowseOutputDirectory: () => void
}

export function CompilerSettingsPanel({
  draft,
  onChange,
  onBrowseCompilerFile,
  onBrowseOutputDirectory,
}: CompilerSettingsPanelProps) {
  return (
    <>
      <label className="settings-row">
        <span>Mode</span>
        <select
          aria-label="Compiler mode"
          value={draft.mode}
          onChange={(event) =>
            onChange({
              ...draft,
              mode: event.currentTarget.value === 'lp' ? 'lp' : 'mock',
            })
          }
        >
          <option value="mock">Mock</option>
          <option value="lp">LP</option>
        </select>
      </label>
      <label className="settings-field">
        <span>LP_XMLConverter</span>
        <div className="settings-path-row">
          <input
            type="text"
            placeholder="/Applications/.../LP_XMLConverter"
            value={draft.converter_path}
            disabled={draft.mode !== 'lp'}
            onChange={(event) =>
              onChange({
                ...draft,
                converter_path: event.currentTarget.value,
              })
            }
          />
          <button type="button" disabled={draft.mode !== 'lp'} onClick={onBrowseCompilerFile}>
            Browse
          </button>
        </div>
      </label>
      <label className="settings-field">
        <span>Output directory</span>
        <div className="settings-path-row">
          <input
            type="text"
            placeholder="Project sibling /output"
            value={draft.output_dir}
            onChange={(event) =>
              onChange({
                ...draft,
                output_dir: event.currentTarget.value,
              })
            }
          />
          <button type="button" onClick={onBrowseOutputDirectory}>
            Browse
          </button>
        </div>
      </label>
    </>
  )
}
