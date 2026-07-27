from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "verify_geonode_milestone.py"
sys.path.insert(0, str(ROOT / "scripts"))


def load_module():
    spec = importlib.util.spec_from_file_location("verify_geonode_milestone", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PersistenceVerificationTest(unittest.TestCase):
    @patch("subprocess.run")
    def test_restart_is_non_destructive_and_starts_the_stack(self, run: Mock) -> None:
        """Catches an accidental volume-removal flag in the persistence proof."""
        module = load_module()

        with patch.object(module, "start_stack") as start_stack:
            module.restart_without_volume_removal()

        commands = [call.args[0] for call in run.call_args_list]
        flattened = [part for command in commands for part in command]
        self.assertNotIn("-v", flattened)
        self.assertNotIn("--volumes", flattened)
        self.assertEqual(commands[0][-1], "down")
        start_stack.assert_called_once_with()

    def test_main_checks_seed_before_restart_health_and_after_restart(self) -> None:
        """Catches a persistence proof that restarts before establishing its baseline."""
        module = load_module()
        calls: list[str] = []

        with (
            patch.object(module, "verify_seed", side_effect=lambda: calls.append("seed")),
            patch.object(
                module,
                "restart_without_volume_removal",
                side_effect=lambda: calls.append("restart"),
            ),
            patch.object(
                module,
                "assert_core_services",
                side_effect=lambda _states: calls.append("health"),
            ),
            patch.object(module, "stack_status", return_value={}),
        ):
            module.main()

        self.assertEqual(calls, ["seed", "restart", "health", "seed"])

    def test_main_does_not_restart_when_baseline_seed_check_fails(self) -> None:
        """Catches recreating containers when the initial catalogue/WFS check already fails."""
        module = load_module()

        with (
            patch.object(module, "verify_seed", side_effect=RuntimeError("missing seed")),
            patch.object(module, "restart_without_volume_removal") as restart,
        ):
            with self.assertRaisesRegex(RuntimeError, "missing seed"):
                module.main()

        restart.assert_not_called()

    def test_main_does_not_postcheck_when_restart_fails(self) -> None:
        """Catches reporting a post-restart result after the stack failed to come back."""
        module = load_module()

        with (
            patch.object(module, "verify_seed") as verify_seed,
            patch.object(
                module,
                "restart_without_volume_removal",
                side_effect=RuntimeError("restart failed"),
            ),
            patch.object(module, "assert_core_services") as health,
        ):
            with self.assertRaisesRegex(RuntimeError, "restart failed"):
                module.main()

        self.assertEqual(verify_seed.call_count, 1)
        health.assert_not_called()


if __name__ == "__main__":
    unittest.main()
