from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "geonode_stack.py"
UPSTREAM_HEALTHCHECK_FIXTURE = (
    ROOT / "tests" / "fixtures" / "geonode_5_1_geoserver_healthcheck.yml"
)


def load_module():
    spec = importlib.util.spec_from_file_location("geonode_stack", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GeoNodeEnvironmentTest(unittest.TestCase):
    def test_upstream_build_exclusions_preserve_pin_and_stay_stable(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            upstream = checkout / ".dockerignore"
            upstream.write_text("geonode/uploaded\ngeonode/static_root\n", encoding="utf-8")
            original = upstream.read_bytes()
            with patch.object(module, "GEONODE_DIR", checkout):
                module.prepare_upstream_build_context()
                generated = checkout / "Dockerfile.dockerignore"
                rules = generated.read_text(encoding="utf-8").splitlines()
                for excluded in (".git", ".env", ".env.*", "**/__pycache__", "**/*.py[cod]"):
                    self.assertIn(excluded, rules)
                self.assertTrue(generated.read_bytes().startswith(original))
                os.utime(generated, (1_700_000_000, 1_700_000_000))
                module.prepare_upstream_build_context()
                self.assertEqual(generated.stat().st_mtime, 1_700_000_000)
            self.assertEqual(upstream.read_bytes(), original)

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
            "GEOSERVER_JAVA_OPTS=-Xms512m -Xmx1G -Dcustom.geoserver.flag=keep-me -XX:TieredStopAtLevel=1",
            patched,
        )
        self.assertIn(
            "GEOSERVER_PUBLIC_LOCATION=http://localhost:8080/geoserver/",
            patched,
        )
        self.assertIn("127.0.0.1", patched)

    def test_patch_env_keeps_an_explicit_geoserver_compiler_level(self) -> None:
        module = load_module()
        source = "GEOSERVER_JAVA_OPTS=-Xms4G -Xmx4G -XX:TieredStopAtLevel=2\n"
        patched = module.patch_env_text(source)
        self.assertIn("-XX:TieredStopAtLevel=2", patched)
        self.assertNotIn("-XX:TieredStopAtLevel=1", patched)

    def test_patch_env_preserves_configured_database_name(self) -> None:
        module = load_module()

        patched = module.patch_env_text("NRW_DATABASE_NAME=nrw_custom\n")

        self.assertIn("NRW_DATABASE_NAME=nrw_custom", patched)
        self.assertNotIn("NRW_DATABASE_NAME=nrw_gis", patched)

    def test_unresolved_template_values_are_rejected(self) -> None:
        module = load_module()

        with self.assertRaisesRegex(ValueError, "unresolved placeholders"):
            module.assert_resolved_env("POSTGRES_PASSWORD={pgpwd}\n")

    def test_compose_command_uses_root_entry_point_and_private_environment(self) -> None:
        module = load_module()

        command = module.compose_command("config", "--quiet")

        self.assertEqual(command[:2], ["docker", "compose"])
        self.assertIn("--project-directory", command)
        self.assertIn(str(ROOT), command)
        self.assertIn(str(ROOT / "geonode" / ".env"), command)
        self.assertEqual(
            [command[index + 1] for index, value in enumerate(command) if value == "-f"],
            [
                str(ROOT / "docker-compose.yaml"),
            ],
        )
        self.assertEqual(command[-2:], ["config", "--quiet"])

    def test_project_database_name_uses_non_secret_environment_setting(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("NRW_DATABASE_NAME=nrw_alternate\nSECRET=not-read\n")
            with patch.object(module, "ENV_PATH", env_path):
                self.assertEqual(module.project_database_name(), "nrw_alternate")

    def test_project_database_name_process_override_beats_selected_file(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("NRW_DATABASE_NAME=nrw_from_file\n", encoding="utf-8")
            with (
                patch.object(module, "ENV_PATH", env_path),
                patch.dict("os.environ", {"NRW_DATABASE_NAME": "nrw_from_process"}, clear=True),
            ):
                self.assertEqual(module.project_database_name(), "nrw_from_process")

    def test_database_name_precedence_honors_default_file_process_and_cli(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / "alternate.env"
            env_path.write_text("NRW_DATABASE_NAME='nrw_from_file'\n", encoding="utf-8")

            self.assertEqual(
                module.resolve_database_name(env_path=env_path, environ={}), "nrw_from_file"
            )
            self.assertEqual(
                module.resolve_database_name(
                    env_path=env_path,
                    environ={"NRW_DATABASE_NAME": "nrw_from_process"},
                ),
                "nrw_from_process",
            )
            self.assertEqual(
                module.resolve_database_name(
                    env_path=env_path,
                    environ={"NRW_DATABASE_NAME": "nrw_from_process"},
                    explicit="nrw_from_cli",
                ),
                "nrw_from_cli",
            )
            self.assertEqual(
                module.resolve_database_name(env_path=Path(directory) / "missing", environ={}),
                "nrw_gis",
            )
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                module.resolve_database_name(
                    env_path=env_path, environ={"NRW_DATABASE_NAME": ""}
                )
            with self.assertRaisesRegex(ValueError, "PostgreSQL identifier"):
                module.resolve_database_name(
                    env_path=env_path, environ={}, explicit="not-valid-name"
                )

    def test_invalid_database_name_fails_before_environment_mutation(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            geonode_dir = Path(directory) / "geonode"
            geonode_dir.mkdir()
            (geonode_dir / "create-envfile.py").touch()
            compose_path = geonode_dir / "docker-compose.yml"
            compose_path.touch()
            env_path = geonode_dir / ".env"
            original = "NRW_DATABASE_NAME=not-valid-name\n"
            env_path.write_text(original, encoding="utf-8")
            with (
                patch.object(module, "GEONODE_DIR", geonode_dir),
                patch.object(module, "ENV_PATH", env_path),
                patch.object(module, "COMPOSE_PATH", compose_path),
                patch.object(module, "validate_upstream_checkout"),
                patch.object(module.subprocess, "run") as run,
            ):
                with self.assertRaisesRegex(ValueError, "PostgreSQL identifier"):
                    module.initialize_environment()

            self.assertEqual(env_path.read_text(encoding="utf-8"), original)
            run.assert_not_called()

    def test_project_database_secrets_are_generated_once_and_preserved(self) -> None:
        module = load_module()
        source = "POSTGRES_PASSWORD=keep-me\nNRW_DATABASE_PASSWORD=existing\n"
        generated = iter(["read-password", "scenario-password"])

        patched = module.ensure_project_env_values(source, token_factory=lambda: next(generated))
        repeated = module.ensure_project_env_values(patched, token_factory=lambda: "must-not-be-used")

        self.assertIn("NRW_DATABASE_PASSWORD=existing", patched)
        self.assertIn("NRW_GEOSERVER_READ_PASSWORD=read-password", patched)
        self.assertIn("NRW_GEOSERVER_SCENARIO_PASSWORD=scenario-password", patched)
        self.assertEqual(patched, repeated)

    def test_validate_upstream_checkout_rejects_missing_checkout(self) -> None:
        """Catches treating an archive without the nested upstream clone as ready."""
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            missing_checkout = Path(directory) / "geonode"
            with patch.object(module, "GEONODE_DIR", missing_checkout):
                with self.assertRaisesRegex(RuntimeError, "missing"):
                    module.validate_upstream_checkout()

    def test_validate_upstream_checkout_accepts_exact_clean_checkout(self) -> None:
        """Catches accepting a checkout other than the pinned upstream revision."""
        module = load_module()
        expected = json.loads((ROOT / "config" / "geonode-upstream.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "geonode"
            (checkout / ".git").mkdir(parents=True)
            responses = [
                subprocess.CompletedProcess([], 0, stdout=f"{expected['commit']}\n"),
                subprocess.CompletedProcess([], 0, stdout=f"{expected['repository']}.git\n"),
                subprocess.CompletedProcess([], 0, stdout=""),
            ]
            with (
                patch.object(module, "GEONODE_DIR", checkout),
                patch.object(module.subprocess, "run", side_effect=responses),
            ):
                module.validate_upstream_checkout()

    def test_validate_upstream_checkout_rejects_wrong_revision(self) -> None:
        """Catches a valid Git checkout at a revision other than GeoNode 5.1.0."""
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "geonode"
            (checkout / ".git").mkdir(parents=True)
            responses = [
                subprocess.CompletedProcess([], 0, stdout="not-the-pinned-revision\n"),
                subprocess.CompletedProcess([], 0, stdout="https://github.com/GeoNode/geonode.git\n"),
            ]
            with (
                patch.object(module, "GEONODE_DIR", checkout),
                patch.object(module.subprocess, "run", side_effect=responses),
            ):
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    module.validate_upstream_checkout()

    def test_validate_upstream_checkout_rejects_tracked_local_changes(self) -> None:
        """Catches a modified upstream checkout that could invalidate the local stack."""
        module = load_module()
        expected = json.loads((ROOT / "config" / "geonode-upstream.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "geonode"
            (checkout / ".git").mkdir(parents=True)
            responses = [
                subprocess.CompletedProcess([], 0, stdout=f"{expected['commit']}\n"),
                subprocess.CompletedProcess([], 0, stdout=f"{expected['repository']}\n"),
                subprocess.CompletedProcess([], 0, stdout=" M docker-compose.yml\n"),
            ]
            with (
                patch.object(module, "GEONODE_DIR", checkout),
                patch.object(module.subprocess, "run", side_effect=responses),
            ):
                with self.assertRaisesRegex(RuntimeError, "tracked local modifications"):
                    module.validate_upstream_checkout()

    def test_provision_upstream_checkout_clones_and_detaches_exact_pin(self) -> None:
        """Catches first-time provisioning that omits the exact pinned detached checkout."""
        module = load_module()
        expected = json.loads((ROOT / "config" / "geonode-upstream.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "geonode"

            def run(command, **_kwargs):
                if command[:2] == ["git", "clone"]:
                    (checkout / ".git").mkdir(parents=True)
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.object(module, "GEONODE_DIR", checkout),
                patch.object(module.subprocess, "run", side_effect=run) as run_mock,
                patch.object(module, "validate_upstream_checkout") as validate,
            ):
                module.provision_upstream_checkout()

        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertEqual(
            commands[0],
            ["git", "clone", "--no-checkout", expected["repository"], str(checkout)],
        )
        self.assertEqual(
            commands[1],
            ["git", "-C", str(checkout), "checkout", "--detach", expected["commit"]],
        )
        validate.assert_called_once_with()

    def test_provision_existing_checkout_only_validates_without_modifying_it(self) -> None:
        """Catches a repeat provision that rewrites or rejects an already valid checkout."""
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "geonode"
            (checkout / ".git").mkdir(parents=True)
            with (
                patch.object(module, "GEONODE_DIR", checkout),
                patch.object(module, "validate_upstream_checkout") as validate,
                patch.object(module.subprocess, "run") as run,
            ):
                module.provision_upstream_checkout()

        validate.assert_called_once_with()
        run.assert_not_called()

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

    def test_local_override_replaces_upstream_public_port_mappings(self) -> None:
        override = (
            ROOT / "config" / "geonode" / "docker-compose.nrw-project.yml"
        ).read_text()

        self.assertIn("geonode:\n    ports: !override", override)
        self.assertIn("geoserver:\n    ports: !override", override)
        self.assertIn('"127.0.0.1:${HTTP_PORT}:80"', override)
        self.assertIn('"127.0.0.1:8080:8080"', override)

    def test_local_override_extends_the_geoserver_health_deadline(self) -> None:
        """Upstream's ~3 minute deadline fails a healthy stack mid-deployment.

        GeoServer's webapp deployment routinely runs for several minutes before its
        Spring context starts, so the upstream timings report a correct stack as
        unhealthy. That in turn fails `frontend`'s `service_healthy` dependency and
        aborts `geonode_stack start`. Only the timings are overridden here; the
        upstream probe itself must survive the merge.
        """
        # The ignored upstream checkout is intentionally unavailable to normal
        # unit runs.  This compact, tracked fixture records the pinned GeoNode
        # 5.1 healthcheck contract that the project override must extend.
        upstream = UPSTREAM_HEALTHCHECK_FIXTURE.read_text(encoding="utf-8")
        override = (
            ROOT / "config" / "geonode" / "docker-compose.nrw-project.yml"
        ).read_text()

        self.assertIn("start_period: 60s", upstream)
        self.assertIn("http://geoserver:8080/geoserver/ows", upstream)
        block = re.search(
            r"^    healthcheck:\n((?:^      .+\n)+)", override, re.MULTILINE
        )
        self.assertIsNotNone(block, "the project override must declare a healthcheck")
        assert block is not None
        keys = {line.split(":", 1)[0].strip() for line in block.group(1).splitlines()}

        self.assertEqual(keys, {"start_period", "interval", "retries"})
        self.assertIn("start_period: 600s", block.group(1))
        self.assertIn("interval: 30s", block.group(1))
        self.assertIn("retries: 3", block.group(1))

    def test_init_first_run_prints_only_ready_message(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            geonode_dir = Path(temporary_directory) / "geonode"
            geonode_dir.mkdir()
            (geonode_dir / "create-envfile.py").touch()
            (geonode_dir / ".dockerignore").touch()
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
                "frontend",
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
    def test_resource_report_reads_memory_and_disk_without_environment(self, run: Mock) -> None:
        module = load_module()
        run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout='{"MemTotal": 8589934592, "NCPU": 6}'),
            subprocess.CompletedProcess(
                [], 0, stdout='{"Type":"Images","Size":"1GB","Reclaimable":"500MB"}\n'
            ),
        ]

        memory, cpus, disk, host_free = module.docker_resource_report()

        self.assertEqual(memory, 8589934592)
        self.assertEqual(cpus, 6)
        self.assertEqual(disk[0]["Type"], "Images")
        self.assertIsInstance(host_free, int)
        self.assertEqual(
            run.call_args_list[0].args[0], ["docker", "info", "--format", "{{json .}}"]
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["docker", "system", "df", "--format", "{{json .}}"],
        )

    def test_resource_gate_rejects_insufficient_memory(self) -> None:
        module = load_module()
        with patch.object(module, "docker_resource_report", return_value=(2 * 1024**3, 8, [], 0)):
            with self.assertRaisesRegex(RuntimeError, "at least 3.5 GiB usable"):
                module.assert_docker_resources()

    def test_resource_gate_accepts_four_gib_vm_after_guest_overhead(self) -> None:
        module = load_module()
        with (
            patch.object(module, "docker_resource_report",
                         return_value=(3840 * 1024**2, 4, [], 5 * 1024**3)),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            module.assert_docker_resources()

    def test_resource_gate_rejects_insufficient_cpus(self) -> None:
        """Two CPUs starve GeoServer's single-threaded deployment past every health deadline."""
        module = load_module()
        with patch.object(
            module, "docker_resource_report", return_value=(8 * 1024**3, 2, [], 5 * 1024**3)
        ):
            with self.assertRaisesRegex(RuntimeError, r"2 CPU\(s\); at least 4 are required"):
                module.assert_docker_resources()

    def test_resource_gate_accepts_the_minimum_cpu_allocation(self) -> None:
        module = load_module()
        with (
            patch.object(
                module,
                "docker_resource_report",
                return_value=(8 * 1024**3, module.MINIMUM_DOCKER_CPUS, [], 5 * 1024**3),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            module.assert_docker_resources()

    def test_resource_report_rejects_a_missing_cpu_count(self) -> None:
        module = load_module()
        with patch("subprocess.run") as run:
            run.side_effect = [
                subprocess.CompletedProcess([], 0, stdout='{"MemTotal": 8589934592}'),
            ]
            with self.assertRaisesRegex(RuntimeError, "did not report its available CPUs"):
                module.docker_resource_report()

    def test_resource_diagnostics_distinguish_host_free_space_from_docker_capacity(self) -> None:
        module = load_module()
        output = io.StringIO()
        with (
            patch.object(
                module, "docker_resource_report", return_value=(8 * 1024**3, 8, [], 5 * 1024**3)
            ),
            contextlib.redirect_stdout(output),
        ):
            module.assert_docker_resources()

        self.assertIn("Host storage available", output.getvalue())
        self.assertIn("Docker storage capacity: unknown", output.getvalue())
        self.assertIn("Docker CPUs: 8", output.getvalue())

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
            "frontend": "running",
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
            "frontend": "running",
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
            "frontend": "running",
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

        self.assertEqual(wait_for_http.call_count, 3)
        wait_for_http.assert_any_call("http://localhost:8081/")
        wait_for_core_services.assert_called_once_with()

    def test_start_initializes_brings_up_and_waits_for_stack(self) -> None:
        module = load_module()

        with (
            patch.object(module, "docker_is_ready", return_value=True),
            patch.object(module, "assert_docker_resources") as resources,
            patch.object(module, "initialize_environment") as initialize,
            patch.object(module.subprocess, "run") as run,
            patch.object(module, "wait_until_healthy") as wait,
        ):
            module.start_stack()

        initialize.assert_called_once_with()
        resources.assert_called_once_with()
        run.assert_called_once_with(module.compose_command("up", "-d", "--build"), check=True)
        wait.assert_called_once_with()

    def test_rebuild_preserves_volumes_while_recreating_containers(self) -> None:
        module = load_module()
        with (
            patch.object(module, "docker_is_ready", return_value=True),
            patch.object(module, "assert_docker_resources"),
            patch.object(module, "initialize_environment"),
            patch.object(module, "wait_until_healthy"),
            patch.object(module.subprocess, "run") as run,
        ):
            module.rebuild_stack()

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(module.compose_command("build", "--no-cache", "frontend", "nrw-etl"), commands)
        self.assertIn(module.compose_command("up", "-d", "--force-recreate"), commands)
        self.assertFalse(any("-v" in command or "--volumes" in command for command in commands))

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
            "frontend": "running",
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
