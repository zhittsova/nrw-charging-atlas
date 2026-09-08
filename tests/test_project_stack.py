from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
import project_stack  # noqa: E402


class ProjectStackTest(unittest.TestCase):
    @patch("project_stack.subprocess.run")
    @patch("project_stack.compose_command")
    def test_tool_runner_uses_ephemeral_profile_without_shell(self, compose, run) -> None:
        compose.return_value = ["docker", "compose", "run"]

        project_stack.run_etl("scripts/example.py", "--flag")

        compose.assert_called_once_with(
            "--profile",
            "tools",
            "run",
            "--rm",
            "nrw-etl",
            "python",
            "scripts/example.py",
            "--flag",
        )
        run.assert_called_once_with(["docker", "compose", "run"], check=True)

    @patch("project_stack.run_etl")
    def test_seed_rebuilds_all_data_domains_then_reapplies_grants(self, run_etl) -> None:
        project_stack.seed_database(population_snapshot=None)

        self.assertEqual(
            run_etl.call_args_list,
            [
                call("scripts/initialize_nrw_database.py"),
                call("scripts/load_nrw_postgis.py"),
                call("scripts/load_nrw_population_postgis.py"),
                call("scripts/load_nrw_infrastructure_postgis.py"),
                call("scripts/load_nrw_grid_postgis.py"),
                call("scripts/load_nrw_road_network_postgis.py"),
                call("scripts/load_nrw_energy_balance_postgis.py"),
                call("scripts/initialize_nrw_database.py", "--grant-only"),
            ],
        )

    @patch("project_stack.run_etl")
    def test_seed_can_use_offline_population_snapshot(self, run_etl) -> None:
        snapshot = Path(__file__)

        project_stack.seed_database(population_snapshot=snapshot)

        self.assertIn(
            call(
                "scripts/load_nrw_population_postgis.py",
                "--snapshot",
                "/app/data/raw/eurostat_population_nrw.json",
            ),
            run_etl.call_args_list,
        )

    @patch("project_stack.run_etl")
    def test_fetch_downloads_public_sources_and_large_grid(self, run_etl) -> None:
        project_stack.fetch_data()

        self.assertEqual(
            run_etl.call_args_list,
            [
                call("scripts/fetch_real_data.py"),
                call("scripts/fetch_nrw_infrastructure.py", "--allow-large"),
            ],
        )

    @patch("project_stack.subprocess.run")
    @patch("project_stack.geonode_stack.project_database_name", return_value="nrw_test")
    def test_publish_uses_module_invocation_and_configured_database(self, database_name, run) -> None:
        project_stack.provision_layers()

        database_name.assert_called_once_with()
        run.assert_called_once_with(
            [
                sys.executable,
                "-m",
                "scripts.provision_geoserver_layers",
                "--database-name",
                "nrw_test",
                "--sync-geonode",
            ],
            check=True,
        )

    @patch("project_stack.provision_layers")
    @patch("project_stack.verify_project")
    @patch("project_stack.seed_database")
    @patch("project_stack.fetch_data")
    @patch("project_stack.geonode_stack.start_stack")
    @patch("project_stack.geonode_stack.provision_upstream_checkout")
    def test_bootstrap_orders_stack_download_seed_and_publication(
        self,
        provision_checkout,
        start_stack,
        fetch_data,
        seed_database,
        verify_project,
        provision_layers,
    ) -> None:
        manager = unittest.mock.Mock()
        manager.attach_mock(provision_checkout, "checkout")
        manager.attach_mock(start_stack, "start")
        manager.attach_mock(fetch_data, "fetch")
        manager.attach_mock(seed_database, "seed")
        manager.attach_mock(provision_layers, "publish")
        manager.attach_mock(verify_project, "verify")

        project_stack.bootstrap()

        self.assertEqual(
            manager.mock_calls,
            [call.checkout(), call.start(), call.fetch(), call.seed(), call.publish(), call.verify()],
        )


if __name__ == "__main__":
    unittest.main()
