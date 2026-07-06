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
