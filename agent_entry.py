from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath

import agent as core


_ORIGINAL_GROQ_PLAN = core.groq_plan
_ORIGINAL_VALIDATE_AND_APPLY = core.validate_and_apply_plan


def normalize_generated_text(content: str) -> str:
    """Remove trailing spaces/tabs and normalize generated text to LF internally."""
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    had_final_newline = content.endswith("\n")
    normalized = "\n".join(line.rstrip(" \t") for line in content.splitlines())
    if had_final_newline:
        normalized += "\n"
    return normalized


def preserve_existing_newline_style(path: Path, content: str) -> str:
    """Keep CRLF for an existing CRLF file so a small edit does not rewrite every line."""
    if not path.exists():
        return content
    try:
        raw = path.read_bytes()
    except OSError:
        return content
    lf_count = raw.count(b"\n")
    crlf_count = raw.count(b"\r\n")
    if lf_count and crlf_count / lf_count >= 0.8:
        return content.replace("\r\n", "\n").replace("\n", "\r\n")
    return content


def complete_snapshot_repository(repo_dir: Path, config: dict) -> str:
    """Build a bounded snapshot without ever cutting a file in the middle."""
    tracked = core.run(["git", "ls-files"], cwd=repo_dir).splitlines()
    preferred = sorted(
        (p for p in tracked if core.is_safe_path(p)),
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

        block = f"\n--- COMPLETE FILE: {rel} ---\n{text}\n--- END FILE: {rel} ---\n"
        remaining = max_total_chars - total
        if len(block) > remaining:
            # Never send a partial file: a truncated tail can make valid code look broken.
            continue
        chunks.append(block)
        total += len(block)
        count += 1

    if not chunks:
        raise RuntimeError("No complete safe text files fit in the repository snapshot budget")
    return "".join(chunks)


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


def python_top_level_symbols(content: str) -> set[str]:
    tree = ast.parse(content)
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def replacement_rejection_reason(repo_dir: Path, rel: str, content: str) -> str | None:
    destination = repo_dir / rel
    if not destination.exists():
        return None
    try:
        old_content = destination.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "existing file cannot be safely decoded"

    old_normalized = old_content.replace("\r\n", "\n").replace("\r", "\n")
    new_normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    old_lines = old_normalized.splitlines()
    new_lines = new_normalized.splitlines()

    if len(old_lines) >= 40 and len(new_lines) < int(len(old_lines) * 0.80):
        return f"replacement shrinks a {len(old_lines)}-line file to {len(new_lines)} lines"

    if rel.endswith(".py"):
        try:
            old_symbols = python_top_level_symbols(old_normalized)
        except SyntaxError:
            old_symbols = set()
        try:
            new_symbols = python_top_level_symbols(new_normalized)
        except SyntaxError as exc:
            return f"generated Python is not syntactically valid: {exc.msg}"
        removed = sorted(old_symbols - new_symbols)
        if removed:
            return f"replacement removes existing top-level symbols: {', '.join(removed)}"

    return None


def _review_policy(extra: str = "") -> str:
    policy = """

--- MARSCHAL REVIEW POLICY ---
- Every file shown in the repository snapshot is COMPLETE and ends with an explicit END FILE marker. Never assume that a shown file is truncated.
- Never emit trailing whitespace.
- Never modify .gitignore, .gitattributes, editor configuration, hidden dotfiles, lock files, CI/workflow files, secrets, or environment files.
- Preserve all existing top-level functions and classes unless the task explicitly proves one is obsolete; for autonomous maintenance, prefer not to remove them at all.
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
        rel = str(cleaned.get("path", "")).strip()
        content = cleaned.get("content")
        if not isinstance(content, str):
            print(f"Reviewer gate rejected missing text content for {rel}. No changes applied.")
            return []

        normalized = normalize_generated_text(content)
        reason = replacement_rejection_reason(repo_dir, rel, normalized)
        if reason:
            print(f"Reviewer gate rejected {rel}: {reason}. No changes applied.")
            return []
        cleaned["content"] = preserve_existing_newline_style(repo_dir / rel, normalized)
        cleaned_files.append(cleaned)

    cleaned_plan["files"] = cleaned_files
    return _ORIGINAL_VALIDATE_AND_APPLY(repo_dir, cleaned_plan, config)


def main() -> int:
    core.snapshot_repository = complete_snapshot_repository
    core.groq_plan = policy_groq_plan
    core.validate_and_apply_plan = policy_validate_and_apply
    return core.main()


if __name__ == "__main__":
    raise SystemExit(main())
