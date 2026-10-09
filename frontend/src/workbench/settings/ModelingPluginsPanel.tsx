import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  adoptModelingPlugin,
  fetchModelingPlugins,
  fetchModelingTools,
  installModelingPlugin,
  personalizeModelingPlugin,
  restoreModelingPlugin,
  saveModelingPluginMethod,
  saveModelingPluginManifest,
  setModelingPluginEnabled,
  type ModelingPluginInfo,
  type ModelingToolInfo,
} from '../../api/client'
import { useT } from '../../i18n'

interface Props { active: boolean; projectName: string | null }

export function ModelingPluginsPanel({ active, projectName }: Props) {
  const t = useT()
  const [plugins, setPlugins] = useState<ModelingPluginInfo[]>([])
  const [tools, setTools] = useState<ModelingToolInfo[]>([])
  const [loadIssues, setLoadIssues] = useState<{ skill_id: string; code: string; message: string }[]>([])
  const [selectionIssues, setSelectionIssues] = useState<{ code: string; field_path: string; message: string }[]>([])
  const [hasProject, setHasProject] = useState(false)
  const [draftEnabled, setDraftEnabled] = useState<Record<string, boolean>>({})
  const [editing, setEditing] = useState<string | null>(null)
  const [editingManifest, setEditingManifest] = useState<string | null>(null)
  const [methodDraft, setMethodDraft] = useState('')
  const [manifestDraft, setManifestDraft] = useState('')
  const [installPath, setInstallPath] = useState('')
  const [filterText, setFilterText] = useState('')
  const [restoreVersion, setRestoreVersion] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    const [pluginResult, toolResult] = await Promise.all([fetchModelingPlugins(), fetchModelingTools()])
    setPlugins(pluginResult.plugins ?? [])
    setHasProject(pluginResult.has_project ?? false)
    setTools(toolResult.tools ?? [])
    setLoadIssues(pluginResult.issues ?? [])
    setSelectionIssues(pluginResult.selection_issues ?? [])
    setDraftEnabled({})
    if (!pluginResult.ok) setError(pluginResult.error ?? t('settings.plugins.loadError'))
  }, [t])

  useEffect(() => { if (active) void refresh() }, [active, projectName, refresh])

  const rows = useMemo(() => {
    const preferred = new Map<string, ModelingPluginInfo>()
    for (const plugin of plugins) {
      const old = preferred.get(plugin.skill_id)
      const rank = (source: string) => source === 'personal' ? 4 : source === 'project' ? 3 : source === 'example' ? 2 : 1
      if (!old || rank(plugin.source) > rank(old.source)) preferred.set(plugin.skill_id, plugin)
    }
    return [...preferred.values()].sort((a, b) => a.name.localeCompare(b.name))
  }, [plugins])
  const filteredRows = useMemo(() => {
    const query = filterText.trim().toLocaleLowerCase()
    if (!query) return rows
    return rows.filter(plugin => [plugin.name, plugin.domain, plugin.skill_id, ...plugin.aliases].join(' ').toLocaleLowerCase().includes(query))
  }, [filterText, rows])

  async function run(action: () => Promise<{ ok: boolean; error?: string }>, success = t('settings.plugins.saved')) {
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await action()
      if (!result.ok) throw new Error(result.error || t('settings.plugins.actionFailed'))
      setMessage(success)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('settings.plugins.actionFailed'))
    } finally { setBusy(false) }
  }

  async function saveEnabledDraft() {
    setBusy(true); setError(''); setMessage('')
    try {
      for (const [skillId, enabled] of Object.entries(draftEnabled)) {
        const result = await setModelingPluginEnabled(skillId, enabled)
        if (!result.ok) throw new Error(result.error || t('settings.plugins.actionFailed'))
      }
      setMessage(t('settings.plugins.saved'))
      await refresh()
    } catch (reason) { setError(reason instanceof Error ? reason.message : t('settings.plugins.actionFailed')) }
    finally { setBusy(false) }
  }

  async function saveMethod(plugin: ModelingPluginInfo) {
    await run(() => saveModelingPluginMethod(plugin.skill_id, methodDraft, plugin.content_hash), t('settings.plugins.versionSaved'))
    setEditing(null)
    setMethodDraft('')
  }

  async function saveManifest(plugin: ModelingPluginInfo) {
    let manifest: Record<string, unknown>
    try { manifest = JSON.parse(manifestDraft) as Record<string, unknown> }
    catch { setError(t('settings.plugins.manifestJsonError')); return }
    await run(() => saveModelingPluginManifest(plugin.skill_id, manifest, plugin.content_hash), t('settings.plugins.versionSaved'))
    setEditingManifest(null)
    setManifestDraft('')
  }

  return <div className="modeling-plugins-panel">
    <p className="settings-help">{t('settings.plugins.intro')}</p>
    {!hasProject && <p className="settings-help">{t('settings.plugins.noProject')}</p>}
    {error && <div className="settings-save-error" role="alert">{error}</div>}
    {message && <div className="settings-saved-state" role="status">{message}</div>}
    {loadIssues.map((issue, index) => <div className="settings-save-error" role="status" key={`${issue.skill_id}-${issue.code}-${index}`}>{issue.skill_id}: {issue.code} · {issue.message}</div>)}
    {selectionIssues.map((issue, index) => <div className="settings-save-error" role="status" key={`${issue.field_path}-${issue.code}-${index}`}>{issue.code}: {issue.message}</div>)}

    <section className="modeling-plugin-section">
      <h3>{t('settings.plugins.title')}</h3>
      <label className="modeling-plugin-filter">{t('settings.plugins.filter')}<input value={filterText} onChange={event => setFilterText(event.target.value)} /></label>
      {filteredRows.length === 0 ? <p className="settings-help">{rows.length ? t('settings.plugins.noMatches') : t('settings.plugins.empty')}</p> : filteredRows.map(plugin => {
        const enabled = draftEnabled[plugin.skill_id] ?? plugin.enabled
        const dirty = Object.hasOwn(draftEnabled, plugin.skill_id)
        const personal = plugins.find(item => item.skill_id === plugin.skill_id && item.source === 'personal')
        return <article className="modeling-plugin-card" key={plugin.skill_id}>
          <header>
            <div><strong>{plugin.name}</strong><span>{plugin.domain} · {plugin.source} · {plugin.version} · {plugin.status}</span></div>
            <span className={`plugin-state ${plugin.enabled ? 'is-on' : ''}`}>{plugin.selected ? (plugin.enabled ? t('settings.plugins.enabled') : t('settings.plugins.disabled')) : t('settings.plugins.notAdopted')}</span>
          </header>
          <div className="modeling-plugin-meta">
            <span>{t('settings.plugins.capabilities')}: {plugin.capabilities.join(', ') || '—'}</span>
            <span>{t('settings.plugins.pinned')}: {plugin.pinned_version ? `${plugin.pinned_version} · ${plugin.pinned_hash?.slice(0, 10)}` : '—'}</span>
            <span>{t('settings.plugins.intents')}: {plugin.intents.join(', ')}</span>
          </div>
          {plugin.recent_usage.length > 0 ? <p className="settings-help">{t('settings.plugins.recentUse')}: {plugin.recent_usage[0].stage} · {plugin.recent_usage[0].version} · {plugin.recent_usage[0].content_hash.slice(0, 10)} · {plugin.recent_usage[0].consumed} · {plugin.recent_usage[0].outcome} · {t('settings.plugins.toolsUsed')}: {plugin.recent_usage[0].tools_used.join(', ') || '—'}</p> : <p className="settings-help">{t('settings.plugins.notUsed')}</p>}
          {plugin.update_available && <p className="settings-dirty-state">{t('settings.plugins.updateAvailable')}</p>}
          {plugin.update_diff && <details className="modeling-plugin-diff"><summary>{t('settings.plugins.viewDiff')}</summary><pre>{plugin.update_diff}</pre></details>}
          {plugin.shadowed && <p className="settings-help">{t('settings.plugins.duplicateId')}</p>}
          <div className="modeling-plugin-actions">
            {!plugin.installed && <button type="button" disabled={busy} onClick={() => void run(() => personalizeModelingPlugin(plugin.skill_id, plugin.source), t('settings.plugins.copyCreated'))}>{t('settings.plugins.makeCopy')}</button>}
            {plugin.installed && hasProject && (!plugin.selected || plugin.update_available) && <button type="button" disabled={busy} onClick={() => void run(() => adoptModelingPlugin(plugin.skill_id, plugin.source), t('settings.plugins.adopted'))}>{t('settings.plugins.adopt')}</button>}
            {plugin.selected && <label className="settings-inline-toggle"><input type="checkbox" checked={enabled} onChange={event => setDraftEnabled(current => ({ ...current, [plugin.skill_id]: event.target.checked }))} />{t('settings.plugins.enable')}</label>}
            {personal && <>
              <button type="button" onClick={() => { setEditing(editing === plugin.skill_id ? null : plugin.skill_id); setMethodDraft(plugin.methodology); setEditingManifest(null) }}>{t('settings.plugins.edit')}</button>
              <button type="button" onClick={() => { setEditingManifest(editingManifest === plugin.skill_id ? null : plugin.skill_id); setManifestDraft(JSON.stringify(plugin.manifest, null, 2)); setEditing(null) }}>{t('settings.plugins.editDomainData')}</button>
            </>}
          </div>
          {personal && <div className="modeling-plugin-version">
            <label>{t('settings.plugins.versions')} <select value={restoreVersion[plugin.skill_id] ?? personal.version} onChange={event => setRestoreVersion(current => ({ ...current, [plugin.skill_id]: event.target.value }))}>{personal.versions.map(version => <option key={version}>{version}</option>)}</select></label>
            {restoreVersion[plugin.skill_id] && restoreVersion[plugin.skill_id] !== personal.version && <button type="button" disabled={busy} onClick={() => void run(() => restoreModelingPlugin(plugin.skill_id, restoreVersion[plugin.skill_id]), t('settings.plugins.restored'))}>{t('settings.plugins.restore')}</button>}
          </div>}
          {editing === plugin.skill_id && <div className="modeling-plugin-editor">
            <label>{t('settings.plugins.methodology')}<textarea value={methodDraft} onChange={event => setMethodDraft(event.target.value)} rows={14} /></label>
            <button type="button" disabled={busy || !methodDraft.trim()} onClick={() => void saveMethod(plugin)}>{t('settings.plugins.saveVersion')}</button>
            <button type="button" disabled={busy} onClick={() => setEditing(null)}>{t('settings.plugins.cancel')}</button>
          </div>}
          {editingManifest === plugin.skill_id && <div className="modeling-plugin-editor">
            <label>{t('settings.plugins.domainData')}<textarea value={manifestDraft} onChange={event => setManifestDraft(event.target.value)} rows={18} spellCheck={false} /></label>
            <p className="settings-help">{t('settings.plugins.manifestValidation')}</p>
            <button type="button" disabled={busy || !manifestDraft.trim()} onClick={() => void saveManifest(plugin)}>{t('settings.plugins.saveDomainVersion')}</button>
            <button type="button" disabled={busy} onClick={() => setEditingManifest(null)}>{t('settings.plugins.cancel')}</button>
          </div>}
        </article>
      })}
      {Object.keys(draftEnabled).length > 0 && <button type="button" disabled={busy} onClick={() => void saveEnabledDraft()}>{t('settings.plugins.saveSelection')}</button>}
      <div className="modeling-plugin-import">
        <label>{t('settings.plugins.importPath')}<input value={installPath} onChange={event => setInstallPath(event.target.value)} placeholder="/path/to/domain-skill" /></label>
        <button type="button" disabled={busy || !installPath.trim()} onClick={() => void run(() => installModelingPlugin(installPath.trim()), t('settings.plugins.installed'))}>{t('settings.plugins.install')}</button>
      </div>
    </section>

    <section className="modeling-plugin-section">
      <h3>{t('settings.plugins.toolsTitle')}</h3>
      {tools.map(tool => <div className="modeling-tool-row" key={tool.id}><span><strong>{tool.name}</strong><small>{tool.provider}{tool.tools?.length ? ` · ${tool.tools.join(', ')}` : ''}</small></span><em>{tool.status}</em></div>)}
    </section>
  </div>
}
