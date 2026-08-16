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

    def test_unsafe_plan_paths_rejects_dotfiles(self) -> None:
        plan = {
            "files": [
                {"path": ".gitignore", "content": "*.db\n"},
                {"path": "app/main.py", "content": "print('ok')\n"},
            ]
        }
        self.assertEqual(agent_entry.unsafe_plan_paths(plan), [".gitignore"])

    def test_detects_existing_test_infrastructure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent_entry.core.run(["git", "init"], cwd=repo)
            (repo / "tests").mkdir()
            (repo / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
            agent_entry.core.run(["git", "add", "tests/test_smoke.py"], cwd=repo)
            self.assertTrue(agent_entry.has_existing_test_infrastructure(repo))

    def test_reviewer_rejects_new_test_suite_without_infrastructure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent_entry.core.run(["git", "init"], cwd=repo)
            (repo / "README.md").write_text("demo\n", encoding="utf-8")
            agent_entry.core.run(["git", "add", "README.md"], cwd=repo)
            plan = {"files": [{"path": "tests/test_api.py", "content": "def test_api():\n    assert True\n"}]}
            self.assertEqual(agent_entry.policy_validate_and_apply(repo, plan, {}), [])
            self.assertFalse((repo / "tests" / "test_api.py").exists())

    def test_reviewer_rejects_protected_path_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent_entry.core.run(["git", "init"], cwd=repo)
            plan = {"files": [{"path": ".gitignore", "content": "*.db\n"}]}
            self.assertEqual(agent_entry.policy_validate_and_apply(repo, plan, {}), [])
            self.assertFalse((repo / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
