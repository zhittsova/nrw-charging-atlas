from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "geonode_stack.py"


def load_module():
    spec = importlib.util.spec_from_file_location("geonode_stack", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GeoNodeEnvironmentTest(unittest.TestCase):
    def test_patch_env_preserves_secrets_and_sets_local_urls(self) -> None:
        module = load_module()
        source = "\n".join(
            [
                "POSTGRES_PASSWORD=keep-me",
                "ADMIN_PASSWORD=also-keep-me",
                "SITEURL=http://localhost/",
                "NGINX_BASE_URL=http://localhost",
                "HTTP_HOST=localhost",
                "HTTP_PORT=80",
                "HTTPS_HOST=localhost",
                "HTTPS_PORT=443",
                "GEOSERVER_WEB_UI_LOCATION=http://localhost/geoserver/",
                "GEOSERVER_PUBLIC_LOCATION=http://localhost/geoserver/",
                "GEOSERVER_JAVA_OPTS=-Xms4G -Xmx4G -Dcustom.geoserver.flag=keep-me",
                'ALLOWED_HOSTS="[\'django\', \'localhost\']"',
            ]
        )

        patched = module.patch_env_text(source)

        self.assertIn("POSTGRES_PASSWORD=keep-me", patched)
        self.assertIn("ADMIN_PASSWORD=also-keep-me", patched)
        self.assertIn("SITEURL=http://localhost:8000/", patched)
        self.assertIn("HTTP_PORT=8000", patched)
        self.assertIn("HTTPS_HOST=", patched)
        self.assertIn("HTTPS_PORT=8443", patched)
        self.assertIn("MEMCACHED_OPTIONS=", patched)
        self.assertIn("MEMCACHED_LOCATION=memcached:11211", patched)
        self.assertIn(
            "GEOSERVER_JAVA_OPTS=-Xms512m -Xmx1G -Dcustom.geoserver.flag=keep-me",
            patched,
        )
        self.assertIn(
            "GEOSERVER_PUBLIC_LOCATION=http://localhost:8080/geoserver/",
            patched,
        )
        self.assertIn("127.0.0.1", patched)

    def test_unresolved_template_values_are_rejected(self) -> None:
        module = load_module()

        with self.assertRaisesRegex(ValueError, "unresolved placeholders"):
            module.assert_resolved_env("POSTGRES_PASSWORD={pgpwd}\n")

    def test_compose_command_uses_upstream_project_directory(self) -> None:
        module = load_module()

        command = module.compose_command("config", "--quiet")

        self.assertEqual(command[:2], ["docker", "compose"])
        self.assertIn("--project-directory", command)
        self.assertIn(str(ROOT / "geonode"), command)
        self.assertIn(str(ROOT / "geonode" / ".env"), command)
        self.assertEqual(
            command[command.index("-f") + 1 : command.index("-f") + 4],
            [
                str(ROOT / "geonode" / "docker-compose.yml"),
                "-f",
                str(ROOT / "config" / "geonode" / "docker-compose.apple-silicon.yml"),
            ],
        )
        self.assertEqual(command[-2:], ["config", "--quiet"])

    def test_upstream_pin_matches_checked_out_geonode(self) -> None:
        module = load_module()

        module.validate_upstream_checkout()

    def test_upstream_pin_is_official_geonode_5_1_0_release(self) -> None:
        upstream = json.loads((ROOT / "config" / "geonode-upstream.json").read_text())

        self.assertEqual(upstream["repository"], "https://github.com/GeoNode/geonode")
        self.assertEqual(
            upstream["commit"], "614b85f10b5d156f8b04882df59f03c89360adf5"
        )

    def test_local_override_makes_letsencrypt_opt_in(self) -> None:
        override = (
            ROOT / "config" / "geonode" / "docker-compose.apple-silicon.yml"
        ).read_text()

        self.assertIn("letsencrypt:\n    profiles:\n      - tls", override)

    def test_init_first_run_prints_only_ready_message(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            geonode_dir = Path(temporary_directory) / "geonode"
            geonode_dir.mkdir()
            (geonode_dir / "create-envfile.py").touch()
            compose_path = geonode_dir / "docker-compose.yml"
            compose_path.touch()
            env_path = geonode_dir / ".env"

            def run(command, **kwargs):
                if command[0] == sys.executable:
                    if not (
                        kwargs.get("capture_output") or kwargs.get("stdout") is not None
                    ):
                        print("INFO - .env file created: .env")
                    env_path.write_text("POSTGRES_PASSWORD=keep-me\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)

            output = io.StringIO()
            with (
                patch.object(module, "GEONODE_DIR", geonode_dir),
                patch.object(module, "ENV_PATH", env_path),
                patch.object(module, "COMPOSE_PATH", compose_path),
                patch.object(module, "validate_upstream_checkout"),
                patch.object(module.subprocess, "run", side_effect=run),
                patch.object(sys, "argv", ["geonode_stack.py", "init"]),
                contextlib.redirect_stdout(output),
            ):
                module.main()

            self.assertEqual(
                output.getvalue(),
                "GeoNode local environment is ready at geonode/.env\n",
            )

    def test_init_replays_generator_diagnostics_to_stderr_on_failure(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            geonode_dir = Path(temporary_directory) / "geonode"
            geonode_dir.mkdir()
            (geonode_dir / "create-envfile.py").touch()
            compose_path = geonode_dir / "docker-compose.yml"
            compose_path.touch()
            env_path = geonode_dir / ".env"

            def run(command, **kwargs):
                if command[0] == sys.executable:
                    raise subprocess.CalledProcessError(
                        1,
                        command,
                        output="INFO - generating environment\n",
                        stderr="ERROR - database configuration is invalid\n",
                    )
                return subprocess.CompletedProcess(command, 0)

            output = io.StringIO()
            errors = io.StringIO()
            with (
                patch.object(module, "GEONODE_DIR", geonode_dir),
                patch.object(module, "ENV_PATH", env_path),
                patch.object(module, "COMPOSE_PATH", compose_path),
                patch.object(module, "validate_upstream_checkout"),
                patch.object(module.subprocess, "run", side_effect=run),
                patch.object(sys, "argv", ["geonode_stack.py", "init"]),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    module.main()

            self.assertEqual(output.getvalue(), "")
            self.assertIn("INFO - generating environment", errors.getvalue())
            self.assertIn("ERROR - database configuration is invalid", errors.getvalue())


class GeoNodeLifecycleTest(unittest.TestCase):
    def test_required_services_match_geonode_5_1_compose(self) -> None:
        module = load_module()

        self.assertEqual(
            module.CORE_SERVICES,
            {
                "django",
                "celery",
                "memcached",
                "geonode",
                "geoserver",
                "data-dir-conf",
                "db",
                "redis",
            },
        )

    @patch("subprocess.run")
    def test_docker_readiness_uses_docker_info(self, run: Mock) -> None:
        module = load_module()
        run.return_value.returncode = 0

        self.assertTrue(module.docker_is_ready())
        run.assert_called_once_with(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    @patch("subprocess.run")
    def test_stop_never_removes_volumes(self, run: Mock) -> None:
        module = load_module()

        module.stop_stack()

        command = run.call_args.args[0]
        self.assertEqual(command[-1], "stop")
        self.assertNotIn("-v", command)
        self.assertNotIn("--volumes", command)

    def test_required_services_are_reported_unhealthy(self) -> None:
        module = load_module()
        states = {
            "db": "healthy",
            "redis": "healthy",
            "django": "running",
            "celery": "running",
            "memcached": "running",
            "geonode": "running",
        }

        with self.assertRaisesRegex(RuntimeError, "data-dir-conf.*geoserver"):
            module.assert_core_services(states)

    def test_exited_data_dir_configuration_is_reported_unhealthy(self) -> None:
        module = load_module()
        states = {
            "django": "running",
            "celery": "running",
            "memcached": "running",
            "geonode": "running",
            "letsencrypt": "running",
            "geoserver": "healthy",
            "data-dir-conf": "exited",
            "db": "healthy",
            "redis": "healthy",
        }

        with self.assertRaisesRegex(RuntimeError, "data-dir-conf"):
            module.assert_core_services(states)

    def test_wait_for_core_services_retries_starting_service_until_healthy(self) -> None:
        module = load_module()
        starting = {
            "django": "healthy",
            "celery": "running",
            "memcached": "healthy",
            "geonode": "running",
            "geoserver": "starting",
            "data-dir-conf": "healthy",
            "db": "healthy",
            "redis": "healthy",
        }
        healthy = {**starting, "geoserver": "healthy"}

        with (
            patch.object(module, "stack_status", side_effect=[starting, healthy]) as status,
            patch.object(module.time, "sleep") as sleep,
        ):
            module.wait_for_core_services(timeout=1)

        self.assertEqual(status.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_wait_for_core_services_times_out_with_last_health_error(self) -> None:
        module = load_module()
        states = {"db": "healthy"}

        with (
            patch.object(module, "stack_status", return_value=states),
            patch.object(module.time, "monotonic", side_effect=[0, 0, 1]),
            patch.object(module.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "Timed out waiting for GeoNode core services:.*geoserver"
            ):
                module.wait_for_core_services(timeout=1)

        sleep.assert_called_once_with(2)

    def test_parse_compose_ps_prefers_health_and_falls_back_to_state(self) -> None:
        module = load_module()

        states = module.parse_compose_ps(
            '[{"Service": "db", "Health": "healthy", "State": "running"}, '
            '{"Service": "django", "State": "running"}]'
        )

        self.assertEqual(states, {"db": "healthy", "django": "running"})

    def test_parse_compose_ps_supports_ndjson(self) -> None:
        module = load_module()

        states = module.parse_compose_ps(
            '{"Service": "db", "Health": "healthy"}\n'
            '{"Service": "django", "State": "running"}\n'
        )

        self.assertEqual(states, {"db": "healthy", "django": "running"})

    def test_wait_for_http_returns_for_successful_response(self) -> None:
        module = load_module()
        response = MagicMock()
        response.status = 200

        with patch.object(module, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            module.wait_for_http("http://example.test", timeout=1)

        urlopen.assert_called_once_with("http://example.test", timeout=5)

    def test_wait_for_http_retries_connection_reset_before_success(self) -> None:
        module = load_module()
        response = MagicMock()
        response.status = 200
        successful_request = MagicMock()
        successful_request.__enter__.return_value = response

        with (
            patch.object(module, "urlopen") as urlopen,
            patch.object(module.time, "sleep") as sleep,
        ):
            urlopen.side_effect = [ConnectionResetError(54, "Connection reset"), successful_request]
            module.wait_for_http("http://example.test", timeout=1)

        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_wait_until_healthy_waits_for_core_service_health(self) -> None:
        module = load_module()

        with (
            patch.object(module, "wait_for_http") as wait_for_http,
            patch.object(module, "wait_for_core_services") as wait_for_core_services,
        ):
            module.wait_until_healthy()

        self.assertEqual(wait_for_http.call_count, 2)
        wait_for_core_services.assert_called_once_with()

    def test_start_initializes_brings_up_and_waits_for_stack(self) -> None:
        module = load_module()

        with (
            patch.object(module, "docker_is_ready", return_value=True),
            patch.object(module, "initialize_environment") as initialize,
            patch.object(module.subprocess, "run") as run,
            patch.object(module, "wait_until_healthy") as wait,
        ):
            module.start_stack()

        initialize.assert_called_once_with()
        run.assert_called_once_with(module.compose_command("up", "-d"), check=True)
        wait.assert_called_once_with()

    def test_status_cli_prints_services_and_validates_health(self) -> None:
        module = load_module()
        states = {
            "db": "healthy",
            "redis": "healthy",
            "django": "running",
            "celery": "running",
            "memcached": "running",
            "geonode": "running",
            "letsencrypt": "running",
            "geoserver": "running",
            "data-dir-conf": "running",
        }
        output = io.StringIO()

        with (
            patch.object(module, "stack_status", return_value=states),
            patch.object(sys, "argv", ["geonode_stack.py", "status"]),
            contextlib.redirect_stdout(output),
        ):
            module.main()

        self.assertEqual(output.getvalue(), "".join(f"{service}: {state}\n" for service, state in states.items()))


if __name__ == "__main__":
    unittest.main()
