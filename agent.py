from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

CONFIG_PATH = Path(__file__).with_name("agent-config.json")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

PROTECTED_PREFIXES = (
    ".git/",
    ".github/",
    "node_modules/",
    ".venv/",
    "venv/",
    "dist/",
    "build/",
)
PROTECTED_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
}
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".html",
    ".css",
}


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None, check: bool = True) -> str:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{result.stdout}")
    return result.stdout.strip()


def is_safe_path(raw_path: str) -> bool:
    if not raw_path or "\\" in raw_path:
        return False
    path = PurePosixPath(raw_path)
    if path.is_absolute() or ".." in path.parts:
        return False
    normalized = path.as_posix()
    if normalized in PROTECTED_NAMES or path.name in PROTECTED_NAMES:
        return False
    if any(normalized.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return False
    if path.name.startswith(".env"):
        return False
    return path.suffix.lower() in TEXT_SUFFIXES


def select_target(config: dict) -> str:
    targets = list(config["targets"])
    requested = os.getenv("TARGET_REPO", "").strip()
    if requested:
        if requested not in targets:
            raise ValueError(f"TARGET_REPO must be one of: {', '.join(targets)}")
        return requested
    index = dt.date.today().toordinal() % len(targets)
    return targets[index]


def snapshot_repository(repo_dir: Path, config: dict) -> str:
    tracked = run(["git", "ls-files"], cwd=repo_dir).splitlines()
    preferred = sorted(
        (p for p in tracked if is_safe_path(p)),
        key=lambda p: (
            0 if PurePosixPath(p).name.lower() in {"readme.md", "pyproject.toml", "package.json", "go.mod"} else 1,
            p.count("/"),
            p,
        ),
    )

    chunks: list[str] = []
    total = 0
    count = 0
    max_files = int(config["snapshot_max_files"])
    max_file_bytes = int(config["snapshot_max_file_bytes"])
    max_total_chars = int(config["snapshot_max_total_chars"])

    for rel in preferred:
        if count >= max_files or total >= max_total_chars:
            break
        path = repo_dir / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > max_file_bytes or b"\x00" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        remaining = max_total_chars - total
        block = f"\n--- FILE: {rel} ---\n{text}\n"
        if len(block) > remaining:
            block = block[:remaining]
        chunks.append(block)
        total += len(block)
        count += 1

    if not chunks:
        raise RuntimeError("No safe text files were available for the repository snapshot")
    return "".join(chunks)


def _groq_retry_delay(exc: urllib.error.HTTPError, detail: str, attempt: int) -> float:
    header = exc.headers.get("Retry-After") if exc.headers else None
    if header:
        try:
            return min(max(float(header), 1.0), 90.0)
        except ValueError:
            pass
    match = re.search(r"try again in\s+([0-9.]+)s", detail, flags=re.IGNORECASE)
    if match:
        return min(max(float(match.group(1)) + 1.0, 1.0), 90.0)
    return min(2.0 ** attempt * 5.0, 60.0)


def groq_plan(repo_name: str, snapshot: str, config: dict) -> dict:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    model = os.getenv("GROQ_MODEL", config.get("model", "openai/gpt-oss-120b"))
    system = (
        "You are Marschal, a conservative senior software maintainer. "
        "Make exactly one small, useful, reviewable improvement to an existing repository. "
        "Prefer a bug fix, a focused test improvement, a reliability improvement, or documentation that corrects a real gap. "
        "Do not add dependencies, do not change CI/workflows, do not touch secrets or environment files, "
        "do not perform broad rewrites, and do not invent APIs that are not visible in the snapshot. "
        "Return only a JSON object."
    )
    user = f"""
Repository: {repo_name}

Return this JSON shape:
{{
  "title": "short PR title",
  "summary": "why this change is useful",
  "files": [
    {{"path": "relative/path.ext", "content": "COMPLETE replacement file content"}}
  ]
}}

Rules:
- Maximum {config['max_changed_files']} files.
- Only modify files for which you can provide complete valid content.
- New files are allowed only when clearly useful, such as a focused test or documentation file.
- Never touch .github/, .env*, lock files, generated files, binaries, credentials, or secrets.
- Do not add or change third-party dependencies.
- Keep the total diff small and under {config['max_total_changed_lines']} changed lines.
- If no safe, useful change can be justified from the snapshot, return an empty files array.

Repository snapshot:
{snapshot}
"""

    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.15,
            "max_completion_tokens": int(config.get("max_completion_tokens", 1800)),
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")

    max_attempts = max(1, int(config.get("groq_max_attempts", 4)))
    body: dict | None = None
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            GROQ_URL,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Marschal/1.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1500]
            if exc.code == 429 and attempt < max_attempts:
                delay = _groq_retry_delay(exc, detail, attempt)
                print(f"Groq rate limit reached; retrying in {delay:.1f}s ({attempt}/{max_attempts}).")
                time.sleep(delay)
                continue
            raise RuntimeError(f"Groq API returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            if attempt < max_attempts:
                delay = min(2.0 ** attempt, 15.0)
                print(f"Groq network error; retrying in {delay:.1f}s ({attempt}/{max_attempts}).")
                time.sleep(delay)
                continue
            raise RuntimeError(f"Groq API request failed: {exc.reason}") from exc

    if body is None:
        raise RuntimeError("Groq API did not return a response")

    content = body["choices"][0]["message"]["content"]
    plan = json.loads(content)
    if not isinstance(plan, dict) or not isinstance(plan.get("files", []), list):
        raise RuntimeError("Groq returned an invalid plan")
    return plan


def validate_and_apply_plan(repo_dir: Path, plan: dict, config: dict) -> list[str]:
    files = plan.get("files", [])
    max_files = int(config["max_changed_files"])
    max_bytes = int(config["max_output_file_bytes"])
    if len(files) > max_files:
        raise RuntimeError(f"Plan changes {len(files)} files; limit is {max_files}")

    changed: list[str] = []
    root = repo_dir.resolve()
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError("Each plan file must be an object")
        rel = str(item.get("path", "")).strip()
        content = item.get("content")
        if not is_safe_path(rel):
            raise RuntimeError(f"Refusing unsafe path: {rel}")
        if not isinstance(content, str):
            raise RuntimeError(f"Missing text content for {rel}")
        encoded = content.encode("utf-8")
        if len(encoded) > max_bytes:
            raise RuntimeError(f"Refusing oversized output file: {rel}")

        destination = (repo_dir / rel).resolve()
        if root not in destination.parents and destination != root:
            raise RuntimeError(f"Path escapes repository: {rel}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")
        changed.append(rel)
    return changed


def validate_diff(repo_dir: Path, config: dict) -> list[str]:
    run(["git", "diff", "--check"], cwd=repo_dir)
    names = [line for line in run(["git", "status", "--porcelain"], cwd=repo_dir).splitlines() if line]
    if not names:
        return []

    changed_files = run(["git", "diff", "--name-only"], cwd=repo_dir).splitlines()
    untracked = run(["git", "ls-files", "--others", "--exclude-standard"], cwd=repo_dir).splitlines()
    all_changed = sorted(set(changed_files + untracked))
    if len(all_changed) > int(config["max_changed_files"]):
        raise RuntimeError("Changed-file safety limit exceeded")
    for rel in all_changed:
        if not is_safe_path(rel):
            raise RuntimeError(f"Diff contains protected path: {rel}")

    numstat = run(["git", "diff", "--numstat"], cwd=repo_dir).splitlines()
    total_lines = 0
    for row in numstat:
        parts = row.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            total_lines += int(parts[0]) + int(parts[1])
    for rel in untracked:
        try:
            total_lines += len((repo_dir / rel).read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            raise RuntimeError(f"Unable to validate untracked file: {rel}")
    if total_lines > int(config["max_total_changed_lines"]):
        raise RuntimeError(f"Changed-line safety limit exceeded: {total_lines}")

    python_files = [repo_dir / p for p in all_changed if p.endswith(".py")]
    for path in python_files:
        run(["python", "-m", "py_compile", str(path)], cwd=repo_dir)
    return all_changed


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:40] or "maintenance"


def main() -> int:
    config = load_config()
    owner = config["owner"]
    repo = select_target(config)
    gh_token = os.getenv("GH_AGENT_TOKEN", "").strip()
    if not gh_token:
        raise RuntimeError("GH_AGENT_TOKEN is not configured")

    env = os.environ.copy()
    env["GH_TOKEN"] = gh_token
    env["GITHUB_TOKEN"] = gh_token

    with tempfile.TemporaryDirectory(prefix="marschal-") as tmp:
        repo_dir = Path(tmp) / repo
        run(["gh", "repo", "clone", f"{owner}/{repo}", str(repo_dir), "--", "--depth=1"], env=env)
        default_branch = run(["gh", "api", f"repos/{owner}/{repo}", "--jq", ".default_branch"], env=env)
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        branch = f"marschal/{timestamp}"
        run(["git", "checkout", "-b", branch], cwd=repo_dir)

        snapshot = snapshot_repository(repo_dir, config)
        plan = groq_plan(repo, snapshot, config)
        files = plan.get("files", [])
        if not files:
            print(f"Marschal found no justified safe change for {owner}/{repo}.")
            return 0

        validate_and_apply_plan(repo_dir, plan, config)
        changed = validate_diff(repo_dir, config)
        if not changed:
            print("Plan produced no actual changes.")
            return 0

        title = str(plan.get("title") or "Marschal maintenance improvement").strip()[:120]
        summary = str(plan.get("summary") or "Small automated maintenance improvement.").strip()
        commit_message = f"chore: {slugify(title).replace('-', ' ')}"

        if os.getenv("MARSCHAL_DRY_RUN", "").lower() in {"1", "true", "yes"}:
            print(f"DRY RUN: {owner}/{repo} -> {title}")
            print("Changed files:")
            for rel in changed:
                print(f"- {rel}")
            return 0

        run(["git", "config", "user.name", "Sonti22"], cwd=repo_dir)
        run(["git", "config", "user.email", "162977312+Sonti22@users.noreply.github.com"], cwd=repo_dir)
        run(["git", "add", "--", *changed], cwd=repo_dir)
        run(["git", "commit", "-m", commit_message], cwd=repo_dir)
        run(["git", "push", "-u", "origin", branch], cwd=repo_dir, env=env)

        body = (
            f"{summary}\n\n"
            "### Safety checks\n"
            "- `git diff --check` passed\n"
            "- Python files, when changed, passed `py_compile`\n"
            f"- Changed files: {len(changed)} / {config['max_changed_files']} allowed\n\n"
            "Generated by **Marschal** using Groq. This PR is intentionally not auto-merged."
        )
        pr_url = run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                f"{owner}/{repo}",
                "--base",
                default_branch,
                "--head",
                branch,
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=repo_dir,
            env=env,
        )
        print(f"Created PR: {pr_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
