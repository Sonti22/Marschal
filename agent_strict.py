from __future__ import annotations

import ast
from pathlib import Path

import agent as core
import agent_entry as base


_LAST_SNAPSHOT_PATHS: set[str] = set()


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
    global _LAST_SNAPSHOT_PATHS
    _LAST_SNAPSHOT_PATHS = extract_snapshot_paths(snapshot)

    plan = base.policy_groq_plan(repo_name, snapshot, config)
    unseen = unseen_plan_paths(plan)
    if not unseen:
        return plan

    print(
        "Strict reviewer rejected paths not present as complete snapshot files: "
        f"{', '.join(unseen)}. Requesting one grounded alternative."
    )
    feedback = (
        "STRICT GROUNDING RULE: modify only an EXISTING path named in a COMPLETE FILE marker above. "
        "Do not create any new file. The previous candidate used unavailable paths: "
        f"{', '.join(unseen)}. Choose a DIFFERENT bounded high-leverage change grounded only in visible "
        "complete files and meeting the Python Tech Lead / Software Architect quality bar, or return an empty files array."
    )
    return base._ORIGINAL_GROQ_PLAN(
        repo_name,
        snapshot + base._review_policy(feedback),
        config,
    )


def strict_validate_and_apply(repo_dir: Path, plan: dict, config: dict) -> list[str]:
    unseen = unseen_plan_paths(plan)
    if unseen:
        print(
            "Strict reviewer rejected paths not fully observed in the snapshot: "
            f"{', '.join(unseen)}. No changes applied."
        )
        return []

    quality_reason = base.lead_quality_rejection_reason(plan)
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
