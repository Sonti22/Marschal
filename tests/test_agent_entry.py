from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import agent_entry


class AgentEntryPolicyTests(unittest.TestCase):
    def test_normalize_generated_text_removes_trailing_whitespace(self) -> None:
        source = "alpha   \n beta\t\n"
        self.assertEqual(agent_entry.normalize_generated_text(source), "alpha\n beta\n")

    def test_preserve_existing_crlf_style(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.py"
            path.write_bytes(b"alpha\r\nbeta\r\n")
            result = agent_entry.preserve_existing_newline_style(path, "alpha\ngamma\n")
            self.assertEqual(result, "alpha\r\ngamma\r\n")

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

    def test_lead_quality_accepts_production_reliability_change(self) -> None:
        plan = {
            "title": "Make database retries idempotent",
            "summary": "Prevents duplicate writes during retryable database failures and improves consistency.",
            "files": [{"path": "app/service.py", "content": "value = 1\n"}],
        }
        self.assertIsNone(agent_entry.lead_quality_rejection_reason(plan))

    def test_lead_quality_rejects_cosmetic_change(self) -> None:
        plan = {
            "title": "Fix README typo",
            "summary": "Correct a spelling issue.",
            "files": [{"path": "README.md", "content": "text\n"}],
        }
        reason = agent_entry.lead_quality_rejection_reason(plan)
        self.assertIsNotNone(reason)

    def test_lead_quality_rejects_unsubstantiated_docs_only_change(self) -> None:
        plan = {
            "title": "Improve documentation",
            "summary": "Add more examples for developers.",
            "files": [{"path": "README.md", "content": "examples\n"}],
        }
        reason = agent_entry.lead_quality_rejection_reason(plan)
        self.assertIsNotNone(reason)

    def test_structural_index_exposes_cross_file_router_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent_entry.core.run(["git", "init"], cwd=repo)
            endpoint = repo / "app" / "api" / "v1" / "endpoints" / "organizations.py"
            endpoint.parent.mkdir(parents=True)
            endpoint.write_text(
                "from fastapi import APIRouter, Depends\n"
                "from app.core.security import verify_api_key\n\n"
                "router = APIRouter(dependencies=[Depends(verify_api_key)])\n",
                encoding="utf-8",
            )
            agent_entry.core.run(["git", "add", "app/api/v1/endpoints/organizations.py"], cwd=repo)
            tracked = agent_entry.core.run(["git", "ls-files"], cwd=repo).splitlines()
            index = agent_entry.build_structural_index(repo, tracked, 2000)
            self.assertIn("app/api/v1/endpoints/organizations.py", index)
            self.assertIn("verify_api_key", index)
            self.assertIn("APIRouter(dependencies=[Depends(verify_api_key)])", index)
            self.assertIn("DERIVED, READ-ONLY", index)

    def test_complete_snapshot_never_truncates_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent_entry.core.run(["git", "init"], cwd=repo)
            (repo / "README.md").write_text("x" * 500 + "\n", encoding="utf-8")
            (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
            agent_entry.core.run(["git", "add", "README.md", "app.py"], cwd=repo)
            snapshot = agent_entry.complete_snapshot_repository(
                repo,
                {
                    "snapshot_max_files": 10,
                    "snapshot_max_file_bytes": 1000,
                    "snapshot_max_total_chars": 160,
                    "snapshot_index_max_chars": 1000,
                },
            )
            self.assertNotIn("COMPLETE FILE: README.md", snapshot)
            self.assertIn("COMPLETE FILE: app.py", snapshot)
            self.assertIn("END FILE: app.py", snapshot)
            self.assertIn("value = 1", snapshot)

    def test_detects_existing_test_infrastructure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent_entry.core.run(["git", "init"], cwd=repo)
            (repo / "tests").mkdir()
            (repo / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
            agent_entry.core.run(["git", "add", "tests/test_smoke.py"], cwd=repo)
            self.assertTrue(agent_entry.has_existing_test_infrastructure(repo))

    def test_replacement_rejects_removed_python_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = repo / "app.py"
            path.write_text(
                "def keep():\n    return 1\n\ndef must_remain():\n    return 2\n",
                encoding="utf-8",
            )
            reason = agent_entry.replacement_rejection_reason(
                repo,
                "app.py",
                "def keep():\n    return 1\n",
            )
            self.assertIsNotNone(reason)
            self.assertIn("must_remain", reason or "")

    def test_reviewer_rejects_new_test_suite_without_infrastructure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent_entry.core.run(["git", "init"], cwd=repo)
            (repo / "README.md").write_text("demo\n", encoding="utf-8")
            agent_entry.core.run(["git", "add", "README.md"], cwd=repo)
            plan = {
                "title": "Validate API transaction behavior",
                "summary": "Adds coverage for transaction rollback correctness.",
                "files": [{"path": "tests/test_api.py", "content": "def test_api():\n    assert True\n"}],
            }
            self.assertEqual(agent_entry.policy_validate_and_apply(repo, plan, {}), [])
            self.assertFalse((repo / "tests" / "test_api.py").exists())

    def test_reviewer_rejects_protected_path_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent_entry.core.run(["git", "init"], cwd=repo)
            plan = {
                "title": "Harden database reliability",
                "summary": "Improves database retry reliability.",
                "files": [{"path": ".gitignore", "content": "*.db\n"}],
            }
            self.assertEqual(agent_entry.policy_validate_and_apply(repo, plan, {}), [])
            self.assertFalse((repo / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
