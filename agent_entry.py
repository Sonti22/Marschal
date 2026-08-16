from __future__ import annotations

from pathlib import Path, PurePosixPath

import agent as core


_ORIGINAL_GROQ_PLAN = core.groq_plan
_ORIGINAL_VALIDATE_AND_APPLY = core.validate_and_apply_plan


def normalize_generated_text(content: str) -> str:
    """Remove trailing spaces/tabs while preserving a final newline when present."""
    had_final_newline = content.endswith(("\n", "\r"))
    normalized = "\n".join(line.rstrip(" \t") for line in content.splitlines())
    if had_final_newline:
        normalized += "\n"
    return normalized


def has_existing_test_infrastructure(repo_dir: Path) -> bool:
    tracked = core.run(["git", "ls-files"], cwd=repo_dir).splitlines()
    for raw in tracked:
        path = PurePosixPath(raw)
        name = path.name.lower()
        parts = {part.lower() for part in path.parts}
        if "tests" in parts or name.startswith("test_") or name.endswith("_test.py"):
            return True
        if name in {"pytest.ini", "tox.ini", "noxfile.py"}:
            return True
    return False


def looks_like_test_path(raw_path: str) -> bool:
    path = PurePosixPath(raw_path)
    name = path.name.lower()
    parts = {part.lower() for part in path.parts}
    return "tests" in parts or name.startswith("test_") or name.endswith("_test.py")


def policy_groq_plan(repo_name: str, snapshot: str, config: dict) -> dict:
    policy = """

--- MARSCHAL REVIEW POLICY ---
- Never emit trailing whitespace.
- Do not introduce a new test suite into a repository that has no visible test infrastructure.
- Do not rely on undeclared third-party test/runtime dependencies.
- If a safe improvement would require adding dependencies or bootstrapping CI/test tooling, choose a different small improvement or return an empty files array.
- Prefer an existing-file bug fix, validation/reliability fix, or a precise documentation correction over speculative scaffolding.
--- END POLICY ---
"""
    return _ORIGINAL_GROQ_PLAN(repo_name, snapshot + policy, config)


def policy_validate_and_apply(repo_dir: Path, plan: dict, config: dict) -> list[str]:
    files = plan.get("files", [])
    if any(
        isinstance(item, dict) and looks_like_test_path(str(item.get("path", "")))
        for item in files
    ) and not has_existing_test_infrastructure(repo_dir):
        raise RuntimeError(
            "Reviewer gate rejected test changes: target repository has no existing test infrastructure"
        )

    cleaned_plan = dict(plan)
    cleaned_files: list[dict] = []
    for item in files:
        if not isinstance(item, dict):
            cleaned_files.append(item)
            continue
        cleaned = dict(item)
        content = cleaned.get("content")
        if isinstance(content, str):
            cleaned["content"] = normalize_generated_text(content)
        cleaned_files.append(cleaned)
    cleaned_plan["files"] = cleaned_files
    return _ORIGINAL_VALIDATE_AND_APPLY(repo_dir, cleaned_plan, config)


def main() -> int:
    core.groq_plan = policy_groq_plan
    core.validate_and_apply_plan = policy_validate_and_apply
    return core.main()


if __name__ == "__main__":
    raise SystemExit(main())
