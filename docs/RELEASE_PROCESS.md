# OpenBrep Release Process

This checklist keeps `main`, release tags, and GitHub builds traceable.

## Required Invariants

- `main` is the source branch for public releases.
- `origin/main` and local `main` must point to the same commit before tagging.
- Version tags must peel to the released `main` commit.
- Release tags are immutable. If a release is wrong, publish a new patch tag.
- Do not force-push `main` or published `v*` tags.

## Preflight

```bash
git switch main
git status --short --branch
git fetch origin --tags
git rev-parse main
git rev-parse origin/main
python -m pytest tests/ -q
```

The worktree must be clean and `main` must equal `origin/main` before release
tagging. If they differ, merge or push the normal branch history first.

## Release Branch

Use a short release branch for final notes and version bumps:

```bash
git switch -c release/vX.Y.Z
python -m pytest tests/ -q
git push -u origin release/vX.Y.Z
```

Open a PR into `main` and wait for the `Tests` workflow to pass. Merge without
rewriting `main`.

## Tagging

After the release PR is merged:

```bash
git switch main
git pull --ff-only origin main
python -m pytest tests/ -q
git tag -a vX.Y.Z -m "OpenBrep vX.Y.Z"
git rev-parse main
git rev-parse 'vX.Y.Z^{}'
git push origin vX.Y.Z
```

The two `rev-parse` commands must print the same commit hash. The tag push will
trigger the installer build workflow. For `v*` tags, that workflow should:

1. Build macOS and Windows installer zip files.
2. Upload them as workflow artifacts.
3. Create or update the matching GitHub Release.
4. Attach `OpenBrep-*-macOS.zip` and `OpenBrep-*-Windows.zip` as release assets.

## Package Smoke Guardrails

Installer verification must cover both startup and browser rendering.

- Never use only `/_stcore/health` as success. Streamlit can report health
  while `/` still returns `404` if frozen frontend assets are missing.
- Never treat a page load alone as success. The browser must finish loading the
  app without `ModuleNotFoundError`, `ImportError`, or `Traceback` in the log.
- Test the zip itself, not the local `obr` command.
- Use a fresh extraction directory for each test run.
- For macOS release zips, verify both:

```bash
python scripts/package_smoke.py release/OpenBrep-free-macOS.zip --timeout 90
python scripts/package_browser_smoke.py release/OpenBrep-free-macOS.zip --timeout 90
```

Packaging regressions fixed in v0.6.11:

- Bundle `streamlit/static` at the exact runtime path expected by Streamlit.
- Bundle `streamlit_ace/frontend/build` at the exact runtime path expected by
  `streamlit_ace`.
- Include `streamlit.runtime.scriptrunner` hidden imports so frozen script
  execution can import `magic_funcs`.

## Postflight

```bash
git status --short --branch
git rev-parse main
git rev-parse origin/main
git rev-parse 'vX.Y.Z^{}'
gh release view vX.Y.Z
```

Record the commit hash in the release notes.
