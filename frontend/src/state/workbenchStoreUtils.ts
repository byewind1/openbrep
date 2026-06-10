import type { CompileIssue, CompileResult, CompilerSettings, LlmSettings, MockCompileResponse, ProjectScript, WorkbenchSnapshot } from '../api/types'

export const SCRIPT_FALLBACK_ORDER = ['3d.gdl', '2d.gdl', '1d.gdl', 'vl.gdl', 'pr.gdl', 'ui.gdl', 'paramlist.xml', 'libpartdata.xml']

export function defaultLlmSettings(): LlmSettings {
  return {
    model: 'glm-4-flash',
    models: ['glm-4-flash'],
    api_key: '',
    api_base: '',
    max_retries: 5,
    assistant_settings: '',
  }
}

export function hydrateSnapshot(snapshot: WorkbenchSnapshot, fallbackCompiler: CompilerSettings, fallbackLlm: LlmSettings) {
  return {
    // 旧版 backend 的 snapshot 不带 epoch 字段时保持原值，避免误触发过期守卫
    ...(snapshot.project_epoch !== undefined
      ? { projectEpoch: snapshot.project_epoch, sessionId: snapshot.session_id ?? null }
      : {}),
    project: snapshot.project,
    parameters: snapshot.parameters,
    preview: snapshot.preview,
    preview2d: null,
    warnings: snapshot.warnings ?? snapshot.preview?.warnings ?? [],
    compilerSettings: snapshot.compiler ?? fallbackCompiler,
    llmSettings: snapshot.llm ?? fallbackLlm,
    draftParameters: {},
    scripts: [],
    activeScriptName: null,
    scriptContents: {},
    dirtyScripts: {},
    revisions: [],
    latestRevisionId: null,
    revisionLoading: false,
    mockCompileResult: null,
  }
}

export function selectPreferredScript(scripts: ProjectScript[], current: string | null) {
  if (current && scripts.some((script) => script.name === current && script.exists)) return current
  for (const preferred of SCRIPT_FALLBACK_ORDER) {
    if (scripts.some((script) => script.name === preferred && script.exists)) return preferred
  }
  return scripts.find((script) => script.exists)?.name ?? null
}

export function pruneDirtyScripts(dirtyScripts: Record<string, boolean>, scripts: ProjectScript[]) {
  const allowed = new Set(scripts.map((script) => script.name))
  return Object.fromEntries(Object.entries(dirtyScripts).filter(([name]) => allowed.has(name)))
}

export function normalizeScriptName(path: string) {
  const trimmed = path.trim()
  if (!trimmed) return ''
  return trimmed.split('/').pop() ?? trimmed
}

export function buildMockCompileSummary(result: MockCompileResponse) {
  if (!result.success && result.error) return `Mock compile failed: ${result.error}`
  const errors = countIssues(result.issues, 'error')
  const warnings = countIssues(result.issues, 'warning')
  if (errors === 0 && warnings === 0) return `Mock compile passed in ${result.duration_ms} ms`
  return `Mock compile finished in ${result.duration_ms} ms (${errors} errors, ${warnings} warnings)`
}

export function compileIssuesFromResult(result: CompileResult): CompileIssue[] {
  const compile = result.compile
  if (!compile) {
    return result.error ? [{ severity: 'error', script: '', line: null, message: result.error }] : []
  }
  return [
    ...(compile.errors ?? []).map((message) => ({ severity: 'error', script: '', line: null, message })),
    ...(compile.warnings ?? []).map((message) => ({ severity: 'warning', script: '', line: null, message })),
  ]
}

export function formatAssistantRequestError(error: string | undefined, fallback: string) {
  const message = (error || fallback).trim()
  return isLlmConfigurationError(message) ? `LLM settings error: ${message}` : message
}

export function isLlmConfigurationError(message: string) {
  const normalized = message.toLowerCase()
  return (
    normalized.includes('llm 配置错误') ||
    normalized.includes('llm 认证失败') ||
    normalized.includes('api key') ||
    normalized.includes('authentication') ||
    normalized.includes('base_url') ||
    normalized.includes('provider') ||
    normalized.includes('litellm') ||
    normalized.includes('model')
  )
}

function countIssues(issues: CompileIssue[], severity: string) {
  return issues.filter((issue) => issue.severity === severity).length
}
