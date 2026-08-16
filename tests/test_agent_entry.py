from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import agent_entry


class AgentEntryPolicyTests(unittest.TestCase):
    def test_normalize_generated_text_removes_trailing_whitespace(self) -> None:
        source = "alpha   \n beta\t\n"
        self.assertEqual(agent_entry.normalize_generated_text(source), "alpha\n beta\n")

    def test_looks_like_test_path(self) -> None:
        self.assertTrue(agent_entry.looks_like_test_path("tests/test_api.py"))
        self.assertTrue(agent_entry.looks_like_test_path("test_main.py"))
        self.assertFalse(agent_entry.looks_like_test_path("app/main.py"))

    def test_detects_existing_test_infrastructure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent_entry.core.run(["git", "init"], cwd=repo)
            (repo / "tests").mkdir()
            (repo / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
            agent_entry.core.run(["git", "add", "tests/test_smoke.py"], cwd=repo)
            self.assertTrue(agent_entry.has_existing_test_infrastructure(repo))


if __name__ == "__main__":
    unittest.main()
