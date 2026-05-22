# OpenBrep Evals

This directory contains deterministic quality scorecards for OpenBrep.

Current scope:

- `fixtures/gdl_objects/`: valid standalone 3D GDL fixture scripts.
- `fixtures/broken_gdl/`: generated broken variants for repair/checker coverage.
- `prepare_fixtures.py`: regenerates broken fixture variants from valid scripts.
- `scorecards/run_scorecard.py`: runs static checks plus mock, real, or auto HSF compilation, then writes optional JSON results.

Run:

```bash
python evals/prepare_fixtures.py
python evals/scorecards/run_scorecard.py --mode mock --output evals/scorecards/results/
python evals/scorecards/run_scorecard.py --mode auto --output evals/scorecards/results/
python evals/scorecards/run_scorecard.py --mode real --output evals/scorecards/results/
```

The scorecard is intentionally LLM-free for now. It measures fixture compileability and broken-script detection, not live generation quality. In `real` mode, missing `LP_XMLConverter` is reported as `skipped` with an explicit reason instead of as a fixture failure. In `auto` mode, OpenBrep uses real compilation when the converter is available and falls back to mock compilation otherwise.
