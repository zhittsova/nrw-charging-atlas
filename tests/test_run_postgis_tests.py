from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_postgis_tests as runner  # noqa: E402


class DisposablePostgisTestRunnerTest(unittest.TestCase):
    def test_test_database_name_is_namespaced_and_validates_run_id(self) -> None:
        self.assertEqual(runner.test_database_name("a1b2"), "nrw_test_a1b2")
        self.assertEqual(runner.test_volume_name("a1b2"), "nrw-postgis-test-data-a1b2")
        with self.assertRaisesRegex(ValueError, "lowercase hexadecimal"):
            runner.test_database_name("project-db")

    def test_disposable_url_rejects_the_project_database(self) -> None:
        with self.assertRaisesRegex(ValueError, "Refusing non-disposable"):
            runner.require_disposable_database_url(
                "postgresql://localhost/nrw_gis", run_id="a1b2", endpoint="127.0.0.1:5432"
            )

    def test_disposable_url_requires_current_run_and_endpoint_context(self) -> None:
        url = "postgresql://u:p@127.0.0.1:5432/nrw_test_a1b2"
        for run_id, endpoint, message in (
            (None, "127.0.0.1:5432", "RUN_ID"),
            ("", "127.0.0.1:5432", "RUN_ID"),
            ("b3c4", "127.0.0.1:5432", "Refusing non-disposable"),
            ("a1b2", None, "endpoint"),
            ("a1b2", "127.0.0.1:9999", "endpoint"),
        ):
            with self.subTest(run_id=run_id, endpoint=endpoint), self.assertRaisesRegex(ValueError, message):
                runner.require_disposable_database_url(url, run_id=run_id, endpoint=endpoint)

    def test_disposable_url_rejects_shared_or_nonloopback_targets_before_sql(self) -> None:
        for url in (
            "postgresql://u:p@shared.example/nrw_test_a1b2",
            "postgresql://u:p@127.0.0.2:5432/nrw_test_a1b2",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "endpoint"):
                runner.require_disposable_database_url(
                    url, run_id="a1b2", endpoint="127.0.0.1:5432"
                )

    def test_disposable_url_requires_exact_current_database_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "Refusing non-disposable"):
            runner.require_disposable_database_url(
                "postgresql://u:p@127.0.0.1:5432/nrw_test_other",
                run_id="a1b2",
                endpoint="127.0.0.1:5432",
            )

    def test_psql_connection_keeps_the_password_out_of_command_arguments(self) -> None:
        command, environment = runner.psql_connection(
            "postgresql://test-user:temporary-password@127.0.0.1:5432/nrw_test_a1b2",
            run_id="a1b2",
            endpoint="127.0.0.1:5432",
        )

        self.assertNotIn("temporary-password", command)
        self.assertEqual(environment["PGPASSWORD"], "temporary-password")
        self.assertEqual(command[:3], ["psql", "--host", "127.0.0.1"])

    @patch.object(runner, "wait_for_database")
    @patch.object(runner, "create_database")
    @patch.object(runner, "start_disposable_postgis")
    @patch.object(runner, "require_command")
    @patch.object(runner, "run_command")
    @patch("subprocess.run")
    @patch("secrets.token_hex", return_value="a1b2c3d4")
    def test_runner_passes_only_a_unique_target_and_cleans_up(
        self,
        token_hex: Mock,
        pytest_run: Mock,
        docker_run: Mock,
        require_command: Mock,
        start: Mock,
        create_database: Mock,
        wait: Mock,
    ) -> None:
        database_url = "postgresql://postgres:temporary@127.0.0.1:5432/nrw_test_a1b2c3d4"
        start.return_value = (
            "nrw-postgis-test-a1b2c3d4",
            database_url,
            "nrw-postgis-test-data-a1b2c3d4",
        )
        pytest_run.return_value.returncode = 0
        docker_run.return_value.returncode = 0

        result = runner.run_postgis_tests(image="fixture-postgis")

        self.assertEqual(result, 0)
        require_command.assert_any_call("docker")
        require_command.assert_any_call("psql")
        start.assert_called_once_with(image="fixture-postgis", run_id="a1b2c3d4")
        wait.assert_called_once_with(database_url, run_id="a1b2c3d4", endpoint="127.0.0.1:5432")
        create_database.assert_called_once_with("nrw-postgis-test-a1b2c3d4", "nrw_test_a1b2c3d4")
        self.assertEqual(
            pytest_run.call_args.kwargs["env"]["SCENARIO_TEST_DATABASE_URL"], database_url
        )
        self.assertEqual(pytest_run.call_args.kwargs["env"]["SCENARIO_TEST_RUN_ID"], "a1b2c3d4")
        self.assertEqual(pytest_run.call_args.kwargs["env"]["SCENARIO_TEST_ENDPOINT"], "127.0.0.1:5432")
        self.assertEqual(
            docker_run.call_args_list,
            [
                call(["docker", "rm", "--force", "nrw-postgis-test-a1b2c3d4"]),
                call(["docker", "volume", "rm", "nrw-postgis-test-data-a1b2c3d4"]),
            ],
        )
        token_hex.assert_called_once_with(8)

    @patch.object(runner, "available_loopback_port", return_value=5432)
    @patch.object(runner, "require_command")
    @patch.object(runner, "run_command")
    @patch("secrets.token_hex", return_value="a1b2c3d4")
    def test_startup_failure_cleans_pre_registered_owned_resources(
        self, token_hex: Mock, docker_run: Mock, require_command: Mock, port: Mock
    ) -> None:
        docker_run.side_effect = [
            Mock(returncode=125, stderr="startup failed", stdout=""),
            Mock(returncode=1, stderr="No such container", stdout=""),
            Mock(returncode=1, stderr="No such volume", stdout=""),
        ]

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            runner.run_postgis_tests(image="fixture-postgis")

        self.assertEqual(
            docker_run.call_args_list[-2:],
            [
                call(["docker", "rm", "--force", "nrw-postgis-test-a1b2c3d4"]),
                call(["docker", "volume", "rm", "nrw-postgis-test-data-a1b2c3d4"]),
            ],
        )

    def test_cleanup_runs_after_readiness_database_pytest_and_interrupt_failures(self) -> None:
        database_url = "postgresql://postgres:temporary@127.0.0.1:5432/nrw_test_a1b2c3d4"
        phases = (
            ("readiness", "wait_for_database", RuntimeError("not ready")),
            ("database", "create_database", RuntimeError("create failed")),
            ("pytest", "subprocess.run", KeyboardInterrupt()),
            ("interrupt", "wait_for_database", KeyboardInterrupt()),
        )
        for phase, failure_target, failure in phases:
            with self.subTest(phase=phase), ExitStack() as stack:
                mocks = (
                    stack.enter_context(patch.object(runner, "require_command")),
                    stack.enter_context(
                        patch.object(
                            runner,
                            "start_disposable_postgis",
                            return_value=(
                                "nrw-postgis-test-a1b2c3d4",
                                database_url,
                                "nrw-postgis-test-data-a1b2c3d4",
                            ),
                        )
                    ),
                    stack.enter_context(patch.object(runner, "wait_for_database")),
                    stack.enter_context(patch.object(runner, "create_database")),
                    stack.enter_context(
                        patch.object(runner, "run_command", return_value=Mock(returncode=0))
                    ),
                    stack.enter_context(patch("subprocess.run", return_value=Mock(returncode=0))),
                    stack.enter_context(patch("secrets.token_hex", return_value="a1b2c3d4")),
                )
                target = {
                    "wait_for_database": mocks[2],
                    "create_database": mocks[3],
                    "subprocess.run": mocks[5],
                }[failure_target]
                target.side_effect = failure
                with self.assertRaises(type(failure)):
                    runner.run_postgis_tests(image="fixture-postgis")
                docker_run = mocks[4]
                self.assertEqual(
                    docker_run.call_args_list,
                    [
                        call(["docker", "rm", "--force", "nrw-postgis-test-a1b2c3d4"]),
                        call(["docker", "volume", "rm", "nrw-postgis-test-data-a1b2c3d4"]),
                    ],
                )

    @patch.object(runner, "start_disposable_postgis")
    @patch.object(runner, "require_command")
    @patch.object(runner, "run_command")
    @patch("subprocess.run")
    @patch("secrets.token_hex", return_value="a1b2c3d4")
    def test_cleanup_problems_do_not_hide_pytest_result(
        self, token_hex: Mock, pytest_run: Mock, docker_run: Mock, require_command: Mock, start: Mock
    ) -> None:
        database_url = "postgresql://postgres:temporary@127.0.0.1:5432/nrw_test_a1b2c3d4"
        start.return_value = (
            "nrw-postgis-test-a1b2c3d4", database_url, "nrw-postgis-test-data-a1b2c3d4"
        )
        pytest_run.return_value.returncode = 1
        docker_run.side_effect = [
            Mock(returncode=0, stderr="", stdout="container logs"),
            Mock(returncode=1, stderr="cleanup temporarily unavailable", stdout=""),
            Mock(returncode=1, stderr="No such volume", stdout=""),
        ]
        with patch.object(runner, "wait_for_database"), patch.object(runner, "create_database"):
            self.assertEqual(runner.run_postgis_tests(image="fixture-postgis"), 1)


if __name__ == "__main__":
    unittest.main()
