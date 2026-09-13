from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_full_stack_checks as checks  # noqa: E402


class FullStackCheckRunnerTest(unittest.TestCase):
    def preserved_state(self, root: Path, *, revision: str = "abc123") -> tuple[Path, Path, Path]:
        env_file = root / "geonode" / ".env"
        runtime_root = root / "data" / "runtime"
        revision_file = root / "revision"
        env_file.parent.mkdir()
        runtime_root.mkdir(parents=True)
        env_file.write_text("ADMIN_USERNAME=admin\nADMIN_PASSWORD=sentinel\n", encoding="utf-8")
        revision_file.write_text(f"{revision}\n", encoding="utf-8")
        return env_file, runtime_root, revision_file

    def test_not_opted_in_is_reported_as_skipped_not_success(self) -> None:
        with patch("builtins.print") as output:
            self.assertEqual(checks.run_full_stack_checks(enabled=False), 0)
        self.assertIn("SKIPPED", output.call_args.args[0])
        self.assertNotIn("PASSED", output.call_args.args[0])

    def test_opt_in_requires_the_running_stack_flag(self) -> None:
        with patch.dict("os.environ", {}, clear=True), patch("builtins.print") as output:
            self.assertEqual(checks.run_full_stack_checks(enabled=True), 2)
        self.assertIn("FAILED", output.call_args.args[0])

    @patch("subprocess.run")
    def test_enabled_run_explicitly_passes_owned_preserved_state(self, run: Mock) -> None:
        run.return_value = Mock(returncode=0)
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory)
            env_file, runtime_root, revision_file = self.preserved_state(state_root)
            with patch.dict("os.environ", {"GEONODE_INTEGRATION": "1"}, clear=True):
                self.assertEqual(
                    checks.run_full_stack_checks(
                        enabled=True,
                        geonode_env_file=env_file,
                        runtime_root=runtime_root,
                        deployment_revision_file=revision_file,
                        expected_revision="abc123",
                    ),
                    0,
                )
            self.assertEqual(env_file.read_text(encoding="utf-8"), "ADMIN_USERNAME=admin\nADMIN_PASSWORD=sentinel\n")
            self.assertTrue(runtime_root.is_dir())
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            [sys.executable, "-m", "pytest", "-q", checks.FOUNDATION_TEST],
            [sys.executable, checks.E2E_VERIFIER, "--runtime-root", str(runtime_root)],
        ])
        self.assertEqual(run.call_args_list[0].kwargs["env"]["GEONODE_ENV_FILE"], str(env_file))
        self.assertEqual(run.call_args_list[1].kwargs["env"]["GEONODE_ENV_FILE"], str(env_file))

    def test_enabled_run_rejects_missing_or_mismatched_preserved_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file, runtime_root, revision_file = self.preserved_state(Path(directory), revision="other")
            with patch.dict("os.environ", {"GEONODE_INTEGRATION": "1"}, clear=True), patch(
                "builtins.print"
            ) as output:
                self.assertEqual(
                    checks.run_full_stack_checks(
                        enabled=True,
                        geonode_env_file=env_file,
                        runtime_root=runtime_root,
                        deployment_revision_file=revision_file,
                        expected_revision="abc123",
                    ),
                    2,
                )
        self.assertIn("revision", output.call_args.args[0])

    @patch("subprocess.run")
    def test_failed_opted_in_command_is_not_reported_as_success(self, run: Mock) -> None:
        run.return_value = Mock(returncode=1)
        with tempfile.TemporaryDirectory() as directory:
            env_file, runtime_root, revision_file = self.preserved_state(Path(directory))
            with patch.dict("os.environ", {"GEONODE_INTEGRATION": "1"}, clear=True), patch(
                "builtins.print"
            ) as output:
                self.assertEqual(
                    checks.run_full_stack_checks(
                        enabled=True,
                        geonode_env_file=env_file,
                        runtime_root=runtime_root,
                        deployment_revision_file=revision_file,
                        expected_revision="abc123",
                    ),
                    1,
                )
        self.assertIn("FAILED", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
