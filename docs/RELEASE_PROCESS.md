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
2. Smoke-test the generated zips before upload.
3. Upload them as workflow artifacts.
4. Create or update the matching GitHub Release.
5. Attach `OpenBrep-*-macOS.zip` and `OpenBrep-*-Windows.zip` as release assets.
6. State supported OS versions and CPU architectures in the GitHub Release
   notes. Do not publish a generic `macOS` claim when the asset is only
   `arm64` or only `x86_64`.

## Package Smoke Guardrails

Installer verification must cover both startup and browser rendering.

- Never use only `/_stcore/health` as success. Streamlit can report health
  while `/` still returns `404` if frozen frontend assets are missing.
- Never treat a page load alone as success. The browser must finish loading the
  app without `ModuleNotFoundError`, `ImportError`, or `Traceback` in the log.
- Test the zip itself, not the local `obr` command.
- Use a fresh extraction directory for each test run.
- Check the published package architecture and minimum macOS version before
  announcing compatibility. The effective minimum is the highest `minos` value
  among the frozen executable and bundled `.dylib` / `.so` files.
- For macOS release zips, verify both:

```bash
python scripts/package_smoke.py release/OpenBrep-free-macOS.zip --timeout 90
python scripts/package_browser_smoke.py release/OpenBrep-free-macOS.zip --timeout 90
```

- The GitHub `Build installers` workflow runs package smoke automatically
  before uploading artifacts: macOS runs both package smoke and browser smoke;
  Windows runs package smoke. If any smoke step fails, the release publish job
  is blocked.

Packaging regressions fixed in v0.6.11:

- Bundle `streamlit/static` at the exact runtime path expected by Streamlit.
- Bundle `streamlit_ace/frontend/build` at the exact runtime path expected by
  `streamlit_ace`.
- Include `streamlit.runtime.scriptrunner` hidden imports so frozen script
  execution can import `magic_funcs`.

## macOS Signing And Notarization

The macOS installer workflow can produce signed and notarized zips when the
repository has Apple Developer secrets configured. Apple Developer ID signing
and notarization require a paid Apple Developer Program account.

Required GitHub Secrets:

```text
MACOS_CERTIFICATE_P12_BASE64
MACOS_CERTIFICATE_PASSWORD
MACOS_KEYCHAIN_PASSWORD
MACOS_DEVELOPER_ID_APPLICATION
APPLE_ID
APPLE_TEAM_ID
APPLE_APP_SPECIFIC_PASSWORD
```

Local equivalent:

```bash
export MACOS_DEVELOPER_ID_APPLICATION="Developer ID Application: Name (TEAMID)"
export APPLE_ID="apple-id@example.com"
export APPLE_TEAM_ID="TEAMID"
export APPLE_APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"
bash scripts/build_macos.sh
```

`scripts/build_macos.sh` signs `dist/OpenBrep` before zipping when
`MACOS_DEVELOPER_ID_APPLICATION` is set, then notarizes the final zip when the
Apple notarization credentials are present. The GitHub workflow verifies signed
macOS builds with:

```bash
codesign --verify --deep --strict --verbose=2 OpenBrep/OpenBrep
spctl -a -vvv -t execute OpenBrep/OpenBrep
```

If these secrets are absent, the workflow intentionally falls back to the
unsigned package and the public Gatekeeper workaround remains required.

## Postflight

```bash
git status --short --branch
git rev-parse main
git rev-parse origin/main
git rev-parse 'vX.Y.Z^{}'
gh release view vX.Y.Z
```

Record the commit hash in the release notes.
