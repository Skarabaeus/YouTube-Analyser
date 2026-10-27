# Proposal

## Why

The AI security review currently runs as a pre-commit hook. It takes a minute or more per commit, and a CI-based review (dropped proposal `add-ci-security-review`) would need a separately billed Anthropic API key. With a single developer, CI adds little over a local gate. Reviewing once before code leaves the machine gives the same protection for published code at a fraction of the waiting time.

## What Changes

- The AI security review moves from the **pre-commit** to the **pre-push** stage. It reviews **every local commit that has not been pushed to the remote yet**, commit by commit, so that something added in one commit and removed in a later one is still caught.
- Fast deterministic checks stay at pre-commit: gitleaks on the staged changes, ruff check and ruff format.
- **Accepting a finding:** the commit that contains it carries a `Security-Review-Accepted: <finding> — <reason>` trailer. The pre-push review is told about these acceptances and does not fail on them.
- The `Security-Review: passed (...)` commit trailer is **dropped**. At commit time the review has not run yet, so the trailer could not be truthful.
- `INSTRUCTIONS.md` §2 is rewritten for the new procedure. The CI rows and the "CI is the enforcing gate" rule are removed.
- References in `CLAUDE.md`, `README.md`, `SPEC.md` and `openspec/config.yaml` are updated.
- The proposal `add-ci-security-review` is withdrawn (deleted, never implemented).

## Capabilities

### New Capabilities
None.

### Modified Capabilities
None. This is development tooling and process only; no product behaviour changes (`skip_specs: true`).

## Impact

- **Changed files:** `scripts/security-review-hook.sh`, `.pre-commit-config.yaml`, `INSTRUCTIONS.md`, `CLAUDE.md`, `README.md`, `SPEC.md`, `openspec/config.yaml`.
- **Removed:** `openspec/changes/add-ci-security-review/`.
- **Developer setup:** `uv run pre-commit install` now installs both the pre-commit and the pre-push hook.
- **No Python code, dependency or runtime changes.**
