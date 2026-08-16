import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent


class SafePathTests(unittest.TestCase):
    def test_accepts_normal_source_paths(self):
        self.assertTrue(agent.is_safe_path("src/app.py"))
        self.assertTrue(agent.is_safe_path("tests/test_app.py"))
        self.assertTrue(agent.is_safe_path("README.md"))

    def test_rejects_sensitive_and_escaping_paths(self):
        rejected = [
            "../secret.py",
            "/tmp/file.py",
            ".github/workflows/evil.yml",
            ".env",
            ".env.local",
            "package-lock.json",
            "src\\windows.py",
        ]
        for path in rejected:
            with self.subTest(path=path):
                self.assertFalse(agent.is_safe_path(path))


class TargetSelectionTests(unittest.TestCase):
    def test_explicit_configured_target_is_used(self):
        config = {"targets": ["one", "two"]}
        with patch.dict("os.environ", {"TARGET_REPO": "two"}, clear=False):
            self.assertEqual(agent.select_target(config), "two")

    def test_unknown_explicit_target_is_rejected(self):
        config = {"targets": ["one", "two"]}
        with patch.dict("os.environ", {"TARGET_REPO": "other"}, clear=False):
            with self.assertRaises(ValueError):
                agent.select_target(config)


class ApplyPlanTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "max_changed_files": 3,
            "max_output_file_bytes": 50000,
        }

    def test_applies_safe_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {"files": [{"path": "src/example.py", "content": "VALUE = 1\n"}]}
            changed = agent.validate_and_apply_plan(root, plan, self.config)
            self.assertEqual(changed, ["src/example.py"])
            self.assertEqual((root / "src/example.py").read_text(), "VALUE = 1\n")

    def test_rejects_protected_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {"files": [{"path": ".github/workflows/x.yml", "content": "x: y\n"}]}
            with self.assertRaises(RuntimeError):
                agent.validate_and_apply_plan(root, plan, self.config)

    def test_rejects_too_many_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {
                "files": [
                    {"path": "a.py", "content": "a=1\n"},
                    {"path": "b.py", "content": "b=1\n"},
                    {"path": "c.py", "content": "c=1\n"},
                    {"path": "d.py", "content": "d=1\n"},
                ]
            }
            with self.assertRaises(RuntimeError):
                agent.validate_and_apply_plan(root, plan, self.config)


if __name__ == "__main__":
    unittest.main()
