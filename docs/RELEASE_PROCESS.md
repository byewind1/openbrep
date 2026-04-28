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
trigger the installer build workflow.

## Postflight

```bash
git status --short --branch
git rev-parse main
git rev-parse origin/main
git rev-parse 'vX.Y.Z^{}'
```

Record the commit hash in the release notes.
