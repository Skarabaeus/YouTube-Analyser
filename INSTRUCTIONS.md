# Working Instructions

These rules apply to every contributor, human or AI agent, and to every change in this repository.

## 1. Spec-driven development with OpenSpec

`SPEC.md` is the initial product spec. **All changes go through [OpenSpec](https://github.com/Fission-AI/OpenSpec)** (CLI ≥ 1.14, schema `spec-driven`, initialised for Claude Code). As changes are archived, the source of truth moves to `openspec/specs/`, and `SPEC.md` remains as historical context.

### Workflow per change
| Step | Claude Code | CLI |
|---|---|---|
| Explore (optional) | `/openspec-explore` | — |
| Propose | `/openspec-propose "<idea>"` → `openspec/changes/<id>/` with `proposal.md`, `design.md`, `specs/<capability>/spec.md` (deltas), `tasks.md` | `openspec new change <id>` |
| Validate | — | `openspec validate <id> --strict` |
| Approve | Owner reviews and approves the proposal. **No implementation without approval.** | — |
| Implement | `/openspec-apply-change` — work through `tasks.md`, ticking items off | `openspec status --change <id>` |
| Archive | `/openspec-archive-change` after merge/deploy → deltas merged into `openspec/specs/` | `openspec archive <id>` |

### Rules
- Change ids are verb-led kebab-case, e.g. `add-video-daily-ingestion`.
- One change = one coherent capability or fix. Do not mix unrelated work.
- Every requirement has at least one scenario, and every scenario has at least one test.
- If implementation shows the spec is wrong, update the change's deltas first, then the code.
- Every `design.md` contains a **Security considerations** section and, where data is stored, a **Data retention** section referencing `SPEC.md` §7.
- Trivial changes (typos, formatting, patch-level dependency bumps without behaviour change) may skip a proposal. The commit subject then starts with `no-spec:` and gives the reason.

## 2. Security review before every push

**No commit leaves this machine without a security review**, small changes included. Fast, deterministic checks run on every commit. The AI security review runs before every push and covers **every commit that is not on the remote yet**.

### Procedure
1. **Commit.** The pre-commit hook runs gitleaks on the staged changes, plus `ruff check` and `ruff format --check`. Fix whatever it reports.
2. **Push.** The pre-push hook (`scripts/security-review-hook.sh`) collects all commits that are not on the remote yet (`git rev-list <pushed ref> --not --remotes=<remote>`). It has Claude Code (`claude -p`) review them **one commit at a time**, so a secret added in one commit and removed in a later one is still caught. The push is blocked unless the review ends with `SECURITY_REVIEW: PASS`.
3. **Resolve every finding of severity *medium* or higher** with a new commit, then push again. Alternatively, accept it explicitly (below).
4. Before opening a change for review or archiving it, also run `uv run pytest` and `uv run pip-audit`.

Install both hooks once per clone with `uv run pre-commit install`. It installs `pre-commit` and `pre-push`, as set in `.pre-commit-config.yaml`. The hook needs a logged-in Claude Code CLI (`claude`).

### Accepting a finding
Add a trailer to the commit that contains the finding (`git commit --amend`, or a later commit before pushing):
```
Security-Review-Accepted: <finding> — <reason>
```
The pre-push review receives all such trailers of the commits it reviews. It still reports a matching finding, but does not fail on it. Accept only findings you have understood, and give a concrete reason.

### Skipping the review
`git push --no-verify` or `SKIP=security-review git push` skip the AI review. Use them only when the review cannot run (e.g. no network). Then run the review as soon as possible, either interactively with `/security-review` in Claude Code or by running `scripts/security-review-hook.sh` with `PRE_COMMIT_TO_REF` set to the pushed commit and `PRE_COMMIT_REMOTE_NAME` to a remote that does not have it. Record the reason in the next commit message.

There is deliberately **no CI review**: this project has a single developer. A CI-based review would need a separately billed Anthropic API key and adds little over the local gate. Revisit this when other people contribute.

### Automated checks
| Check | Tool | Where |
|---|---|---|
| Secret scanning | `gitleaks` | pre-commit (every commit) |
| Python lint incl. security rules | `ruff check` (`S` rules) | pre-commit |
| Formatting | `ruff format --check` | pre-commit |
| AI security review | `scripts/security-review-hook.sh` (`claude -p`, all unpushed commits) | pre-push |
| Tests | `uv run pytest` | manually, before pushing |
| Dependency vulnerabilities | `uv run pip-audit` | manually, before pushing and after dependency changes |
| Terraform | `terraform fmt/validate`, `tflint`, `checkov` | pre-commit, added with the first `infra/` change |

### Review focus areas for this project
- **Secrets:** OAuth refresh tokens and the client secret are never committed, logged or written to S3. They live only in SSM SecureString, readable by the ingest role alone.
- **IAM:**
  - Separate roles for ingest, retention, operator CLI and read-only MCP.
  - Resource ARNs scoped by tenant prefix.
  - No `*` actions or resources without written justification.
- **S3:** Block Public Access, encryption, TLS-only policy, lifecycle rules as per `SPEC.md` §7.
- **MCP server:**
  - Strictly read-only.
  - `query_sql` limited to `SELECT`/`WITH` over tenant-filtered views, with row limit and timeout.
  - No path/command injection via tool arguments; output size capped.
- **Manual imports:** untrusted input. Strict schema validation, size limits, no formula/CSV injection into outputs.
- **Data retention:** changes touching storage must not break the YouTube API policy compliance in `SPEC.md` §7.
- **Network exposure:** any change that makes a component reachable from the network (e.g. remote MCP) needs a threat-model note in its OpenSpec `design.md`.
- **Dependencies:** new ones are justified in the proposal; versions pinned via `uv.lock`; Terraform providers pinned via `.terraform.lock.hcl`.

## 3. General conventions
- Never commit `.env`, credentials, tokens, Terraform state or `*.tfvars` containing personal data, or real API responses. Test fixtures are anonymised.
- AWS changes go only through Terraform (`infra/`). The Google Cloud setup follows `docs/setup-google-youtube.md`.
- Commit messages: imperative subject, OpenSpec change id in the body, and `Security-Review-Accepted:` trailers for accepted findings (§2).
