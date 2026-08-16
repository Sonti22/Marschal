from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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

    def test_new_file_is_rejected_even_if_named_in_snapshot_state(self) -> None:
        old_paths = set(agent_strict._LAST_SNAPSHOT_PATHS)
        try:
            agent_strict._LAST_SNAPSHOT_PATHS = {"new.py"}
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                plan = {"files": [{"path": "new.py", "content": "value = 1\n"}]}
                self.assertEqual(agent_strict.strict_validate_and_apply(repo, plan, {}), [])
                self.assertFalse((repo / "new.py").exists())
        finally:
            agent_strict._LAST_SNAPSHOT_PATHS = old_paths


if __name__ == "__main__":
    unittest.main()
