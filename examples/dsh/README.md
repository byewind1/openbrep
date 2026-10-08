# OpenBrep MCP with DeepSeek Harness

This example connects DSH to OpenBrep's local stdio MCP server. DSH remains the
agent/model loop; OpenBrep supplies deterministic HSF project operations and
verification tools. No OpenBrep model credentials are needed for this route.

## Prerequisites

- OpenBrep installed in the environment used to launch DSH, with `obr` on that
  process's `PATH`.
- The OpenBrep package dependencies installed, including the MCP SDK.
- DSH's `@deepseek-ai/dsh-mcp-client` available in the active profile.
- An HSF project directory to work on.

## Connect

Copy `cordis.patch.yml` to `~/.dsh/cordis.patch.yml`, or copy its single
`insert` row to the project's `.dsh/cordis.patch.yml` for project-scoped use.
Start a new DSH session and wait for MCP discovery. The tools use names such as
`mcp__openbrep__capabilities` and `mcp__openbrep__load_project`.

If DSH cannot find `obr`, replace `command` with the absolute path to the
installed executable. Keep `transport: stdio`; the MCP server is launched as a
child process and does not require a separate HTTP service.

## Suggested first task

Use the portable `openbrep-gdl-development` Skill in
`../agent-skills/openbrep-gdl-development/SKILL.md`. Start with
`mcp__openbrep__capabilities`, then load the explicit HSF path. Use draft edits
and inspect their result before applying. `mode="mock"` is not a real
Archicad compile; report it as structural simulation. Run semantic and visual
evidence where applicable, and report stale or missing required checks as
unverified.

This is a configuration example, not a claim that DSH has been installed or
that a live DSH-to-OpenBrep session was validated in this environment.
