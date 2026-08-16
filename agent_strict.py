from __future__ import annotations

import ast
from pathlib import Path

import agent as core
import agent_entry as base


_LAST_SNAPSHOT_PATHS: set[str] = set()
STRICT_TRIVIAL_TITLE_TERMS = (
    "typing",
    "type hint",
    "readme",
    "comment",
    "format",
    "rename",
    "spelling",
    "typo",
    "cleanup imports",
)


def extract_snapshot_paths(snapshot: str) -> set[str]:
    paths: set[str] = set()
    prefix = "--- COMPLETE FILE: "
    suffix = " ---"
    for line in snapshot.splitlines():
        if line.startswith(prefix) and line.endswith(suffix):
            paths.add(line[len(prefix) : -len(suffix)])
    return paths


def unseen_plan_paths(plan: dict) -> list[str]:
    unseen: list[str] = []
    for item in plan.get("files", []):
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path", "")).strip()
        if rel and rel not in _LAST_SNAPSHOT_PATHS:
            unseen.append(rel)
    return unseen


def strict_quality_rejection_reason(plan: dict) -> str | None:
    if not plan.get("files"):
        return None
    title = str(plan.get("title", "")).lower()
    for term in STRICT_TRIVIAL_TITLE_TERMS:
        if term in title:
            return f"title signals a low-leverage maintenance task: {term}"
    return base.lead_quality_rejection_reason(plan)


def no_change_plan(reason: str) -> dict:
    return {
        "title": "No justified Tech Lead change",
        "summary": reason,
        "files": [],
    }


def imported_bindings(content: str) -> set[str]:
    tree = ast.parse(content)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return names


def referenced_names(content: str) -> set[str]:
    tree = ast.parse(content)
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def newly_unused_imports(path: Path, generated_content: str) -> list[str]:
    if path.suffix != ".py" or not path.is_file():
        return []
    try:
        old_content = path.read_text(encoding="utf-8")
        old_imports = imported_bindings(old_content)
        new_imports = imported_bindings(generated_content)
        used = referenced_names(generated_content)
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []
    return sorted(name for name in (new_imports - old_imports) if name not in used)


def strict_groq_plan(repo_name: str, snapshot: str, config: dict) -> dict:
    """Make exactly one model call; reject weak or ungrounded output instead of forcing a PR."""
    global _LAST_SNAPSHOT_PATHS
    _LAST_SNAPSHOT_PATHS = extract_snapshot_paths(snapshot)

    plan = base._ORIGINAL_GROQ_PLAN(
        repo_name,
        snapshot + base._review_policy(
            "SINGLE-PASS RULE: this is your only planning attempt. Choose a grounded Tech-Lead-level change now, "
            "or return an empty files array. Do not propose a placeholder task expecting a retry."
        ),
        config,
    )

    unsafe = base.unsafe_plan_paths(plan)
    if unsafe:
        reason = f"proposal uses protected paths: {', '.join(unsafe)}"
        print(f"Strict Tech Lead gate rejected proposal: {reason}. No change will be made.")
        return no_change_plan(reason)

    unseen = unseen_plan_paths(plan)
    if unseen:
        reason = f"proposal uses paths not present as complete snapshot files: {', '.join(unseen)}"
        print(f"Strict Tech Lead gate rejected proposal: {reason}. No change will be made.")
        return no_change_plan(reason)

    quality_reason = strict_quality_rejection_reason(plan)
    if quality_reason:
        print(f"Strict Tech Lead gate rejected proposal: {quality_reason}. No change will be made.")
        return no_change_plan(quality_reason)

    return plan


def strict_validate_and_apply(repo_dir: Path, plan: dict, config: dict) -> list[str]:
    unseen = unseen_plan_paths(plan)
    if unseen:
        print(
            "Strict reviewer rejected paths not fully observed in the snapshot: "
            f"{', '.join(unseen)}. No changes applied."
        )
        return []

    quality_reason = strict_quality_rejection_reason(plan)
    if quality_reason:
        print(f"Tech Lead strict gate rejected proposal: {quality_reason}. No changes applied.")
        return []

    for item in plan.get("files", []):
        if not isinstance(item, dict):
            print("Strict reviewer rejected an invalid plan item. No changes applied.")
            return []
        rel = str(item.get("path", "")).strip()
        path = repo_dir / rel
        if not path.is_file():
            print(f"Strict reviewer rejected new file {rel}: autonomous file creation is disabled.")
            return []
        content = item.get("content")
        if isinstance(content, str):
            unused = newly_unused_imports(path, content)
            if unused:
                print(
                    f"Strict reviewer rejected {rel}: newly introduced unused imports: "
                    f"{', '.join(unused)}. No changes applied."
                )
                return []

    # Git's default whitespace checker treats the CR byte in newly added CRLF
    # lines as trailing whitespace on Linux. Mark CR-at-EOL as intentional so
    # real trailing spaces are still caught without rewriting CRLF repositories.
    core.run(["git", "config", "core.whitespace", "cr-at-eol"], cwd=repo_dir)
    return base.policy_validate_and_apply(repo_dir, plan, config)


def main() -> int:
    core.snapshot_repository = base.complete_snapshot_repository
    core.groq_plan = strict_groq_plan
    core.validate_and_apply_plan = strict_validate_and_apply
    return core.main()


if __name__ == "__main__":
    raise SystemExit(main())
