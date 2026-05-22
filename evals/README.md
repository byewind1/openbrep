# OpenBrep Evals

This directory contains deterministic quality scorecards for OpenBrep.

Current scope:

- `fixtures/gdl_objects/`: valid standalone 3D GDL fixture scripts.
- `fixtures/broken_gdl/`: generated broken variants for repair/checker coverage.
- `prepare_fixtures.py`: regenerates broken fixture variants from valid scripts.
- `scorecards/run_scorecard.py`: runs static checks and mock HSF compilation, then writes optional JSON results.

Run:

```bash
python evals/prepare_fixtures.py
python evals/scorecards/run_scorecard.py --mode mock --output evals/scorecards/results/
```

The scorecard is intentionally LLM-free for now. It measures fixture compileability and broken-script detection, not live generation quality.
