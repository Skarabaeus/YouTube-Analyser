# Proposal

## Why

`INSTRUCTIONS.md` §2 requires a security review of every commit. Today it is enforced only by a local pre-commit hook, which can be bypassed with `--no-verify` and is never run for changes made on another machine or through the GitHub web UI. The repository is now public on GitHub (`Skarabaeus/YouTube-Analyser`), `main` is unprotected, and `INSTRUCTIONS.md` already names CI as the enforcing gate "once the remote exists".

## What Changes

- **GitHub Actions workflow** that runs on every pull request to `main` and on every push to `main`. It runs the automated checks from `INSTRUCTIONS.md` §2:
  - `gitleaks` secret scan over the commits of the PR (on pushes: the pushed commits);
  - `ruff check` (including the `S` security rules) and `ruff format --check`;
  - `pytest`;
  - `pip-audit` against the locked dependencies.
- **AI security review** on every pull request with `anthropics/claude-code-security-review`:
  - It runs on every push to the PR, not only once per PR.
  - It is given this project's review focus areas (from `INSTRUCTIONS.md` §2) as extra instructions.
  - The job **fails when a finding of severity medium or higher is reported**, matching the local rule.
  - An owner-applied PR label lets an explicitly accepted finding pass, with the justification written in the PR. This mirrors the `accepted:` part of the commit trailer.
- **Branch protection on `main`:** changes reach `main` only through pull requests, and both CI jobs must pass. The rule applies to the owner too.
- **Supply-chain hygiene for the workflow itself:** third-party actions pinned to commit SHAs, minimal token permissions, no `pull_request_target`, and Dependabot updates for the pinned actions.
- **Documentation:** `INSTRUCTIONS.md` §2 (CI rows, PR-based workflow, accepting findings in CI) and the README development section.

**BREAKING (workflow):** direct pushes to `main` are no longer possible after branch protection is enabled. Every change, including `no-spec:` changes, goes through a pull request.

## Capabilities

### New Capabilities
- `ci-pipeline`: the automated checks and the AI security review that GitHub runs on pull requests and on `main`, and the merge rules they enforce.

### Modified Capabilities
None. The MCP server and export ingestion are unchanged.

## Impact

- **New files:** `.github/workflows/ci.yml`, `.github/security-review-instructions.md`, `.github/dependabot.yml`.
- **Changed files:** `INSTRUCTIONS.md`, `README.md`.
- **No Python code or runtime dependency changes.**
- **GitHub repository settings** (done by the owner, documented in the tasks):
  - repository secret `ANTHROPIC_API_KEY`;
  - branch protection on `main`;
  - Actions setting "Require approval for all external contributors";
  - label `security-review-accepted`.
- **Cost:** the AI review uses the owner's Anthropic API key. It is billed per PR push, a few cents per review for diffs of this size.
- **Owner workflow:** work happens on branches and is merged through PRs. The local pre-commit hook stays as the fast first check.
