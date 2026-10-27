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

## 2. Security review on every commit

**Every commit is security reviewed before it is created**, small changes included.

### Procedure
1. Stage the intended changes.
2. Run the security review on the staged diff:
   - interactively in Claude Code: `/security-review`
   - headless (git hook): `scripts/security-review-hook.sh` runs `claude -p` with an explicit review prompt for the staged diff (the built-in `/security-review` needs a pushed `origin/HEAD`)
3. Resolve every finding of severity *medium* or higher. Alternatively, document an explicit, justified acceptance in the commit message.
4. All automated checks (below) pass.
5. Commit with the trailer:
   ```
   Security-Review: passed (<YYYY-MM-DD>, <claude|human>)[; accepted: <finding> — <reason>]
   ```

### Automated checks
| Check | Tool | Where |
|---|---|---|
| Secret scanning | `gitleaks` | pre-commit + CI |
| Python security lint | `ruff` (`S` rules) / `bandit` | pre-commit + CI |
| Dependency vulnerabilities | `pip-audit` | CI |
| Terraform | `terraform fmt/validate`, `tflint`, `checkov` | pre-commit + CI |
| AI security review | `claude -p "/security-review"` | pre-commit hook |
| AI security review | `anthropics/claude-code-security-review` GitHub Action | CI, every PR (once the GitHub remote exists) |

The local hook can be bypassed with `--no-verify`, so **CI is the enforcing gate** once the remote exists. Using `--no-verify` requires a stated reason in the commit message.

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
- Commit messages: imperative subject, OpenSpec change id in the body, `Security-Review:` trailer.
