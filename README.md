# Marschal

Marschal is an autonomous engineering agent for repositories owned by `Sonti22`, tuned to the quality bar of a **Python Tech Lead / Software Architect — Backend, Distributed Systems & AI**.

It runs from GitHub Actions, asks Groq (`openai/gpt-oss-120b`) for one bounded but high-leverage production improvement, applies only grounded changes to repository files it has fully inspected, performs deterministic reviewer checks, pushes a dedicated branch, and opens a pull request for review.

## Engineering profile

Marschal prioritizes work that demonstrates senior production engineering rather than activity for its own sake:

- backend architecture and service boundaries;
- async/concurrency correctness and resource lifecycle;
- transactions, idempotency and data consistency;
- API contracts, validation and error semantics;
- database access, query efficiency and connection handling;
- retries, timeouts, failure isolation and resilience;
- security and authentication/authorization correctness;
- observability, structured logging and operational reliability;
- performance, caching, backpressure and bounded resource usage;
- production AI concerns such as provider abstraction, structured outputs, rate limits, retries, fallbacks, evaluation hooks and deterministic tests when justified by the existing project.

Cosmetic churn, typo-only edits, style-only refactors, arbitrary renaming, speculative microservices and resume-padding complexity are rejected. If the available repository context does not justify a Tech-Lead-level change, Marschal makes no PR.

## Safety model

- never commits directly to a target repository default branch;
- never auto-merges generated changes;
- currently edits only existing files that were fully included in the repository snapshot;
- refuses changes to `.github/`, secrets, `.env` files, lock files, binaries and other sensitive paths;
- limits changed files and total changed lines;
- preserves existing Python top-level symbols and repository line-ending style;
- rejects newly introduced unused Python imports;
- does not bootstrap a new test suite when the repository has no existing test infrastructure;
- runs `git diff --check` and Python compilation checks when Python files change;
- leaves final acceptance to the target repository CI and human review.

## Required GitHub Actions secrets

Create these in **Settings → Secrets and variables → Actions** for this repository:

- `GROQ_API_KEY` — a fresh Groq API key. Do not commit it to this repository.
- `GH_AGENT_TOKEN` — a fine-grained GitHub PAT for `Sonti22`, limited to only the target repositories, with `Contents: Read and write`, `Pull requests: Read and write`, and `Actions: Read-only`.

The token is used only by the GitHub Actions runner to clone target repositories, push a branch, and create a PR.

## Target repositories

Targets are configured in `agent-config.json`. Marschal rotates through the configured projects instead of touching every repository in the account.

The context budget is intentionally large enough to inspect multiple complete architecture-relevant files while staying bounded for the Groq free-tier workflow. The change budget allows a focused multi-file fix when justified, but the reviewer gates still prefer a small blast radius.

## Manual run

Open **Actions → Marschal autonomous developer → Run workflow**. You can optionally provide one configured repository name and choose dry-run mode.

## Local checks

```bash
python -m unittest discover -s tests -v
```

## Environment

The workflow expects:

```text
GROQ_API_KEY=...
GH_AGENT_TOKEN=...
GROQ_MODEL=openai/gpt-oss-120b
```

Never paste live keys into source files, issues, pull requests or workflow logs.
