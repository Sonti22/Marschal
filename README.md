# Marschal

Marschal is a conservative autonomous maintenance agent for repositories owned by `Sonti22`.

It runs from GitHub Actions, asks Groq (`openai/gpt-oss-120b`) for one small maintenance improvement, applies only a bounded set of text-file changes, performs local safety checks, pushes a dedicated branch, and opens a pull request for review.

## Safety model

- never commits directly to a target repository default branch;
- never auto-merges generated changes;
- refuses changes to `.github/`, secrets, `.env` files, lock files, binaries, and other sensitive paths;
- limits changed files and total changed lines;
- runs `git diff --check` and Python compilation checks when Python files change;
- leaves final acceptance to the target repository CI and a human review.

## Required GitHub Actions secrets

Create these in **Settings → Secrets and variables → Actions** for this repository:

- `GROQ_API_KEY` — a fresh Groq API key. Do not commit it to this repository.
- `GH_AGENT_TOKEN` — a fine-grained GitHub PAT for `Sonti22`, limited to only the target repositories, with `Contents: Read and write`, `Pull requests: Read and write`, and `Actions: Read-only`.

The token is used only by the GitHub Actions runner to clone target repositories, push a branch, and create a PR.

## Target repositories

Targets are configured in `agent-config.json`. By default Marschal rotates through a small set of active projects instead of touching every repository in the account.

## Manual run

Open **Actions → Marschal autonomous developer → Run workflow**. You can optionally provide one configured repository name.

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

Never paste live keys into source files, issues, pull requests, or workflow logs.
