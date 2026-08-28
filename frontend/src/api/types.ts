/** 预览质量档（P1b）：fast 分段少、accurate 分段翻倍（后端白名单校验） */
export type PreviewQuality = 'fast' | 'accurate'

export type ParameterType =
  | 'Length'
  | 'Angle'
  | 'RealNum'
  | 'Integer'
  | 'Boolean'
  | 'String'
  | 'Material'
  | 'PenColor'
  | 'FillPattern'
  | 'LineType'
  | string

export interface WorkbenchProject {
  name: string
  source?: string
  path?: string
}

export interface WorkbenchParameter {
  name: string
  type_tag: ParameterType
  description: string
  value: string
  is_fixed: boolean
  /** P11：vl.gdl VALUES 枚举（保持脚本声明顺序与原始类型）；无声明时为 null/undefined */
  options?: Array<string | number> | null
  /** P11：vl.gdl VALUES RANGE 约束（[min, max]（或含步长），原样透传）；无声明时为 null/undefined */
  range?: number[] | null
}

export interface ProjectScript {
  name: string
  path: string
  exists: boolean
  size: number
}

export interface RecentProject {
  path: string
  name?: string
  parent_dir?: string
  exists: boolean
}

export interface ProjectRevision {
  revision_id: string
  project_name: string
  gsm_name: string
  created_at: string
  message: string
  file_count: number
  trigger: string
  intent: string
  user_instruction: string
  changed_files: string[]
  parent_revision_id: string | null
  compile: Record<string, unknown>
  explanation: string
  is_latest: boolean
}

export interface PreviewSourceRef {
  script_type: string
  line: number
  command: string
  label: string
  /** 相关代码段（P1e）：生成该 mesh 的外围块区间（FOR…NEXT / IF…ENDIF /
   *  GOSUB 子程序），行号含端点；顶层命令为单行 (line, line)。可选，向后兼容 */
  segment_start?: number | null
  segment_end?: number | null
}

export interface PreviewMesh {
  name: string
  vertices: number[][]
  faces: number[][]
  /** 生成该 mesh 的 GDL 命令打点（openbrep/gdl_previewer.py 逐命令记录）；
   *  RULED 焊接合并等产物可能没有，此时无法溯源跳转 */
  source_ref?: PreviewSourceRef | null
}

export interface PreviewPayload {
  meshes: PreviewMesh[]
  wires: number[][][]
  warnings?: string[]
  verification?: PreviewVerification
}

export interface Preview2DPayload {
  lines: Array<{ from: [number, number]; to: [number, number] }>
  polygons: Array<Array<[number, number]>>
  circles: Array<{ cx: number; cy: number; r: number }>
  arcs: Array<{ cx: number; cy: number; r: number; a0: number; a1: number }>
  warnings?: string[]
  verification?: PreviewVerification
}

export interface PreviewVerification {
  source: 'saved' | 'editor_buffer'
  script_overrides: string[]
}

export interface CompilerSettings {
  mode: 'mock' | 'lp'
  converter_path: string
  output_dir: string
}

export interface LlmSettings {
  model: string
  model_available?: boolean
  models: string[]
  model_options?: LlmModelOption[]
  model_groups?: {
    custom: LlmModelOption[]
    official: LlmModelOption[]
  }
  provider_templates?: LlmProviderTemplate[]
  api_key: string
  api_base: string
  max_retries: number
  assistant_settings: string
  /** D6：Fixed 模式已保存的 reasoning effort（只对 openai-codex 模型有意义；空 = 不覆盖模型默认） */
  reasoning_effort?: string
  /** D9：Codex 路由显式 opt-in；缺失/未知均由后端按 fixed 处理。 */
  codex_routing_mode?: 'fixed' | 'auto'
  /** D1：ChatGPT Codex（openai-codex）连接状态。provider 未拉起时为 null */
  codex?: CodexStatus | null
  /** D10：Codex MODIFY 动态工具桥接 feature flag（默认 false；false 时 UI 不提供 MODIFY/DEBUG 入口） */
  codex_modify_enabled?: boolean
}

// ── Codex BYOA（D1）：ChatGPT 订阅登录与动态模型目录 ───────────────────────
// 后端只返回枚举化状态 + 脱敏账户信息；token/JWT/account id/authUrl/
// auth 路径绝不进入 API payload（后端安全不变量，前端类型同样不定义这些字段）。

export type CodexState =
  | 'no_cli'
  | 'version_incompatible'
  | 'signed_out'
  | 'signed_in'
  | 'login_started'
  | 'quota_exhausted'
  | 'crashed'
  | 'error'

export type CodexLoginMethod = 'chatgpt' | 'chatgptDeviceCode'

export interface CodexAccount {
  /** 脱敏邮箱（如 jo***@example.com），仅用于用户确认连的是自己的账号 */
  email_masked: string | null
  plan_type: string
}

/** D2：account/rateLimits/read 的脱敏摘要（后端只暴露产品需要的字段） */
export interface CodexRateLimits {
  reached: boolean
  reached_type?: string | null
  spend_control_reached?: boolean | null
  plan_type?: string | null
  /** 用量百分比（0-100）；上游未返回时为 null */
  used_percent?: number | null
  window_duration_mins?: number | null
  resets_at?: number | null
  credits?: {
    has_credits?: boolean | null
    unlimited?: boolean | null
  }
}

export interface CodexStatus {
  ok?: boolean
  state: CodexState
  codex_available: boolean
  connected: boolean
  account: CodexAccount | null
  /** D2：脱敏额度摘要（已登录且上游返回时存在） */
  rate_limits?: CodexRateLimits | null
  /** crashed 状态下为 true：UI 提供「重启」动作 */
  restartable?: boolean
  /** P0-1：登录失败/过期提示（completion failure 后短暂可见的可操作文案） */
  login_error?: string
  /** 当前配置模型（llmSettings.model），便于 UI 判断可用性 */
  model?: string
  model_available?: boolean
  error?: string
}

export interface CodexModelInfo {
  /** provider-qualified id：openai-codex/<model>（与 API-key OpenAI 分离） */
  id: string
  label: string
  model: string
  display_name?: string
  hidden?: boolean
  specialty?: string | null
  /** D6：该模型支持的 reasoning effort（只来自 model/list.supportedReasoningEfforts，不硬编码） */
  supported_reasoning_efforts?: { effort: string; description?: string }[]
  /** D6：该模型的默认 reasoning effort（model/list.defaultReasoningEffort） */
  default_reasoning_effort?: string
}

export interface CodexLoginStartResult {
  ok: boolean
  state?: string
  method?: CodexLoginMethod
  error?: string
}

/** D2：设备码登录（用户显式选择）——verification_url + user_code 是用户完成
 *  授权所必需的产品信息（在浏览器输入该码），不是 token/请求头 */
export interface CodexDeviceCodeResult extends CodexLoginStartResult {
  method?: 'chatgptDeviceCode'
  verification_url?: string
  user_code?: string
}

export interface CodexLoginCancelResult {
  ok: boolean
  state?: string
  error?: string
}

export interface CodexLogoutResult {
  ok: boolean
  state?: string
  error?: string
}

export interface CodexRestartResult {
  ok: boolean
  state?: CodexState
  error?: string
}

export interface CodexRateLimitsResult {
  ok: boolean
  rate_limits?: CodexRateLimits
  code?: string
  error?: string
}

export interface CodexModelsResult {
  ok: boolean
  models?: CodexModelInfo[]
  code?: string
  error?: string
}

export interface LlmProviderTemplate {
  name: string
  api: string
  api_mode: string
  env_vars?: string[]
  console_url?: string
  models?: string[]
}

export interface LlmModelOption {
  id: string
  label: string
  kind: 'official' | 'custom'
  provider: string
  target_model?: string
  protocol?: string
  api_base?: string
  has_api_key?: boolean
}

export interface WorkspaceProject {
  name: string
  path: string
  parameter_count: number
  scripts_present: string[]
  latest_revision_id: string | null
  origin: { imported_from: string; imported_kind: string; imported_at: string } | null
  artifact_count: number
  active?: boolean
}

export interface WorkspaceInfo {
  path: string
  project_count: number
  projects: WorkspaceProject[]
}

export interface WorkspaceScanResult {
  ok?: boolean
  error?: string
  code?: string
  workspace?: string | null
  project_count?: number
  projects?: WorkspaceProject[]
  missing_zones?: string[]
}

export interface WorkspaceSearchHit {
  project: string
  location: string
  line: number | null
  snippet: string
}

export interface WorkspaceTrashResult {
  ok?: boolean
  error?: string
  code?: string
  trashed_to?: string
  workspace?: string
}

export interface WorkspaceSearchResult {
  ok?: boolean
  error?: string
  code?: string
  query: string
  hits: WorkspaceSearchHit[]
  hit_count: number
  workspace?: string
}

export interface WorkbenchSnapshot {
  ok?: boolean
  project: WorkbenchProject | null
  parameters: WorkbenchParameter[]
  preview: PreviewPayload
  warnings: string[]
  compiler?: CompilerSettings
  llm?: LlmSettings
  error?: string
  session_id?: string
  project_epoch?: number
  /** 后端 snapshot 的工作区块：无附着为 null（P3-d1） */
  workspace?: WorkspaceInfo | null
}

export interface HsfExportResult extends WorkbenchSnapshot {
  ok: boolean
  saved_to?: string
  cancelled?: boolean
  needs_save_as?: boolean
}

export interface ApplyResult extends WorkbenchSnapshot {
  ok: boolean
  changed: Record<string, unknown>
}

export interface AddParameterRequest {
  name: string
  type_tag: 'Length' | 'RealNum' | 'Integer' | 'Boolean' | 'String'
  value: unknown
  description?: string
}

export interface AddParameterResult extends WorkbenchSnapshot {
  ok: boolean
  added?: WorkbenchParameter
}

export interface UpdateParameterRequest {
  name: string
  new_name?: string
  type_tag?: 'Length' | 'RealNum' | 'Integer' | 'Boolean' | 'String'
  value?: unknown
  description?: string
}

export interface UpdateParameterResult extends WorkbenchSnapshot {
  ok: boolean
  updated?: WorkbenchParameter
}

export interface DeleteParameterResult extends WorkbenchSnapshot {
  ok: boolean
  deleted?: string
}

export interface ValidateParametersResult {
  ok: boolean
  issues: string[]
  error?: string
}

export interface CompileInfo {
  success: boolean
  mode: string
  output_path: string
  stdout: string
  stderr: string
  errors: string[]
  warnings: string[]
  gsm_size_bytes?: number | null
  parameter_count?: number | null
}

export interface CompileResult {
  ok: boolean
  compile?: CompileInfo
  error?: string
}

export interface CompileIssue {
  severity: 'info' | 'warning' | 'error' | string
  script: string
  line: number | null
  message: string
}

export interface MockCompileResponse {
  ok?: boolean
  success: boolean
  mode: string
  issues: CompileIssue[]
  duration_ms: number
  output_path?: string
  gsm_size_bytes?: number | null
  parameter_count?: number | null
  error?: string
}

export interface RevealArtifactResult {
  ok: boolean
  path?: string
  error?: string
}

export interface CompilerSettingsResult {
  ok: boolean
  compiler?: CompilerSettings
  error?: string
}

export interface RuntimeSettingsResult {
  ok: boolean
  compiler?: CompilerSettings
  llm?: LlmSettings
  error?: string
}

export interface ConfigRevisionResult {
  ok: boolean
  revision?: string
  error?: string
}

export interface TapirStatus {
  import_ok: boolean
  available: boolean
  archicad_connected: boolean
  tapir_available: boolean
  version: string
  message: string
  selected_guids: string[]
  selected_details: Array<Record<string, unknown>>
  selected_params: Array<Record<string, unknown>>
  param_edits: Record<string, unknown>
  last_error: string
  last_sync_at: string
}

export interface TapirStatusResult {
  ok: boolean
  tapir?: TapirStatus
  error?: string
}

export interface TapirActionResult extends TapirStatusResult {
  message?: string
}

export interface LlmSettingsResult {
  ok: boolean
  llm?: LlmSettings
  error?: string
}

export interface LlmConnectionTestResult {
  ok: boolean
  message?: string
  model?: string
  duration_ms?: number
  category?: string
  error?: string
  detail?: string
}

export interface DirectoryChoiceResult extends Partial<WorkbenchSnapshot> {
  ok: boolean
  path?: string
  cancelled?: boolean
  error?: string
}

export interface FileChoiceResult {
  ok: boolean
  path?: string
  compiler?: CompilerSettings
  cancelled?: boolean
  error?: string
}

// ── MODIFY 验收报告（V5）：确定性自然语言摘要 + 前后几何对比 ─────────────
export interface ModifyAcceptance {
  summary_lines: string[]
  geometry_delta: {
    status: 'ok' | 'unchanged' | 'before_unavailable' | 'after_unavailable'
    reason?: string
    mesh_count?: { from: number | null; to: number | null }
    bbox_size?: { from: number[] | null; to: number[] | null }
    counts_2d?: {
      from: { lines: number; polygons: number; circles: number; arcs: number } | null
      to: { lines: number; polygons: number; circles: number; arcs: number } | null
    }
  }
  checks: Array<{ name: string; status: string; detail: string }>
}

export interface AssistantMessage {
  role: 'user' | 'assistant'
  content: string
  // 以下字段仅在当前会话内存活：后端聊天历史只持久化 role/content，
  // 刷新后摘要卡降级为 content 里的纯文本（含 Changed files 后缀兜底）。
  changedFiles?: string[]
  errorCategory?: 'llm' | 'compile' | 'general'
  verification?: VerificationReport
  interrupted?: boolean
  // 流式生成过程中的结构化思考步骤，用于时间线展示
  thinkingSteps?: AssistantThinkingStep[]
  // MODIFY 验收报告（V5）：结果卡渲染摘要 + 前后几何对比
  acceptance?: ModifyAcceptance
  // 已发送图片（仅当前会话内存活，与 changedFiles 同语义；用于消息气泡缩略 chip）
  images?: AssistantImageAttachment[]
  // 读图提取卡片（P5d-1，只读）：vision 提取结果渲染（仅当前会话内存活）
  visionExtractions?: VisionExtraction[]
}

// ── 读图提取卡片（P5d-1，只读）─────────────────────────────────────────────
// 与后端 vision_analysis_done 事件 payload.extraction（及
// TaskResult.metadata["vision_extractions"] 条目）同构，snake_case 原样透传。
export interface VisionExtractionCorrection {
  field: string
  old: unknown
  new: unknown
  evidence?: string
}

export interface VisionExtraction {
  token?: string
  skipped?: boolean
  schema_name?: string
  fields?: Record<string, unknown>
  confidence?: Record<string, string>
  corrections?: VisionExtractionCorrection[]
  degraded?: boolean
  critic_degraded?: boolean
  raw_description?: string
  sha256?: string
  // P5d-2：schema 元数据（required + critic_checks = 可编辑确认卡的可编辑范围）
  required?: string[]
  critic_checks?: string[]
  // P5e：MODIFY 复用标记（D7）——提取结果来自内容哈希命中的缓存，标注来源模型
  reused_from_model?: string
}

// ── Streaming events from /api/assistant/generate?stream=1 (SSE) ───────────
export type AssistantStreamEventType =
  | 'status'
  | 'tool_call'
  | 'plan'
  | 'compile_result'
  | 'preview_result'
  | 'assistant_delta'
  | 'done'
  | 'error'

export type ThinkingStage =
  | 'understand'
  | 'think'
  | 'locate'
  | 'plan'
  | 'modify'
  | 'compile'
  | 'preview'
  | 'verify'
  | 'retry'
  | 'budget'
  | 'cancel'
  | 'done'
  | string

export interface AssistantStreamEvent {
  type: AssistantStreamEventType
  data: Record<string, unknown>
}

export interface AssistantThinkingStep {
  type: 'status' | 'tool_call' | 'plan' | 'assistant_delta'
  stage?: ThinkingStage
  message: string
  detail?: string
  ok?: boolean
  // plan 阶段专用
  intentSummary?: string
  affectedFiles?: string[]
  parameterChanges?: Array<{ name: string; from?: string; to?: string }>
  strategy?: string
  // 计划确认门（V3）：面向用户的非代码语言改动描述与风险
  userVisibleChanges?: string[]
  risk?: string
}

// ── Verification report (Phase 3/4/5 self-correcting agent evidence) ──────
// Mirrors openbrep/verification.py VerificationReport.to_dict().
export type VerificationConfidence = 'low' | 'medium' | 'high'
export type VerificationCheckStatus = 'pass' | 'fail' | 'unknown' | 'not_run'

export interface VerificationLineError {
  line_number: number
  severity: 'error' | 'warning'
  message: string
}

export interface VerificationCheck {
  name: string
  check_type: string // static | lint | compile | plan_check
  status: VerificationCheckStatus
  detail: string
  auto_repairable: boolean
  line_errors?: VerificationLineError[]
}

export interface VerificationReport {
  intent: string
  goal: string
  passed: boolean
  confidence: VerificationConfidence
  graph_powered?: boolean
  counts: Record<VerificationCheckStatus, number>
  checks: VerificationCheck[]
  errors_caught: string[]
  fixes_applied: string[]
  remaining_risks: string[]
}

export interface AssistantImageAttachment {
  name: string
  mime: string
  b64: string
  /** 本地路径来源（如 /Users/ren/pic.jpg）；前端不校验，发送后由后端报错指名路径 */
  path?: string
}

export interface AssistantResult {
  ok: boolean
  assistant?: {
    kind: string
    reply: string
  }
  error?: string
}

export interface AssistantHistoryResult {
  ok: boolean
  messages: AssistantMessage[]
  error?: string
}

export interface SaveAssistantHistoryResult {
  ok: boolean
  count: number
  error?: string
}

export interface ImportAssistantHistoryResult {
  ok: boolean
  imported?: number
  source_name?: string
  error?: string
}

export interface DistillAssistantHistoryResult {
  ok: boolean
  instruction?: string
  message_count?: number
  error?: string
}

export interface AssistantCodeBlock {
  path: string
  script_name: string
  content: string
}

export interface AssistantCodeBlocksResult {
  ok: boolean
  blocks: AssistantCodeBlock[]
  error?: string
}

export interface ProjectMemoryStatus {
  memory_root: string
  chat_count: number
  lesson_count: number
  has_learned_skill: boolean
  total_bytes: number
}

export interface ProjectMemoryStatusResult {
  ok: boolean
  memory?: ProjectMemoryStatus
  error?: string
}

export interface ClearProjectMemoryResult {
  ok: boolean
  before?: ProjectMemoryStatus
  error?: string
}

export interface ErrorLesson {
  fingerprint: string
  category: string
  summary: string
  guidance: string
  example: string
  count: number
  first_seen: string
  last_seen: string
  source: string
  project_name: string
  raw_excerpt: string
  ignored?: boolean
}

export interface ProjectLessonsResult {
  ok: boolean
  lessons: ErrorLesson[]
  error?: string
}

export interface LearningSummaryResult {
  ok: boolean
  lesson_count: number
  path: string
  message: string
}

export interface SummarizeMemoryResult {
  ok: boolean
  summary?: LearningSummaryResult
  skill?: string
  error?: string
}

export interface DeleteMemoryLessonResult {
  ok: boolean
  deleted?: string
  remaining_count?: number
  error?: string
}

export interface UpdateMemoryLessonRequest {
  category?: string
  summary?: string
  guidance?: string
  example?: string
}

export interface UpdateMemoryLessonResult {
  ok: boolean
  lesson?: ErrorLesson
  error?: string
}

export interface IgnoreMemoryLessonResult {
  ok: boolean
  ignored?: string
  remaining_count?: number
  error?: string
}

// ── 计划确认门（V3）：MODIFY 先出非代码语言计划，用户确认后才执行 ─────────
export interface PendingPlan {
  intent_summary: string
  user_visible_changes: string[]
  affected_files: string[]
  risk: string
}

// ── 模式级 skill 提案（P2-d）：成功 CREATE/MODIFY 后提炼，用户确认后才落盘晋升 ──
export interface SkillProposal {
  name: string
  pattern_type: string
  content: string
  slice?: {
    params?: Record<string, unknown>
    scripts?: Record<string, string>
  } | null
  evidence?: {
    intent?: string
    changed_files?: string[]
    project?: string
  } | null
}

export interface SkillProposalConfirmResult {
  ok: boolean
  skill?: string
  verified?: boolean
  gate?: string
  status?: string
  path?: string
  discarded?: boolean
  message?: string
  code?: string
  error?: string
}

export interface GenerateResult {
  ok: boolean
  assistant?: {
    kind: string
    reply: string
    changed_files: string[]
    intent: string
    verification?: VerificationReport | null
    acceptance?: ModifyAcceptance | null
  } | null
  preview?: PreviewPayload | null
  warnings?: string[]
  events?: Array<{ type: string; data: unknown }>
  error?: string
  // 计划确认门（V3）
  awaiting_confirmation?: boolean
  pending_plan?: PendingPlan | null
  plan_failed?: boolean
  // 模式级 skill 提案（P2-d）
  skill_proposal?: SkillProposal | null
}

export interface CreateProjectResult extends WorkbenchSnapshot {
  ok: boolean
  assistant?: {
    kind: string
    reply: string
    changed_files: string[]
    intent: string
    verification?: VerificationReport | null
  }
  events?: Array<{ type: string; data: unknown }>
  error?: string
  skill_proposal?: SkillProposal | null
  // P5d-2 提取确认门：读图完成、等用户确认/编辑（extractions = 可编辑卡片数据源）
  awaiting_extraction_confirmation?: boolean
  extractions?: VisionExtraction[]
}

/** P5d-2 提取确认门：待用户确认/编辑的读图结果（含原消息与图片，确认时原样重发） */
export interface PendingExtraction {
  extractions: VisionExtraction[]
  message: string
  images: AssistantImageAttachment[]
}

export interface ProjectScriptsResponse {
  scripts: ProjectScript[]
}

export interface RecentProjectsResponse {
  ok: boolean
  projects: RecentProject[]
  error?: string
}

export interface ProjectRevisionsResponse {
  ok: boolean
  revisions: ProjectRevision[]
  latest_revision_id?: string | null
  error?: string
}

export interface SaveRevisionResponse {
  ok: boolean
  revision?: ProjectRevision
  latest_revision_id?: string | null
  error?: string
}

export interface RestoreRevisionResponse extends Partial<WorkbenchSnapshot> {
  ok: boolean
  restored_revision_id?: string
  revision?: ProjectRevision
  latest_revision_id?: string | null
  error?: string
}

export interface ProjectGitStatus {
  enabled: boolean
  initialized: boolean
  dirty: boolean
  changes: string[]
  last_commit: string
}

export interface ProjectGitResponse {
  ok: boolean
  git: ProjectGitStatus
  message?: string
  error?: string
}

export interface ProjectScriptContentResponse {
  ok?: boolean
  name: string
  path: string
  content: string
  error?: string
}

export interface SaveScriptResponse {
  ok?: boolean
  success: boolean
  saved_at: string
  error?: string
}

export interface KnowledgeStatus {
  ok: boolean
  has_pro: boolean
  free_doc_count: number
  pro_doc_count: number
  pro_doc_names: string[]
  pro_dir: string
  pro_dir_exists: boolean
  message?: string
  error?: string
}
