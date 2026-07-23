# OpenBrep Benchmark Harness

The benchmark harness runs deterministic task-level checks for generated HSF
projects. It is separate from the LLM-free scorecards under `evals/`.

Run:

```bash
python benchmark/runner.py --suite benchmark/tasks/create/ --mode mock
python benchmark/runner.py --suite benchmark/tasks/create/ --mode auto
python benchmark/runner.py --suite benchmark/tasks/create/ --mode real
```

Each task YAML can define:

- `required_params`: parameters that must exist in `paramlist.xml`.
- `required_scripts`: script files that must exist and be non-empty.
- `geometry_check`: legacy natural-language hints consumed by lightweight
  command checks.
- `semantic_assertions`: machine-readable assertions. Two families:
  - textual (regex over generated GDL source): `command_present`, `param_used`,
    `expression_present`, `transform_balanced`.
  - geometric (via `openbrep.semantic_verifier`, runs the lightweight
    `gdl_previewer` — catches "compiles but the geometry is wrong", not just
    "text mentions the right keyword"):
    - `semantic_verification`: mesh non-empty/non-degenerate, and bounding box
      matches the object's declared A/B/ZZYZX within tolerance.
    - `param_responsive` (requires `param`): fails if that declared parameter
      doesn't move the rendered geometry at all when perturbed (a "dead"
      parameter — declared but not actually wired in).
  C11–C20 (Phase 1 expansion) carry both geometric assertion types; C01–C10
  are textual-only (pre-Phase-1 baseline, kept as-is so historical pass-rate
  comparisons stay apples-to-apples).

Runner output includes:

- `compile_pass`
- `static_pass`
- `contract_pass`
- `criteria_pass`
- `criteria_failures`
- `contract_failures`

Result files:

- `benchmark/results/<date>_<suite>.json`: latest full task records for the day.
- `benchmark/results/<suite>.jsonl`: append-only run history.
- `benchmark/results/<suite>_summary.md`: human-readable summary table.

`real` mode uses `LP_XMLConverter` when available. If the converter is missing,
the runner marks tasks as skipped instead of treating environment setup as a
generation quality failure.

## MODIFY suite（Phase 3 新增）

`benchmark/tasks/modify/`（M01–M05）是 MODIFY 场景基准：每个任务带一个
`fixture: benchmark/fixtures/modify/<task_id>` 字段，指向签入仓库的"改动前"
HSF 工程（由 `benchmark/fixtures/modify/build_fixtures.py` 生成，改 fixture 请
改生成器再重跑）。runner 遇到带 fixture 的任务会：

1. 把 fixture 复制到 `benchmark/workdir/<task_id>/`（保护签入原件不被
   编译时的 `save_to_disk` 污染），`HSFProject.load_from_disk` 加载；
2. 走 `TaskPipeline` 的生产 MODIFY 路径（`_handle_script_update`），而不是
   CREATE 路径的 `GDLAgent.run`。

```bash
python benchmark/runner.py --suite benchmark/tasks/modify/ --mode mock
```

## 新旧路径 A/B 对照（Phase 3 实验）

`benchmark/ab_compare.py` 对 MODIFY 任务分别跑旧路径与实验性预算制 agent
loop 新路径（`TaskRequest.agent_loop=True`，默认关闭），对比编译通过率、
整体成功率、几何断言通过率、平均 LLM 调用次数：

```bash
# 离线自测（MockLLM，零 API 消耗，仅验证流程）
python benchmark/ab_compare.py --suite benchmark/tasks/modify/ --mode mock --llm mock
# 真实 A/B（消耗 API 额度，预算由用户把控）
python benchmark/ab_compare.py --suite benchmark/tasks/modify/ --mode mock --llm real
```

报告产出：`benchmark/results/<date>_modify_ab.json` +
`benchmark/results/modify_ab_summary.md`。新路径胜率不高于旧路径则不推广，
仅作为可开关的实验路径存在。

