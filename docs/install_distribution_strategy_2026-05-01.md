# OpenBrep Installation Distribution Strategy

Date: 2026-05-01

## Summary

OpenBrep should stop presenting `git clone` as the default install path for
ordinary users. The project now has three different audiences, and each needs a
different installation entry point:

| Audience | Best entry point | Why |
|---|---|---|
| Architects / designers | GitHub Release desktop zip | Lowest conceptual load: download, unzip, run |
| Command-line users | PyPI via `pipx` or `uv tool` | Isolated install and clean upgrades |
| Contributors | `git clone` + `bash install.sh` | Editable source tree and tests |

## Is npm better?

Not as the primary path.

OpenBrep is a Python + Streamlit application with Python package metadata,
console scripts, PyInstaller packaging, and Archicad/LP_XMLConverter integration.
An npm package would mostly be a wrapper that installs or shells out to Python.
That can help JavaScript developers run `npx openbrep`, but it adds Node.js as a
new prerequisite for the core user group. For architects and GDL users, GitHub
Release assets are friendlier.

Possible future npm role:

```text
npx openbrep
  → check Python / uv
  → install openbrep[ui] into an isolated tool env
  → run obr
```

This is a bootstrapper, not the canonical distribution format.

## Best GitHub-first install path

For GitHub-first distribution, the best public path is:

```text
Git tag vX.Y.Z
  → GitHub Actions build macOS / Windows packages
  → GitHub Release is created automatically
  → Release assets contain OpenBrep-free-macOS.zip / OpenBrep-free-Windows.zip
  → README points ordinary users to /releases/latest
```

This release implements the missing automation step: the installer workflow now
uploads the actual `OpenBrep-*-macOS.zip` and `OpenBrep-*-Windows.zip` assets and
creates or updates the matching GitHub Release for `v*` tags.

## Recommended roadmap

### v0.6.4

- Fix release asset automation.
- Make GitHub Release packages the README and install guide default.
- Keep git clone as developer-only.
- Document `pipx` / `uv tool` as the command-line path after PyPI publishing.

### v0.6.5

- Patch the GitHub Release publishing command after the v0.6.4 tag exposed a
  GitHub CLI compatibility issue: `--notes-from-tag` cannot be used together
  with `--repo`.
- Keep release tags immutable; publish v0.6.5 instead of rewriting v0.6.4.

### v0.6.6

- Add PyPI publishing via Trusted Publishing.
- Verify:
  - `pipx install "openbrep[ui]"`
  - `uv tool install "openbrep[ui]"`
  - `uvx --from "openbrep[ui]" obr`
- Add a GitHub Actions job that builds wheel/sdist and tests installing the wheel
  in a clean environment.

### Later

- Add Homebrew cask only after macOS app packaging and signing are stable.
- Consider npm only as a bootstrapper if there is real demand from JS-oriented
  users or plugin ecosystems.

## References

- GitHub CLI release commands: https://cli.github.com/manual/gh_release_create
- GitHub release assets: https://docs.github.com/repositories/releasing-projects-on-github
- pipx documentation: https://pipx.pypa.io/
- uv tools documentation: https://docs.astral.sh/uv/concepts/tools/
- Python entry points: https://packaging.python.org/en/latest/specifications/entry-points/
- npm package `bin` field: https://docs.npmjs.com/cli/v10/configuring-npm/package-json#bin
