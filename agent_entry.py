from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath

import agent as core


_ORIGINAL_GROQ_PLAN = core.groq_plan
_ORIGINAL_VALIDATE_AND_APPLY = core.validate_and_apply_plan

LEAD_IMPACT_TERMS = {
    "architecture",
    "async",
    "atomic",
    "auth",
    "backpressure",
    "cache",
    "concurr",
    "connection",
    "consisten",
    "contract",
    "correctness",
    "database",
    "deadlock",
    "failure",
    "idempot",
    "index",
    "latency",
    "lock",
    "logging",
    "memory",
    "metric",
    "migration",
    "observability",
    "pagination",
    "performance",
    "query",
    "queue",
    "race",
    "reliab",
    "resilien",
    "resource",
    "retry",
    "security",
    "serialization",
    "session",
    "timeout",
    "transaction",
    "validation",
}
TRIVIAL_TERMS = {
    "comment",
    "format",
    "readme typo",
    "rename variable",
    "spelling",
    "style only",
    "typo",
}
ARCHITECTURE_SIGNAL_TOKENS = (
    "APIRouter(",
    "include_router(",
    "Depends(",
    "create_async_engine(",
    "async_sessionmaker(",
    "BaseSettings",
    "Redis(",
    "Kafka",
    "Celery(",
    "AsyncClient(",
    "ClientSession(",
    "lifespan",
    "middleware",
)


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


def snapshot_priority(raw_path: str) -> tuple[int, int, str]:
    """Prefer architecture-relevant source files over prose when the context budget is tight."""
    path = PurePosixPath(raw_path)
    name = path.name.lower()
    parts = {part.lower() for part in path.parts}

    if name in {"pyproject.toml", "go.mod", "package.json"}:
        rank = 0
    elif name in {
        "main.py",
        "config.py",
        "database.py",
        "security.py",
        "router.py",
        "service.py",
        "services.py",
        "repository.py",
        "repositories.py",
        "models.py",
        "schemas.py",
    }:
        rank = 1
    elif path.suffix.lower() in {".py", ".go", ".ts", ".js"} and parts.intersection(
        {"app", "src", "api", "core", "domain", "services", "service", "repositories", "repository"}
    ):
        rank = 2
    elif "tests" in parts or name.startswith("test_") or name.endswith("_test.py"):
        rank = 3
    elif name in {"docker-compose.yml", "docker-compose.yaml", "dockerfile"}:
        rank = 4
    elif name == "readme.md" or path.suffix.lower() == ".md":
        rank = 6
    else:
        rank = 5
    return rank, len(path.parts), raw_path


def _python_structure_summary(content: str) -> str | None:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None

    imports: list[str] = []
    symbols: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)

    local_imports = [
        name
        for name in imports
        if name.startswith(("app", "src", "core", "api", "domain", "services", "repositories"))
    ]
    if not local_imports:
        local_imports = imports[:6]
    else:
        local_imports = local_imports[:8]

    signals: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or len(signals) >= 4:
            continue
        if any(token in line for token in ARCHITECTURE_SIGNAL_TOKENS):
            signals.append(line[:220])

    parts: list[str] = []
    if local_imports:
        parts.append("imports=" + ",".join(local_imports))
    if symbols:
        parts.append("symbols=" + ",".join(symbols[:12]))
    if signals:
        parts.append("signals=" + " || ".join(signals))
    return "; ".join(parts) if parts else None


def build_structural_index(repo_dir: Path, tracked: list[str], max_chars: int) -> str:
    """Build a compact read-only architecture map across Python files."""
    if max_chars <= 0:
        return ""

    lines = [
        "\n--- REPOSITORY STRUCTURAL INDEX (DERIVED, READ-ONLY) ---",
        "Use this index for cross-file reasoning. A path listed only here is NOT editable unless it also appears as a COMPLETE FILE below.",
    ]
    total = sum(len(line) + 1 for line in lines)

    candidates = sorted(
        (p for p in tracked if p.endswith(".py") and core.is_safe_path(p)),
        key=snapshot_priority,
    )
    for rel in candidates:
        path = repo_dir / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > 100_000 or b"\x00" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        summary = _python_structure_summary(text)
        if not summary:
            continue
        line = f"- {rel} | {summary}"
        if total + len(line) + 1 > max_chars:
            continue
        lines.append(line)
        total += len(line) + 1

    lines.append("--- END STRUCTURAL INDEX ---\n")
    return "\n".join(lines)


def complete_snapshot_repository(repo_dir: Path, config: dict) -> str:
    """Build architecture index plus bounded complete-file context without truncation."""
    tracked = core.run(["git", "ls-files"], cwd=repo_dir).splitlines()
    preferred = sorted(
        (p for p in tracked if core.is_safe_path(p)),
        key=snapshot_priority,
    )

    index = build_structural_index(
        repo_dir,
        tracked,
        int(config.get("snapshot_index_max_chars", 0)),
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
    return index + "".join(chunks)


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


def lead_quality_rejection_reason(plan: dict) -> str | None:
    files = [item for item in plan.get("files", []) if isinstance(item, dict)]
    if not files:
        return None

    title = str(plan.get("title", ""))
    summary = str(plan.get("summary", ""))
    text = f"{title} {summary}".lower()
    has_impact = any(term in text for term in LEAD_IMPACT_TERMS)
    has_trivial_signal = any(term in text for term in TRIVIAL_TERMS)

    suffixes = {PurePosixPath(str(item.get("path", ""))).suffix.lower() for item in files}
    docs_only = bool(suffixes) and suffixes.issubset({".md", ".txt"})

    if has_trivial_signal and not has_impact:
        return "proposal is cosmetic or maintenance-only without production impact"
    if docs_only and not has_impact:
        return "documentation-only proposal does not demonstrate an architecture or production concern"
    if not has_impact:
        return "summary does not identify a concrete production, architecture, reliability, security, data, or performance impact"
    return None


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

--- MARSCHAL PYTHON TECH LEAD / SOFTWARE ARCHITECT POLICY ---
ROLE AND QUALITY BAR
- Operate as a Python Tech Lead / Software Architect focused on Backend, Distributed Systems and production AI systems.
- Select ONE bounded, reviewable change with the highest engineering leverage visible in the snapshot. Bounded does not mean trivial.
- Prefer evidence-backed improvements to transaction boundaries, idempotency, async/concurrency correctness, API contracts, validation, data consistency, database access, failure handling, retries/timeouts, resource lifecycle, security, observability, performance, backpressure, caching, or service boundaries.
- For AI-related repositories, prefer production concerns such as model/provider abstraction, timeouts, retries, structured outputs, rate limits, evaluation hooks, observability, safe fallbacks and deterministic tests. Do not add AI merely to make a project look advanced.
- Think about failure modes, ownership boundaries, invariants and operational consequences before proposing code.
- Use the REPOSITORY STRUCTURAL INDEX to detect cross-file relationships and existing implementations. Before adding a global/cross-cutting mechanism, verify it is not already enforced in lower-level routers/services/dependencies. Do not duplicate architecture that already exists.
- The structural index is derived read-only context. Never edit a file based only on the index; a file is editable only if it also has a COMPLETE FILE marker.
- The PR title and summary must state the concrete production/architecture impact and be grounded in code visible in the snapshot.
- Reject cosmetic churn, typo-only edits, comment-only edits, style-only refactors, arbitrary renaming, fake complexity, resume-padding architecture, speculative microservices, or technology additions without evidence.
- A small fix is acceptable only when it addresses a real senior-level concern (for example a transaction bug, race, resource leak, unsafe retry, broken validation or contract violation).
- If the snapshot is insufficient to justify a Tech-Lead-level change, return an empty files array instead of inventing context.

SAFETY AND REVIEW
- Every file shown with a COMPLETE FILE marker is complete and ends with an explicit END FILE marker. Never assume that such a file is truncated.
- Never emit trailing whitespace.
- Never modify .gitignore, .gitattributes, editor configuration, hidden dotfiles, lock files, CI/workflow files, secrets, or environment files.
- Preserve all existing top-level functions and classes unless the task explicitly proves one is obsolete; for autonomous maintenance, prefer not to remove them at all.
- Do not introduce a new test suite into a repository that has no visible test infrastructure.
- Do not rely on undeclared third-party test/runtime dependencies.
- If a safe improvement would require adding dependencies or bootstrapping CI/test tooling, choose a different high-value improvement or return an empty files array.
- Prefer changes that fit existing abstractions and conventions instead of broad rewrites.
- Every returned path must satisfy the repository safety policy; if unsure, return an empty files array.
--- END POLICY ---
"""
    if extra:
        policy += f"\n{extra}\n"
    return policy


def policy_groq_plan(repo_name: str, snapshot: str, config: dict) -> dict:
    plan = _ORIGINAL_GROQ_PLAN(repo_name, snapshot + _review_policy(), config)
    unsafe = unsafe_plan_paths(plan)
    if unsafe:
        paths = ", ".join(unsafe)
        print(f"Reviewer preflight rejected protected paths: {paths}. Requesting one safer alternative.")
        feedback = (
            "The previous candidate was rejected because it used protected or unsupported paths: "
            f"{paths}. Produce a DIFFERENT bounded high-leverage improvement using only ordinary safe source files. "
            "Do not return any dotfile. If no such Tech-Lead-level change is justified, return an empty files array."
        )
        plan = _ORIGINAL_GROQ_PLAN(repo_name, snapshot + _review_policy(feedback), config)

    quality_reason = lead_quality_rejection_reason(plan)
    if quality_reason:
        print(f"Tech Lead quality gate rejected proposal: {quality_reason}. Requesting one stronger alternative.")
        feedback = (
            f"The previous proposal was rejected because {quality_reason}. Select a DIFFERENT change with concrete "
            "production impact in backend architecture, distributed-systems correctness, reliability, security, "
            "data consistency, observability or performance. Ground every claim in visible code and cross-check the "
            "structural index for existing implementations. If none exists, return an empty files array."
        )
        plan = _ORIGINAL_GROQ_PLAN(repo_name, snapshot + _review_policy(feedback), config)
    return plan


def policy_validate_and_apply(repo_dir: Path, plan: dict, config: dict) -> list[str]:
    unsafe = unsafe_plan_paths(plan)
    if unsafe:
        print(f"Reviewer gate rejected protected paths: {', '.join(unsafe)}. No changes applied.")
        return []

    quality_reason = lead_quality_rejection_reason(plan)
    if quality_reason:
        print(f"Tech Lead quality gate rejected proposal: {quality_reason}. No changes applied.")
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
