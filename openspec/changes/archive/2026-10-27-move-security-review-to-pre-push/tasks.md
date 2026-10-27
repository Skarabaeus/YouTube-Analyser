# Tasks

## 1. Hooks

- [x] 1.1 Rewrite `scripts/security-review-hook.sh` for the pre-push stage (D1–D3): compute the unpushed commits, skip if there are none, review commit by commit, and pass `Security-Review-Accepted` trailers to the prompt. Verify that it skips when nothing is unpushed, that `bash -n` passes, and that the computed list equals `git rev-list HEAD --not --remotes=origin`.
- [x] 1.2 Update `.pre-commit-config.yaml` (D4) and run `uv run pre-commit install`. Verify that `.git/hooks/pre-commit` and `.git/hooks/pre-push` exist and that `uv run pre-commit run --all-files` passes the pre-commit stage.

## 2. Documentation

- [x] 2.1 Rewrite `INSTRUCTIONS.md` §2 covering:
  - the procedure (pre-commit checks, pre-push AI review of all unpushed commits);
  - accepting findings via `Security-Review-Accepted:`;
  - bypass rules;
  - the automated-checks table without CI rows;
  - the commit-message conventions without the `Security-Review: passed` trailer.

  Verify that no reference to CI as a gate or to the old trailer remains.
- [x] 2.2 Update `CLAUDE.md`, the README development section, `SPEC.md` (§10, §12, §14) and the tasks rule in `openspec/config.yaml`. Verify with `git grep -n "per-commit\|Security-Review: passed\|before committing"` that no stale wording remains outside archived changes.
- [x] 2.3 Remove the withdrawn change `openspec/changes/add-ci-security-review/`. Verify that `openspec list` shows only this change.

## 3. Verification

- [x] 3.1 Run the automated checks (`uv run pre-commit run --all-files`, `uv run pytest`, `uv run pip-audit`, `openspec validate move-security-review-to-pre-push --strict`). The final AI security review runs through the new pre-push hook when the owner pushes this change.
