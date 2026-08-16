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
