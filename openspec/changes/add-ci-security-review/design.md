# Design

## Context

- See proposal.md, "Why". The requirements are in `specs/ci-pipeline/spec.md`.
- Repository: public, default branch `main`, no branch protection, no workflows yet. The owner is the only contributor.
- Local gate today (`.pre-commit-config.yaml`): gitleaks 8.30.1, ruff check, ruff format, and `scripts/security-review-hook.sh`, which runs `claude -p` on the staged diff. `pip-audit` and `pytest` are run by hand.
- `anthropics/claude-code-security-review` (checked 2026-10-27):
  - It has no releases, so it can only be pinned to a commit on `main` (currently `0c6a49f1fa56a1d472575da86a94dbc1edb78eda`).
  - Inputs used here: `claude-api-key`, `comment-pr`, `upload-results`, `run-every-commit`, `custom-security-scan-instructions`, `claude-model`.
  - Output: `findings-count` and `results-file` (JSON).
  - It **never fails the job on findings**, and turns its own errors into warnings. A gate therefore has to be built around it.
  - By default it reviews a PR only once (cache keyed by PR); `run-every-commit: true` disables that.
  - Its README states it is not hardened against prompt injection and should only review trusted PRs.

## Goals / Non-Goals

**Goals:**
- One workflow file whose two jobs, `checks` and `security-review`, are the required status checks for `main`.
- The CI result matches the local hook: the same tools, the same severity threshold, and the same way to accept a finding.

**Non-Goals:**
- No Terraform/IaC checks (`terraform fmt/validate`, `tflint`, `checkov`). There is no `infra/` yet; they are added with the first Terraform change.
- No release, packaging or deployment.
- No CodeQL or other additional scanners.
- No removal of the local pre-commit hook. It stays as the fast first check.

## Decisions

### D1 One workflow, two jobs
`.github/workflows/ci.yml` has two jobs:
- **`checks`:** triggered by `pull_request` (to `main`) and `push` (to `main`).
- **`security-review`:** triggered by `pull_request` only.

Each job maps to one required status check. *Alternative:* separate workflow files. Rejected: there is more to pin and keep in sync, with no benefit.

### D2 Checks job
1. `actions/checkout` with `fetch-depth: 0`, needed for gitleaks' commit range.
2. `astral-sh/setup-uv` with the Python version from `.python-version`.
3. `uv sync --locked`. It fails if `uv.lock` does not match `pyproject.toml`.
4. `uv run ruff check`, `uv run ruff format --check`, `uv run pytest`, `uv run pip-audit`.
5. gitleaks:
   - It is downloaded as release binary 8.30.1, the same version as the local hook, and verified against a SHA-256 checksum hard-coded in the workflow.
   - It runs `gitleaks git --redact --log-opts "<base>..<head>"`. The base and head are the PR's base and head SHAs, or for a push, `before..after`.
   - *Alternative:* `gitleaks/gitleaks-action`. It wraps the same binary, but downloads it without our own checksum and has its own licence rules. A pinned, verified binary is simpler to audit.

### D3 Security-review job
1. `actions/checkout` of the PR head with `fetch-depth: 2`, as the action's README requires.
2. `anthropics/claude-code-security-review@<sha>` with:
   - `claude-api-key: ${{ secrets.ANTHROPIC_API_KEY }}`;
   - `run-every-commit: true`: every push to the PR is reviewed, matching "every commit is reviewed";
   - `comment-pr: true`;
   - `upload-results: false` (see Data retention);
   - `custom-security-scan-instructions: .github/security-review-instructions.md`. This file contains the project focus areas from `INSTRUCTIONS.md` §2, phrased as review instructions. `INSTRUCTIONS.md` refers to it so they stay in sync.
   - `claude-model` is left at the action default; it can be set later without changing the spec.
3. **Gate step** (`if: always()`), a small shell + `jq` script:
   - Fail if the API key is missing, or if the results file is missing or not valid JSON (review did not run).
   - Count the findings with severity `HIGH` or `MEDIUM`, case-insensitive. If the count is > 0 and the PR has no `security-review-accepted` label, fail and list the findings in the job summary.
   - Otherwise succeed. Low findings are only listed.
   - Labels are read from the event payload (`github.event.pull_request.labels`). The workflow also triggers on `labeled` and `unlabeled`, so adding the label re-runs the job.
   - The exact field names (`findings[].severity`) are checked against a real run during implementation (task 2.3). If they differ, only the gate script changes.
   - *Alternative:* rely on PR comments and human judgment. Rejected: that is not a gate.

### D4 Accepting a finding
- The owner adds the label `security-review-accepted` and writes the justification into the PR description: `Security-Review accepted: <finding> — <reason>`. This is the CI counterpart of the commit-trailer `accepted:` clause.
- Only users with write access can set labels, so for a fork PR the label is the owner's decision.
- The label re-triggers the review (and its API cost). This is accepted: label changes are rare.

### D5 Branch protection
A classic branch protection rule on `main`, set via `gh api` (documented in tasks):
- Require a pull request before merging, with 0 approvals: the sole owner cannot approve their own PR.
- Require the status checks `checks` and `security-review`, and require the branch to be up to date ("strict").
- `enforce_admins: true`, so the rule applies to the owner too.
- Force pushes and deletion are not allowed.

*Alternative:* rulesets. They are equivalent here; the classic rule is a single `gh api` call and easy to read back.

### D6 Event triggers and permissions
- **Triggers:**
  - `pull_request` with `opened, synchronize, reopened, ready_for_review, labeled, unlabeled`, and `branches: [main]`;
  - `push` with `branches: [main]` (checks job only).
- **Permissions:** top-level `permissions: contents: read`. The `security-review` job adds `pull-requests: write` for comments.
- **Concurrency:** grouped per PR or ref, with `cancel-in-progress: true`, so superseded pushes don't burn API cost.
- **Timeouts:** `timeout-minutes: 15` on checks and `30` on security-review (the action's own default timeout is 20).

### D7 Dependabot for actions
- `.github/dependabot.yml` updates `github-actions` weekly, so SHA pins don't rot.
- Python dependency updates via Dependabot are not part of this change; `pip-audit` already fails on known vulnerabilities.

## Security considerations

- **Secrets:**
  - `ANTHROPIC_API_KEY` is a repository secret. It is used only in the `security-review` job, passed only as an action input, and never echoed.
  - GitHub does not pass secrets to `pull_request` runs from forks. Owner approval of workflow runs from external contributors (repository setting) adds a second barrier.
- **Prompt injection:**
  - The action reviews the diff with an LLM, and a malicious diff can try to talk the reviewer out of a finding.
  - Mitigations: the review is a gate in addition to deterministic checks (gitleaks, ruff `S`, tests), not a replacement. Fork PRs cannot pass the gate without the owner moving the code to a branch of this repo after reading it. The repo has one contributor.
- **Supply chain:**
  - Every third-party action is pinned by commit SHA. The gitleaks binary is checksum-verified. `uv sync --locked` enforces the lockfile.
  - Residual risk: the security-review action installs its own tools at run time (Claude Code CLI, Python packages) without pins we control. Accepted: the job holds only the API key and a token scoped to PR comments; it has no write access to contents.
- **Token scope:** read-only contents everywhere. Only the review job can write PR comments. No `pull_request_target`, so fork code never runs with a write token.
- **Public disclosure:** on a public repo, PR comments with findings are visible before the fix is merged.
  - Accepted: the project is a local tool with no deployed service, and the owner is the only contributor.
  - For a sensitive finding, the owner can fix it before opening the PR, using the local hook.
- **Exports never reach CI:** real data is git-ignored (`report-exports/`, `data/`, `*.csv` outside fixtures), so CI and the reviewer only see synthetic fixtures.

## Data retention

No channel data is stored or processed in CI.
- Workflow logs and PR comments follow GitHub's defaults: logs are kept 90 days, comments until deleted.
- `upload-results: false` avoids an extra artifact copy of the findings.
- The Anthropic API processes the PR diff under the API's data usage terms; the diff contains only code and synthetic fixtures.

## Risks / Trade-offs

- [The action changes its output format or behaviour on `main`] → The SHA pin freezes it. Dependabot proposes updates as PRs, which pass through the same gate.
- [False positives block merges] → Accept via label with a written reason (D4).
- [Review cost grows with PR count] → `run-every-commit` is a deliberate choice. Concurrency cancels superseded runs. Cost per review is cents.
- [Owner locked out by `enforce_admins`] → The owner can temporarily disable the rule in the repository settings. That is visible in the audit log and requires a written reason (documented in `INSTRUCTIONS.md`, like `--no-verify`).
- [Anthropic API outage] → The review job fails closed. The owner waits or disables the rule temporarily, as above.

## Migration Plan

1. Owner sets the secret `ANTHROPIC_API_KEY` and the external-contributor approval setting, and creates the label.
2. Open this change as the first PR. Both jobs must pass on it, so the gate reviews its own introduction. Merge it **before** enabling branch protection, so the required check names are known to GitHub.
3. Enable branch protection.
4. Verify with throwaway test PRs (tasks), then close them unmerged.

Rollback: delete the branch protection rule and the workflow file. No data is affected.
