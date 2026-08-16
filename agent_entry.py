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


def unsafe_plan_paths(plan: dict) -> list[str]:
    unsafe: list[str] = []
    for item in plan.get("files", []):
        if not isinstance(item, dict):
            unsafe.append("<invalid-plan-item>")
            continue
        rel = str(item.get("path", "")).strip()
        if not core.is_safe_path(rel):
            unsafe.append(rel or "<empty-path>")
    return unsafe


def _review_policy(extra: str = "") -> str:
    policy = """

--- MARSCHAL REVIEW POLICY ---
- Never emit trailing whitespace.
- Never modify .gitignore, .gitattributes, editor configuration, hidden dotfiles, lock files, CI/workflow files, secrets, or environment files.
- Do not introduce a new test suite into a repository that has no visible test infrastructure.
- Do not rely on undeclared third-party test/runtime dependencies.
- If a safe improvement would require adding dependencies or bootstrapping CI/test tooling, choose a different small improvement or return an empty files array.
- Prefer an existing-file bug fix, validation/reliability fix, or a precise documentation correction over speculative scaffolding.
- Every returned path must satisfy the repository safety policy; if unsure, return an empty files array.
--- END POLICY ---
"""
    if extra:
        policy += f"\n{extra}\n"
    return policy


def policy_groq_plan(repo_name: str, snapshot: str, config: dict) -> dict:
    plan = _ORIGINAL_GROQ_PLAN(repo_name, snapshot + _review_policy(), config)
    unsafe = unsafe_plan_paths(plan)
    if not unsafe:
        return plan

    paths = ", ".join(unsafe)
    print(f"Reviewer preflight rejected protected paths: {paths}. Requesting one safer alternative.")
    feedback = (
        "The previous candidate was rejected because it used protected or unsupported paths: "
        f"{paths}. Produce a DIFFERENT small improvement using only ordinary safe source/documentation files. "
        "Do not return any dotfile. If no such change is justified, return an empty files array."
    )
    return _ORIGINAL_GROQ_PLAN(repo_name, snapshot + _review_policy(feedback), config)


def policy_validate_and_apply(repo_dir: Path, plan: dict, config: dict) -> list[str]:
    unsafe = unsafe_plan_paths(plan)
    if unsafe:
        print(f"Reviewer gate rejected protected paths: {', '.join(unsafe)}. No changes applied.")
        return []

    files = plan.get("files", [])
    has_test_changes = any(
        isinstance(item, dict) and looks_like_test_path(str(item.get("path", "")))
        for item in files
    )
    if has_test_changes and not has_existing_test_infrastructure(repo_dir):
        print(
            "Reviewer gate rejected test changes because the target repository has no existing "
            "test infrastructure. No changes applied."
        )
        return []

    cleaned_plan = dict(plan)
    cleaned_files: list[dict] = []
    for item in files:
        if not isinstance(item, dict):
            print("Reviewer gate rejected an invalid plan item. No changes applied.")
            return []
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
