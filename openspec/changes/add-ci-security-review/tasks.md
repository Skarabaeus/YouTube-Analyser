# Tasks

## 1. Checks job

- [ ] 1.1 Create `.github/workflows/ci.yml` with the triggers, top-level `permissions: contents: read`, concurrency group and timeouts from D6, and the `checks` job from D2. Resolve each action to a full commit SHA with `gh api repos/<owner>/<action>/commits/<tag>`, writing the tag as a trailing comment. Verify with `actionlint` (via `brew install actionlint`) and a grep showing every `uses:` line ends in a 40-hex SHA.
- [ ] 1.2 Add the gitleaks step: download the 8.30.1 linux x64 tarball, check it against the SHA-256 from the release's checksums file (hard-coded in the workflow), and scan the PR or push commit range with `--redact`. Verify locally that the same command line passes on the current history.
- [ ] 1.3 Create `.github/dependabot.yml` for `github-actions` (weekly). Verify that the YAML parses.

## 2. Security-review job

- [ ] 2.1 Write `.github/security-review-instructions.md` from the focus areas in `INSTRUCTIONS.md` §2, phrased as review instructions. Include the v1 specifics: read-only MCP, the `query_sql` sandbox and caps, export parsing as untrusted input, no real data in the repo, and titles as data. Verify that every focus area appears.
- [ ] 2.2 Add the `security-review` job (D3): PR-only, `pull-requests: write`, checkout with `fetch-depth: 2`, the action pinned to its SHA, and inputs `run-every-commit: true`, `comment-pr: true`, `upload-results: false`, `custom-security-scan-instructions`. Verify with `actionlint`.
- [ ] 2.3 Add the gate step (D3/D4): fail on a missing key or a missing/invalid results file; fail on ≥ 1 HIGH/MEDIUM finding unless the label `security-review-accepted` is present; write the findings to the job summary. Extract it to `scripts/ci-security-gate.sh` and test it locally with crafted result files: none, low only, medium, medium + label, missing, invalid JSON. Confirm the JSON field names against the action's `claudecode/github_action_audit.py` at the pinned SHA.

## 3. Documentation

- [ ] 3.1 Update `INSTRUCTIONS.md` §2:
  - PR-based workflow (branch → PR → both checks green → merge);
  - the CI rows of the automated-checks table (actual tool and file names, and the hook row now says `scripts/security-review-hook.sh` instead of `claude -p "/security-review"`);
  - accepting a finding in CI (label + `Security-Review accepted: …` line in the PR description);
  - disabling branch protection only with a written reason;
  - `.github/security-review-instructions.md` must be kept in sync with the focus areas.

  Verify that no line still says CI comes "once the remote exists".
- [ ] 3.2 Update the README development section (PR workflow, CI jobs). Verify that the README and `INSTRUCTIONS.md` don't contradict each other.

## 4. Repository setup and rollout (owner + agent)

- [ ] 4.1 Owner adds the repository secret `ANTHROPIC_API_KEY`, which must be enabled for Claude Code usage. Owner sets *Settings → Actions → General → Fork pull request workflows* to "Require approval for all external contributors". Agent creates the label `security-review-accepted` with `gh label create`. Verify with `gh secret list` and `gh label list`.
- [ ] 4.2 Run the final security review and the automated checks on the branch (`uv run pre-commit run --all-files`, `uv run pytest`, `uv run pip-audit`, `actionlint`), then commit with the `Security-Review:` trailer. Open this change as a PR from branch `add-ci-security-review` and confirm that both jobs pass and the review comments appear (or report "no findings"). Merge after the owner approves.
- [ ] 4.3 Enable branch protection on `main` per D5 with `gh api -X PUT repos/Skarabaeus/YouTube-Analyser/branches/main/protection`. Verify by reading it back with `gh api …/protection`: the required checks `checks` and `security-review`, `enforce_admins` true, force pushes off.

## 5. Verification against the spec

- [ ] 5.1 Direct push: a test commit pushed to `main` is rejected. Then reset the local branch.
- [ ] 5.2 Throwaway PR containing a fake private key block in a new file: the `checks` job fails on gitleaks with the value redacted.
- [ ] 5.3 Throwaway PR that changes `query_sql` validation to allow non-SELECT statements:
  - the review reports a medium-or-higher finding, the `security-review` job fails, and merge is blocked;
  - after adding the label `security-review-accepted`, the job succeeds;
  - a second push to the PR re-runs the review.

  Close the PRs unmerged and delete their branches.
