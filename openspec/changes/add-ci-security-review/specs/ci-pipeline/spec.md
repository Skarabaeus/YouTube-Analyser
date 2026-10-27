# Spec Delta

## Purpose

Enforces the repository's quality and security rules on GitHub, so that no change reaches `main` without passing the automated checks and an AI security review, even when the local pre-commit hook was skipped.

## ADDED Requirements

### Requirement: Automated checks on pull requests and main
For every pull request targeting `main` and every push to `main`, CI SHALL run a secret scan over the new commits, the Python lint including security rules, the format check, the test suite and a vulnerability audit of the locked dependencies. The check job SHALL fail if any of them fails, or if the lockfile does not match `pyproject.toml`.

#### Scenario: Clean pull request
- **WHEN** a pull request contains only changes that pass lint, format, tests and audits
- **THEN** the check job succeeds

#### Scenario: Committed secret
- **WHEN** a commit in the pull request adds a credential-like string, such as a private key block
- **THEN** the check job fails and its log names the file and rule, with the secret value redacted

#### Scenario: Failing test
- **WHEN** a pull request breaks a test
- **THEN** the check job fails

#### Scenario: Vulnerable dependency
- **WHEN** a locked dependency has a known vulnerability
- **THEN** the check job fails and names the package and advisory

### Requirement: AI security review on pull requests
For every pull request targeting `main`, CI SHALL run an AI security review of the pull request's changes on every push to the pull request. The review SHALL use this project's review focus areas from `INSTRUCTIONS.md` §2. Findings SHALL be posted as comments on the pull request.

#### Scenario: New push to an open pull request
- **WHEN** a second commit is pushed to a pull request that was already reviewed
- **THEN** the review runs again on the updated changes

#### Scenario: Project focus areas
- **WHEN** a pull request makes `query_sql` accept statements other than `SELECT`
- **THEN** the review reports it as a finding

### Requirement: Security review gate
The AI security review job SHALL fail when the review reports at least one finding of severity medium or higher, unless the pull request carries the label `security-review-accepted`. The job SHALL also fail when the review could not run or its result cannot be read. Low-severity findings SHALL be reported without failing the job.

#### Scenario: Medium finding
- **WHEN** the review reports a finding with severity medium
- **THEN** the job fails and the finding is commented on the pull request

#### Scenario: Accepted finding
- **WHEN** the review reports a medium finding and the owner has added the label `security-review-accepted` to the pull request
- **THEN** the job succeeds and still lists the finding

#### Scenario: Review did not run
- **WHEN** the API key is missing or the review errors
- **THEN** the job fails with a message that names the cause

#### Scenario: Low finding only
- **WHEN** the review reports only low-severity findings
- **THEN** the job succeeds and the findings are commented

### Requirement: Merge protection for main
`main` SHALL accept changes only through pull requests whose check job and AI security review job have both succeeded on the latest commit. Direct pushes and force pushes to `main` SHALL be rejected, for the owner as well.

#### Scenario: Direct push
- **WHEN** the owner runs `git push origin main` with a new commit
- **THEN** GitHub rejects the push

#### Scenario: Failing check
- **WHEN** a pull request's check job or AI review job has failed
- **THEN** the pull request cannot be merged

### Requirement: Untrusted contributions
Workflows SHALL NOT run with repository secrets or write permissions on code from forks. A pull request from a fork SHALL NOT be mergeable until its AI security review has run on a branch of this repository.

#### Scenario: Pull request from a fork
- **WHEN** an external contributor opens a pull request from a fork
- **THEN** workflows run only after the owner approves them, the AI review job fails because no secret is available, and the pull request cannot be merged

### Requirement: Hardened workflow definition
Workflows SHALL grant the job token only the permissions each job needs, with read-only repository contents by default. Third-party actions SHALL be referenced by full commit SHA. Workflows SHALL NOT use the `pull_request_target` trigger. Every job SHALL have a timeout.

#### Scenario: Workflow review
- **WHEN** the workflow files are inspected
- **THEN** every `uses:` line of a third-party action references a 40-character commit SHA, only the AI review job may write pull-request comments, and no `pull_request_target` trigger exists
