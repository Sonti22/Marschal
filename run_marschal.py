from __future__ import annotations

import os
import re
import subprocess
import sys

PR_RE = re.compile(r"Created PR:\s+(https://github\.com/\S+/pull/\d+)")


def main() -> int:
    agent = subprocess.run(
        [sys.executable, "agent_strict.py"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(agent.stdout, end="")
    if agent.returncode != 0:
        return agent.returncode

    match = PR_RE.search(agent.stdout)
    if not match:
        return 0

    pr_url = match.group(1)
    token = os.getenv("GH_AGENT_TOKEN", "").strip()
    env = os.environ.copy()
    if token:
        env["GH_TOKEN"] = token
        env["GITHUB_TOKEN"] = token

    print(f"Waiting for target repository checks: {pr_url}")
    try:
        checks = subprocess.run(
            ["gh", "pr", "checks", pr_url, "--watch", "--interval", "10"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("Target checks did not finish within 15 minutes; PR remains open for review.")
        return 0

    print(checks.stdout, end="")
    if checks.returncode != 0:
        output = checks.stdout.lower()
        if "no checks reported" in output or "no checks" in output:
            print("No GitHub checks are configured for this PR; leaving it open for review.")
            return 0
        print("One or more target repository checks failed. The PR was not merged.")
        return checks.returncode

    print("Target repository checks passed. The PR remains open for human review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
