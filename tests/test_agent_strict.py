from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_strict


class StrictPolicyTests(unittest.TestCase):
    def test_extract_snapshot_paths(self) -> None:
        snapshot = "\n--- COMPLETE FILE: app/main.py ---\nprint('ok')\n--- END FILE: app/main.py ---\n"
        self.assertEqual(agent_strict.extract_snapshot_paths(snapshot), {"app/main.py"})

    def test_unseen_plan_path_is_detected(self) -> None:
        old_paths = set(agent_strict._LAST_SNAPSHOT_PATHS)
        try:
            agent_strict._LAST_SNAPSHOT_PATHS = {"app/main.py"}
            plan = {"files": [{"path": "docs/NEW.md", "content": "new\n"}]}
            self.assertEqual(agent_strict.unseen_plan_paths(plan), ["docs/NEW.md"])
        finally:
            agent_strict._LAST_SNAPSHOT_PATHS = old_paths

    def test_low_leverage_typing_title_is_rejected(self) -> None:
        plan = {
            "title": "Add proper request typing to exception handlers",
            "summary": "Clarifies the API contract and improves static analysis.",
            "files": [{"path": "app/main.py", "content": "value = 1\n"}],
        }
        reason = agent_strict.strict_quality_rejection_reason(plan)
        self.assertIsNotNone(reason)
        self.assertIn("low-leverage", reason or "")

    def test_high_leverage_transaction_fix_is_allowed(self) -> None:
        plan = {
            "title": "Make retryable writes idempotent",
            "summary": "Prevents duplicate database writes after transient failures and preserves consistency.",
            "files": [{"path": "app/service.py", "content": "value = 1\n"}],
        }
        self.assertIsNone(agent_strict.strict_quality_rejection_reason(plan))

    def test_single_pass_turns_weak_plan_into_noop(self) -> None:
        snapshot = (
            "\n--- COMPLETE FILE: app/main.py ---\n"
            "value = 1\n"
            "--- END FILE: app/main.py ---\n"
        )
        weak_plan = {
            "title": "Fix README typo",
            "summary": "Correct a spelling issue.",
            "files": [{"path": "app/main.py", "content": "value = 1\n"}],
        }
        with patch.object(agent_strict.base, "_ORIGINAL_GROQ_PLAN", return_value=weak_plan) as model_call:
            plan = agent_strict.strict_groq_plan("demo", snapshot, {})
        self.assertEqual(plan["files"], [])
        self.assertEqual(model_call.call_count, 1)

    def test_single_pass_rejects_unseen_file_without_retry(self) -> None:
        snapshot = (
            "\n--- COMPLETE FILE: app/main.py ---\n"
            "value = 1\n"
            "--- END FILE: app/main.py ---\n"
        )
        unseen_plan = {
            "title": "Make database retries idempotent",
            "summary": "Prevents duplicate writes during transient database failures.",
            "files": [{"path": "app/service.py", "content": "value = 1\n"}],
        }
        with patch.object(agent_strict.base, "_ORIGINAL_GROQ_PLAN", return_value=unseen_plan) as model_call:
            plan = agent_strict.strict_groq_plan("demo", snapshot, {})
        self.assertEqual(plan["files"], [])
        self.assertEqual(model_call.call_count, 1)

    def test_new_file_is_rejected_even_if_named_in_snapshot_state(self) -> None:
        old_paths = set(agent_strict._LAST_SNAPSHOT_PATHS)
        try:
            agent_strict._LAST_SNAPSHOT_PATHS = {"new.py"}
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                plan = {
                    "title": "Harden database transaction retries",
                    "summary": "Improves transaction reliability and consistency.",
                    "files": [{"path": "new.py", "content": "value = 1\n"}],
                }
                self.assertEqual(agent_strict.strict_validate_and_apply(repo, plan, {}), [])
                self.assertFalse((repo / "new.py").exists())
        finally:
            agent_strict._LAST_SNAPSHOT_PATHS = old_paths

    def test_new_unused_import_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "database.py"
            path.write_text(
                "from typing import Any\n\ndef get_db() -> Any:\n    return None\n",
                encoding="utf-8",
            )
            generated = (
                "from typing import NoReturn\n\n"
                "def get_db() -> None:\n"
                "    return None\n"
            )
            self.assertEqual(agent_strict.newly_unused_imports(path, generated), ["NoReturn"])

    def test_used_new_import_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.py"
            path.write_text("value = 1\n", encoding="utf-8")
            generated = "from pathlib import Path\n\nROOT = Path('.')\n"
            self.assertEqual(agent_strict.newly_unused_imports(path, generated), [])


if __name__ == "__main__":
    unittest.main()
