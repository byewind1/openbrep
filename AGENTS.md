# OpenBrep Agent Instructions

This file is the repository entry point for AI coding agents.

## 接手任务前必读（Session Handoff）

**在开始任何开发工作前，先查阅以下内容，不要只靠 git log 推断背景：**

1. **最新 handoff 文档**（了解上次停在哪、未完成事项）：
   - 目录：`/Users/ren/Library/Mobile Documents/iCloud~md~obsidian/Documents/库/01-Projects/dev开发/OpenBrep 开发/`
   - 找最新的 `handoff-YYYY-MM-DD.md` 文件阅读

2. **相关主题文档**（了解历史决策和架构思考）：
   - 同上目录，找与当前任务相关的主题文档

3. **再看代码**。commit message 只是摘要，Obsidian 文档才有决策背景。

## 规划与决策文档的落盘规则

开发计划、调研记录、方案探索、派单文档和 handoff 默认属于本地决策背景，
应落盘到以下 Obsidian 目录，而不是仓库 `docs/`：

```text
/Users/ren/Library/Mobile Documents/iCloud~md~obsidian/Documents/库/01-Projects/dev开发/OpenBrep 开发/
```

规则：

- 用户要求“写开发计划”“做调研”“整理方案”“写 handoff”，但没有明确要求
  纳入仓库时，只写入上述本地目录，不得 `git add`、commit 或 push。
- 此类文档即使已经写完，也仍是待维护者审阅的工作材料；不能套用本文的
  Default Finish Sequence。
- Git commit 表示仓库层面的认可。只有维护者明确表示“纳入仓库”“形成 ADR”
  “更新仓库文档”或认可了具体草案后，才把相应内容迁入 `docs/` 并提交。
- 不要先把草案 push 再用 revert 当作移动文档的方式；应先选对落盘位置。
- 仓库本身的长期规范与用户文档，例如 `AGENTS.md`、架构指南、ADR、README、
  安装/发布文档，仍属于仓库交付物，按正常测试、commit、push 流程完成。
- 如果用户明确要求只负责框架、决策或 review，不负责实施，则不得把计划阶段
  扩张成业务代码实现；只交付本地计划和 review 结论。

---

中文维护者优先阅读：

- `docs/ARCHITECTURE.zh-CN.md`
- `docs/AI_DEVELOPMENT_GUIDE.zh-CN.md`

English / general agent references:

- `docs/ARCHITECTURE.md`
- `docs/AI_DEVELOPMENT_GUIDE.md`

## Project Mission

OpenBrep is a professional AI-assisted GDL workbench for Archicad users.
It is not a generic chatbot wrapper.

Core product contract:

```text
HSF-native source management
GDL generation, repair, explanation, and refactoring
compile-verified GSM output
traceable project and asset lifecycle
```

Product posture:

- OpenBrep must behave like a professional GDL workbench, not a demo toy or
  throwaway chatbot shell.
- File, source, compile, and settings flows need explicit state, clear
  persistence semantics, and visible user feedback.
- Do not silently write user configuration from incidental UI changes. Settings
  panels should use draft state plus an explicit save action, with saved/dirty
  feedback, unless the maintainer explicitly approves autosave for that exact
  control.
- Sample/demo objects may exist as explicit fixtures or examples, but must not
  replace real empty/new/open/save workspace states.

## Goal-Oriented Execution

Treat user requests as outcomes to deliver, not scripts to mechanically follow.
Before editing, infer the smallest useful success criteria for the current
request.

Default success criteria:

- The requested behavior, documentation, or product decision is actually
  delivered.
- Existing architecture boundaries remain intact.
- Relevant tests pass, and full tests pass before merge or push unless the user
  explicitly narrows scope.
- Completed repository work is committed, pushed, and verified against
  `origin/main` unless the user says otherwise. Local planning and decision
  documents follow the Obsidian-only rule above and are not repository work.
- The final answer states what changed, how it was verified, and any remaining
  risk.

Then loop until done:

```text
inspect -> define success criteria -> change -> test -> fix -> retest -> finish
```

The rules in this file are guardrails. They do not replace the outcome. If a
user gives a goal, choose the implementation path yourself. If a user gives
specific steps, follow them while still verifying the final result against the
goal. Ask questions only when missing information blocks completion or a
reasonable assumption would be risky.

## Before Editing

Run:

```bash
git status --short --branch
```

Then read the relevant architecture guide above. Use `rg` to locate symbols
before opening large files.

## Non-Negotiable Rules

1. Do not reintroduce imports of the retired Streamlit `ui/` package. It was
   removed from the repo; code and tests must import from `openbrep.*` and pass
   in a clean environment (CI). Local site-packages leftovers are not a safety net.
2. Do not treat `.gsm` as editable source. HSF project directories are source.
3. Do not bypass `HSFProject` for source state.
4. Do not rewrite `run_agent_generate` behavior without tests.
5. Do not change generation intent routing order without updating tests.
6. Do not silently drop geometry in the Blender importer — unsupported
   operations degrade to explicit warnings or clear errors, never to silent
   empty output.
7. Keep bpy/bmesh/mathutils stubs in one place
   (`openbrep/importers/blender_script/mesh_capture.py`, `mathutils_shim.py`).
   Do not scatter ad-hoc fake modules.
8. Do not add runtime dependencies without declaring them in `pyproject.toml`
   (CI installs only declared deps).
9. Do not silently write user configuration from incidental UI changes (draft
   state + explicit save — see Project Mission).
10. Do not break the current flat workspace layout.

## Architecture Hygiene

Prevent architecture drift by coding to product seams, not buttons or temporary
UI flows. New behavior must first choose one seam:

```text
HSF Source Session
AI Workbench
Preview Verification
Knowledge Memory
Archicad Adapter
Blender Importer (BS2G)
React Workbench Shell
```

Rules:

- `openbrep/workbench_api.py` (`WorkbenchSession`) is the composition root.
  Keep real behavior in testable service modules (`openbrep/workbench/*_service.py`).
- Do not grow a service by mixing unrelated seams. Split when a module starts
  combining chat routing, source mutation, preview, knowledge, and adapter
  behavior.
- Every extracted module needs a small contract test for its public interface.
- Prefer a small, deep interface over many pass-through helpers.

## Where Code Belongs

```text
React workbench UI (pages, panels, store, actions)
  frontend/src/workbench/*
  frontend/src/components/*
  frontend/src/state/*

Local API session (composition root) + backend services
  openbrep/workbench_api.py
  openbrep/workbench/*_service.py

Tapir/Archicad workflow
  openbrep/tapir_bridge.py
  openbrep/tapir_controller.py
  openbrep/workbench/tapir_service.py
  openbrep/workbench_tapir.py

AI generation workflow
  openbrep/runtime/pipeline.py

Blender script → GDL importer (BS2G)
  openbrep/importers/blender_script/*
  primitive mode: parser.py / mapper.py / generator.py
  mesh mode: mesh_capture.py / loft_detect.py / loft_gdl.py / mesh_gdl.py

CLI (obr)
  cli/main.py

Domain logic
  openbrep/*
```

If unsure, keep `workbench_api.py` as a thin adapter and place real behavior in
a testable module.

## Required Tests

Run targeted tests while editing, then full tests before merge:

```bash
python -m pytest tests/ -q
```

Frontend (when touching `frontend/`):

```bash
cd frontend
npx vitest run
npx tsc --noEmit -p tsconfig.app.json
```

Common targeted sets:

```bash
python -m pytest tests/test_blender_script_importer.py tests/test_bs2g_mesh_loft.py tests/test_bs2g_shim.py -q
python -m pytest tests/test_bs2g_gdl_purity.py tests/test_bs2g_compile_gate.py -q
python -m pytest tests/test_workbench_api.py tests/test_workbench_services.py -q
python -m pytest tests/test_gdl_previewer.py tests/test_blender_script_importer.py -q
```

## Current Baseline

As of 2026-08-13:

```text
python tests: 1839 passed, 66 subtests passed
frontend: 458 passed (vitest) + tsc clean
benchmark replay: create/modify zero regression; vision suite 1/3 (recorded baseline)
```

Architecture notes:

- The Streamlit `ui/` package was retired (see `40aa6af` and later cleanup) and
  must not be imported or resurrected. The shell is the React workbench
  (`frontend/`) talking to a local API (`openbrep/workbench_api.py`), packaged
  with a Tauri desktop shell (`src-tauri/`).
- Semantic repair loop (2026-07-28): after compile succeeds, CREATE and
  MODIFY/DEBUG/REPAIR run `verify_semantics`; blocking issues trigger bounded
  accept/rollback repair rounds in `openbrep/runtime/semantic_repair.py`.
  `TaskResult.success` is the verification report's `passed` (delivery gate) —
  do not hardcode it back to `True`.
- Naming alignment (2026-07-28): `openbrep/naming_alignment.py` renames
  parameters to a pluggable naming convention (synonym dictionary + role-aware
  reserved-name rules; A/B/ZZYZX/AC_* are never rename sources; string-literal
  references like `VALUES "name"` are replaced only on whole-string match).
  Renaming is currently consumed by the benchmark runner; production wiring
  (project-level `naming_convention.toml`) is deliberately not done yet.
  `detect_reserved_param_misuse()` from the same module runs in both CREATE
  and MODIFY verification stages: reserved names used in the wrong dimensional
  role (height stuffed into A/B, or ZZYZX as width/depth) become blocking
  `reserved_param_semantic_bug` checks — the artifact is delivered with the
  warning, not auto-repaired.
- Benchmark determinism (2026-07-28): hosted LLMs are nondeterministic even at
  temperature=0, so effect verification uses golden-corpus record/replay —
  `benchmark/runner.py --llm-record corpus.jsonl` once, then
  `--llm-replay corpus.jsonl` for deterministic offline reruns
  (`benchmark/llm_replay.py`). CREATE tasks run through the production
  `TaskPipeline` path; `--jobs N` parallelizes suites (default 4, 1 = serial).
- LLM provider registry (2026-08-01): "which provider owns this model" lives in
  exactly one place — `PROVIDER_PROFILES` in `openbrep/config.py` (prefixes,
  env vars, `provider_keys` names, console URL, litellm native prefix).
  `model_to_provider`, `resolve_api_key`, `LLMAdapter._setup`,
  `_NATIVE_PROVIDERS`, and error-message console URLs all derive from it;
  adding a provider = adding one row. Reads go through
  `LLMConfig.resolve_credentials(model)` (custom_providers → provider_keys →
  top-level → env, with source labeling). Top-level `api_key`/`api_base` are
  the global fallback — never write per-model resolution results back into
  them when switching models. Anthropic's litellm native prefix stays
  `claude/` (not `anthropic/`).
- Unified provider registry (2026-08-04): endpoint-style providers are one
  mechanism now — `[[llm.providers]]` in `config.toml` with canonical keys
  `name / api / api_mode / api_key / default_model / models`
  (`api_mode`: `chat_completions` default, or `anthropic_messages`). Legacy
  `[[llm.custom_providers]]` (`base_url`/`protocol`) is normalized at load by
  `normalize_provider_entry()` (entries carry both old and new keys;
  `_explicit_base` preserves the "absent api → top-level fallback" rule), and
  `GDLAgentConfig.save()` writes only the new keys (save = migration).
  `find_custom_provider_match()` returns the raw entry in `provider` so UI/CLI
  credential writes propagate, plus a `_normalized` view for readers. New
  capabilities: `provider/model` explicit refs (unlisted model ids allowed),
  `default_model`, `${ENV_VAR}` interpolation in any api_key field, and
  `[llm] default` as an alias of `model`. Official built-in models keep the
  2026-08-01 `PROVIDER_PROFILES` + `provider_keys` path (preset data, not
  resolution logic); `provider_templates()` exposes their default endpoints
  (`PROVIDER_API_TEMPLATES`) for future "add from template" UI. The settings
  UI key editor now works for custom providers too (ollama excluded).
- Deterministic micro-modify (2026-08-01, plan P2): pure parameter-value
  changes ("把层板数改成 5" / "set shelf_count to 5") are intercepted before
  the LLM MODIFY path — `openbrep/runtime/micro_modify.py` does high-precision
  detection only (name/description resolution, Length unit conversion, boolean
  words); anything ambiguous, compound, or question-shaped returns None and
  falls through to `_handle_script_update` unchanged. The pipeline applies the
  value, snapshots a before-revision (metadata records the change), persists
  paramlist, and still runs compile; semantic verification is advisory only —
  explicit user intent, same semantics as editing the parameter panel.
- Vision Harness (2026-08-10→12, P5a–P5e): image input goes through
  `openbrep/vision/harness.py` (S1 local schema classification → S2
  schema-driven extraction → S3 critic → S4 hint composition) instead of raw
  image passthrough. Schemas live in `openbrep/vision/schemas/*.yaml`
  (registry: `schema_registry.py`). CREATE with images stops after extraction
  for user confirmation/editing (`TaskRequest.confirm_extraction` /
  `confirmed_extractions`, session `pending_extraction` with project_epoch
  guard); confirmed dicts rebuild plans via `ModelingPlan.from_dict` with
  byte-identical hints when unedited. Extractions persist as content-addressed
  artifacts `.openbrep/vision/extraction-<sha12>.json`
  (`vision/extraction_store.py`); MODIFY (agent loop) runs the lite harness
  (no critic) and reuses cached extractions by sha256, injecting hints into
  the system message (never user messages, never tools). Non-interactive
  paths (benchmark/CLI) are byte-identical — replay zero-miss is the gate.
  MODIFY with images skips micro_modify (guard at pipeline `_try_micro_modify`).
- Naming pipeline (2026-08-12, P7a–P7c): `openbrep/naming.py` is the single
  place for project naming (sanitize keeps CJK, conflicts get `_vN`). AI
  create names by three levels: planner `object_type` > rule-based prompt
  extraction > 未命名构件 fallback; post-generation the project directory is
  renamed (gsm artifacts follow, explicit `project_name` wins). Untitled
  project save prompts for a name (`needs_save_as` → auto-located Save As).
  Project landing spot is workspace-first everywhere (2026-08-13): AI create,
  Save As, and import all resolve explicit request dir > attached workspace
  `hsf/` > settings `output_dir` > `./output`
  (`project_session_service._save_as_auto_dir`).
- Assistant history as asset (2026-08-12, P6a/P6b): chat history can be
  imported across projects and distilled into a draft instruction (never
  auto-sent).
- Chat-intent guards (2026-08-12, P0): `[图N]` tokens are stripped before
  intent detection; agent loop snapshots a before-revision lazily before the
  first actual mutation; generate intent with an open project asks first;
  chat without a project never creates files on disk.
- Verification defense pack (2026-08-13, P8): `openbrep/runtime/semantic_repair.py`
  rejects degenerate repair rounds (ellipsis stubs, content shrink, param loss)
  before the accept check; `static_checker.py` has an `ellipsis_stub` check;
  CREATE/IMAGE runs delivery-integrity checks (placeholder_delivery /
  reserved_params_missing) and retries once on zero `[FILE:]` output before
  hard-failing with `project=None`.
- Previewer condition eval (2026-08-13, P9): `gdl_previewer.py` supports string
  comparison (`=`, `<>/#`), leading `NOT`, single-line IF no longer counted in
  block-IF depth, inline-IF GOSUB runs in full script context, `SQR` allowed.
  String parameters survive the production path
  (`project_parameter_service.parameter_values`); numeric sweeps skip them.
  FOR-body cross-scope GOSUB and `:` multi-statements in inline IFs were added
  on 2026-08-13 (P10, same None-sentinel full-script-context pattern); remaining
  known limits: `IF x THEN RETURN` early-return semantics and END-only-ends-block.
- Parameter UI mode (2026-08-13, P11): `parse_values_declarations` parses vl.gdl
  VALUES (enum/RANGE) into the parameter payload (`options`/`range`); ParameterRail
  renders a `<select>` for enum params (out-of-list current value gets a non-writing
  fallback entry) and has a 参数/参数脚本 tab pair (script tab embeds the lazy
  ScriptEditor on vl.gdl, saves re-pull the snapshot to refresh enums).
- Agent-loop write guards (2026-08-13, P12): `static_checker.find_prose_leaks`
  (markdown prose in .gdl) blocks `update_script`/`patch_script` writes; paramlist
  String-value changes are blocked when old value is still referenced by scripts
  and the new value is not (直棂→zhileng incident); empty compile errors get a
  fallback message. Guards only return error text in tool results — replay-safe.
- String paramlist CDATA (2026-08-13): String parameter values are serialized as
  quoted CDATA (`<Value><![CDATA["直棂"]]></Value>`) — unquoted CDATA triggers
  Archicad "String value error", bare text triggers "Missing CDATA section".
  Compile diagnostics also parse hsf2libpart stdout, not just stderr.
- GSM registration magic (2026-08-13): `Owner`/`Signature` default to the
  Graphisoft constants (MYSGCASG header magic); without them Archicad refuses to
  open the .gsm. Normalization happens at compile time in `compiler.py`
  (`_normalize_registration`, before hsf2libpart, BOM preserved) — do NOT move it
  back to load-time self-healing: that trips the param_modify guard rollback
  (guard only allows paramlist.xml + scripts/* changes) and breaks replay.
- Previewer colon semantics + new static checks (2026-08-13, P13): single-line
  IF allows only ONE conditional statement per Archicad (GDL Reference Guide
  p323) — `IF c THEN s1 : s2` executes s1 conditionally and s2 unconditionally;
  the previewer matches. `static_checker` gains `unknown_command` (warning
  level, statement first word not in `openbrep/data/gdl_commands.txt` — a static
  598-entry union list, never read from knowledge/ at runtime; surfaces invented
  commands like UNLOCK, incl. ui.gdl/vl.gdl) and `bare_not` (error level — NOT
  is a function requiring `NOT (x)`; operator-position heuristic avoids flagging
  English prose "not"). Warnings flow into `VerificationReport.warnings_caught`
  and the `run_static_check` tool summary without blocking delivery.
- Copilot integration (2026-08-13, T1–T4): the Archicad add-on's copilot backend
  lives in the workbench now — `openbrep/workbench/copilot_service.py`
  (`WorkbenchCopilotService`: status/chat/clipboard buffer/error summary,
  clipboard watcher lazy-starts on first call, macOS AppKit→pbpaste fallback),
  routed as `/api/copilot/*` in `workbench_api.py` with the three POST routes
  lock-free (they never touch session/project state). Frontend
  `?mode=copilot` renders a standalone `CopilotPage` (no workbench store,
  direct fetch). `obr serve` (cli/main.py) wraps `scripts/obr7.py
  --tauri --daemon` as the C++ add-on's only launch entry; it hard-fails when
  `frontend/dist` is missing. Copilot chat deliberately bypasses
  `/api/assistant` intent routing — no project required, no file mutation.

## benchmark 黄金语料规范（corpus maintenance）

CI 的 `benchmark-replay` job 用入库语料离线回放 create + modify 套件，
并与 `benchmark/baseline.json` 比较：PASS→FAIL、criteria_failures 增加、
pass 数下降都会红灯。

语料文件：`benchmark/fixtures/llm_corpus/{create,modify}.jsonl`
基线文件：`benchmark/baseline.json`（只能往好的方向更新）

**判断要不要重录语料的唯一依据：改动会不会影响送给 LLM 的 prompt。**

必须重录（prompt 会变化）：
- `knowledge/`、`user_knowledge/`、`skills/` 目录内容变更
  （例外：`GDL_common_errors.md` 当前被 selector 的 `\n---\n` 分片规则
  整体切碎丢失——改动它暂不改变 prompt，见总报告第 10 节 P14 候选；
  `GDL_control_flow.md` / `GDL_parameters.md` / `GDL_quick_reference.md`
  无内部分隔符，改动会进 prompt）
- `benchmark/tasks/` 任务文件的 `description` 变更（description 是发给 LLM 的
  指令本体）
- prompt 构建逻辑变更（pipeline 的指令拼装、object_planner、图谱注入、
  knowledge_selector.py 的选择逻辑、skills_loader）
- LLM model 或 provider 变更
- TaskPipeline 的学习记忆注入策略变更（`include_learned_skills`）

不需要重录（prompt 不变，直接回放验证）：
- 生成后的处理逻辑：linter、static checker、naming_alignment、
  semantic_verifier、semantic_repair 的接受/回退判定
- verification / 报告逻辑（build_verification_report 及各类 check）
- 编译链路（compiler、hsf2libpart 调用方式）
- benchmark 断言逻辑（assertions.py、check_baseline.py）

重录命令（真实 LLM，消耗 token）：

```bash
python -m benchmark.runner --suite benchmark/tasks/create/ --mode auto --jobs 4 --llm-record benchmark/fixtures/llm_corpus/create.jsonl
python -m benchmark.runner --suite benchmark/tasks/modify/ --mode mock --jobs 4 --agent-loop --llm-record benchmark/fixtures/llm_corpus/modify.jsonl
python -m benchmark.update_baseline --init --confirm   # 重建/刷新基线
```

- modify 语料自 S4（2026-08-08）起在 **agent loop 路径**录制/回放
  （`--agent-loop`，patch_script 等工具链首次进入 benchmark 度量）；
  create 套件不受影响（`--agent-loop` 只作用于 MODIFY）。
- modify 录制必须用 `--mode mock`（而非 auto）：agent loop 的 prompt 里嵌了
  编译反馈（compile_script 工具结果 / 完成门禁），而 check_baseline 回放统一用
  mock 编译器——若录制用真机编译器，回放时 mock 的编译结果与录制不一致，
  后续轮次 prompt 全 miss（实测 M02 的 IF/ENDIF 边界即崩）。

- OpenRouter 等不需要 thinking 参数的端点，录制命令加 `GDL_BENCH_THINKING=bare` 前缀（完全不设 extra_body）。
- 默认不设置该变量时行为不变：录制仍强制关闭 thinking。

基线更新（改进落地后）：`python -m benchmark.update_baseline --confirm`。
脚本会拒绝任何退化方向的更新；写盘必须在 commit message 里说明原因。

注意：回放未命中（prompt 流与语料不一致）会报错并提示重录——
这是特性不是故障：它拦住"悄悄改变 prompt 却不重录"的情况。
- The workbench API serializes mutating requests through a session-level lock
  (`openbrep/workbench/request_gate.py`); new routes default to locked.
- Tests must stay isolated from the developer's real `./config.toml` via
  `tests/conftest.py` (`GDL_AGENT_CONFIG` points at a tmp copy).

Important architecture boundaries already exist:

```text
openbrep/workbench_api.py
openbrep/workbench/*_service.py
openbrep/workbench/request_gate.py
openbrep/runtime/pipeline.py
openbrep/runtime/semantic_repair.py
openbrep/naming_alignment.py
benchmark/llm_replay.py
openbrep/importers/blender_script/*
openbrep/vision/harness.py
openbrep/vision/schema_registry.py (+ schemas/*.yaml)
frontend/src/workbench/*
frontend/src/state/*
```

## Default Finish Sequence

Unless the user explicitly asks not to commit or push, finish completed
repository code or repository documentation with this sequence. Local planning,
research, design exploration, dispatch, and handoff documents are excluded and
must follow “规划与决策文档的落盘规则” above:

```bash
python -m pytest tests/ -q
git add ...
git commit -m "type: concise summary"
git push
```

If work was done on a branch, merge it back to `main` after tests pass:

```bash
git switch main
git merge --no-ff branch-name -m "merge branch-name"
python -m pytest tests/ -q
git push
git status --short --branch
git rev-parse main
git rev-parse origin/main
```

The final state should normally be:

```text
main and origin/main point to the same commit
working tree is clean
```

## Release / Installer SOP

> ⚠️ 本节写于 Streamlit/PyInstaller 时代，安装包流水线正在向 Tauri
> （`.github/workflows/release-tauri.yml`）迁移；涉及 Streamlit 打包的段落
> （如 "Known PyInstaller/Streamlit packaging requirements"）已过期，发布前
> 请与维护者确认当前流程。

Only run this section when the user explicitly asks for a release, installer
build, version bump, or public package update. Do not tag or publish a release
for ordinary code/documentation changes.

Release tags are immutable. If a pushed tag or GitHub Release workflow is wrong,
do not rewrite or force-push that tag. Fix the issue in a new commit and publish
a follow-up patch tag.

### When To Use This SOP

Use it for requests such as:

- "做一个小版本更新"
- "发布 vX.Y.Z"
- "打包安装包"
- "让 GitHub Release 可下载"
- "更新安装方式并发布"

Do not use it for normal feature/fix/docs work unless the user explicitly asks
to publish the result.

### Preflight

```bash
git switch main
git status --short --branch
git rev-parse main
git rev-parse origin/main
python -m pytest tests/ -q
```

`main` and `origin/main` must point to the same commit before tagging. The
working tree should be clean except for explicitly ignored local artifacts.

### Version And Docs

For a release version `vX.Y.Z`, update all version sources and release-facing
docs together:

```text
pyproject.toml
openbrep/__init__.py
README.md
README.zh-CN.md
INSTALL_CN.md
docs/releases/vX.Y.Z.md
tests/test_pipeline_modify.py release/version assertions
```

If the release changes install or packaging behavior, also update:

```text
.github/workflows/build-installers.yml
docs/RELEASE_PROCESS.md
docs/install_distribution_strategy_*.md when relevant
```

### Verification

Run:

```bash
python -m pytest tests/ -q
python -m pip wheel . -w /tmp/openbrep_wheel_check --no-deps
```

For installer or packaged-launcher changes, package-level verification must
exercise the downloaded/generated zip itself, not the local `obr` command:

```bash
python scripts/package_smoke.py release/OpenBrep-free-macOS.zip --timeout 90
python scripts/package_browser_smoke.py release/OpenBrep-free-macOS.zip --timeout 90
```

Do not treat `/_stcore/health` alone as installer success. Streamlit can return
health while `/` is `404`, and `/` can load before the app script exposes a
missing frozen dependency. The browser smoke must pass with `ok=true`,
`page_ok=true`, and no `error_markers`.

Known PyInstaller/Streamlit packaging requirements:

```text
streamlit/static must be bundled at streamlit/static
streamlit_ace/frontend/build must be bundled at streamlit_ace/frontend/build
streamlit.runtime.scriptrunner hidden imports must be included
```

Always test from a fresh extraction directory. Do not reuse an old
`/tmp/openbrep-install-test` directory when validating a fixed installer.

For installer workflow changes, also validate workflow syntax locally if
possible:

```bash
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/build-installers.yml"); puts "yaml ok"'
```

### Commit And Tag

```bash
git add ...
git commit -m "type: concise release summary"
git push
git status --short --branch
git rev-parse main
git rev-parse origin/main
git tag -a vX.Y.Z -m "OpenBrep vX.Y.Z"
git rev-parse main
git rev-parse 'vX.Y.Z^{}'
git push origin vX.Y.Z
```

The two `rev-parse` commands for `main` and `vX.Y.Z^{}` must print the same
commit hash.

### GitHub Release Verification

After pushing a `v*` tag, check the installer workflow:

```bash
gh run list --workflow "Build installers" --limit 5
gh run watch <run-id> --exit-status
gh release view vX.Y.Z --json tagName,name,url,assets,isDraft,isPrerelease,targetCommitish
```

The GitHub Release should contain the expected installer assets:

```text
OpenBrep-free-macOS.zip
OpenBrep-free-Windows.zip
```

The Release notes must state platform compatibility explicitly:

```text
macOS CPU architecture: arm64 / x86_64 / universal
Minimum macOS version: derived from the packaged binary dependencies
Windows architecture and minimum tested version
```

For macOS, do not infer compatibility from the filename alone. Check the
published zip itself. The effective minimum macOS version is the highest
`minos` value among the frozen executable and bundled `.dylib` / `.so` files.

If the workflow fails after the tag is pushed, fix the workflow in a new commit
and publish the next patch tag. Do not delete, move, or overwrite the failed
release tag unless the human maintainer explicitly instructs that destructive
operation.

### Known Risks

- Installer builds are slower than normal tests and consume GitHub Actions
  minutes.
- Release tags are public coordination points; a mistaken tag can confuse users.
- GitHub Release assets may expose unfinished or untested packages if the tag is
  pushed too early.
- Packaging workflows may fail for platform-specific reasons not covered by unit
  tests.
- GitHub Actions and GitHub CLI behavior can change; always verify the release
  page and assets after the workflow completes.

## Manual Risk

Unit tests do not fully cover React workbench behavior or real Archicad/Tapir
integration. For changes touching UI, compile, generation, vision, or Tapir,
perform or clearly request manual smoke testing:

```text
obr                                  # launch the workbench UI
create simple object
modify existing object
explain without mutation
import .gdl
import .gsm if LP_XMLConverter is available
import Blender .py (primitive script + bmesh loft script)
load HSF directory
preview 2D/3D (all display modes)
compile versioned .gsm
test Tapir/Archicad when available
```
