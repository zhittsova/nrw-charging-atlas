from __future__ import annotations

import importlib.util
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "backup_restore.py"
sys.path.insert(0, str(ROOT))


def load_module():
    spec = importlib.util.spec_from_file_location("backup_restore", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BackupRestoreTest(unittest.TestCase):
    def test_restore_target_requires_fresh_prefixed_name(self) -> None:
        module = load_module()
        source = {"data": "nrw-restore-geonode-data"}

        with self.assertRaisesRegex(ValueError, "nrw-restore-"):
            module.validate_restore_target("geonode", source)
        with self.assertRaisesRegex(ValueError, "overlap"):
            module.validate_restore_target("nrw-restore-geonode", source)

    def test_restore_target_refuses_existing_volume(self) -> None:
        module = load_module()
        with (
            patch.object(module, "run") as run,
            patch.object(module.subprocess, "run") as subprocess_run,
        ):
            run.return_value.stdout = ""
            subprocess_run.return_value.returncode = 0
            with self.assertRaisesRegex(RuntimeError, "existing restore volume"):
                module.validate_restore_target("nrw-restore-proof", {})

    def test_manifest_verification_detects_corruption(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory)
            component = backup / "components" / "payload.txt"
            component.parent.mkdir()
            component.write_text("original", encoding="utf-8")
            manifest = {
                "format_version": module.BACKUP_FORMAT_VERSION,
                "components": {
                    "payload": {
                        "path": "components/payload.txt",
                        "bytes": component.stat().st_size,
                        "sha256": module.sha256(component),
                    }
                },
            }
            (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with patch.object(module, "REQUIRED_COMPONENTS", frozenset({"payload"})):
                module.load_and_verify_manifest(backup)
                component.write_text("changed", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "integrity"):
                    module.load_and_verify_manifest(backup)

    def test_manifest_verification_rejects_missing_dependencies(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory)
            (backup / "manifest.json").write_text(
                json.dumps({"format_version": module.BACKUP_FORMAT_VERSION, "components": {}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                module.load_and_verify_manifest(backup)

    def test_safe_extract_rejects_symlink(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                info = tarfile.TarInfo("current")
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                output.addfile(info)
            destination = root / "restore"
            destination.mkdir()
            with self.assertRaisesRegex(RuntimeError, "unsafe"):
                module.safe_extract(archive, destination)

    def test_target_environment_rewrites_only_endpoint_fields(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            source.write_text("POSTGRES_PASSWORD=secret\nSITEURL=http://old/\n", encoding="utf-8")
            destination = root / "target.env"
            module.write_target_env(
                source,
                destination,
                target="nrw-restore-proof",
                ports={"db": 15432, "geonode": 18000, "https": 18443, "geoserver": 18080, "frontend": 18081},
            )
            content = destination.read_text(encoding="utf-8")
            self.assertIn("POSTGRES_PASSWORD=secret", content)
            self.assertIn("SITEURL=http://localhost:18000/", content)
            self.assertIn("COMPOSE_PROJECT_NAME=nrw-restore-proof", content)

    def test_restore_volume_uses_an_absolute_backup_mount(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory).resolve()
            (backup / "components").mkdir()
            with patch.object(module, "run") as run:
                module.restore_volume("nrw-restore-proof-gsdatadir", backup, "state.tar.gz")
            command = run.call_args_list[1].args[0]
            self.assertIn(f"type=bind,source={backup},target=/backup,readonly", command)

    def test_isolated_override_replaces_service_environment_files(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "compose.yml"
            runtime = Path(directory) / "runtime path"
            archived_env = Path(directory) / "archived environment"
            module.write_isolated_override(
                override,
                runtime,
                archived_env,
                {"db": 15432, "geonode": 18000, "https": 18443, "geoserver": 18080, "frontend": 18081},
            )
            content = override.read_text(encoding="utf-8")
            for service in module.RESTORED_ENV_FILE_SERVICES:
                self.assertIn(f"  {service}:\n    env_file: !override", content)
                self.assertEqual(content.count(f"  {service}:\n"), 1)
            self.assertEqual(
                content.count(f"- {json.dumps(str(archived_env))}"),
                len(module.RESTORED_ENV_FILE_SERVICES),
            )
            self.assertEqual(content.count("  db:\n"), 1)
            self.assertIn('GEONODE_DATABASE: ""', content)
            self.assertIn('GEONODE_GEODATABASE: ""', content)
            self.assertIn('"127.0.0.1:15432:5432"', content)
            self.assertIn('"127.0.0.1:18000:80"', content)
            self.assertIn(f'"{runtime}:/usr/share/nginx/html/runtime:ro"', content)
            self.assertNotIn('\\\\"', content)

    def test_drill_cleans_known_uuid_after_source_probe_failure(self) -> None:
        module = load_module()
        with (
            patch.object(module, "create_source_probe", side_effect=module.requests.ConnectionError("after commit")),
            patch.object(module, "cleanup_source_probe") as cleanup,
            patch.object(module, "create_backup") as backup,
        ):
            with self.assertRaisesRegex(module.requests.ConnectionError, "after commit"):
                module.run_drill(Path("/private/tmp/ignored"), "nrw-restore-proof")
        backup.assert_not_called()
        self.assertEqual(cleanup.call_args.args[0], "http://localhost:8081")
        self.assertRegex(cleanup.call_args.args[1], r"^[0-9a-f-]{36}$")

    def test_wait_for_database_target_requires_healthy_state(self) -> None:
        module = load_module()
        first = type("Completed", (), {"stdout": '[{"Service":"db","Health":"starting"}]'})()
        ready = type("Completed", (), {"stdout": '[{"Service":"db","Health":"healthy"}]'})()
        with patch.object(module, "run", side_effect=(first, ready)), patch.object(module.time, "sleep"):
            module.wait_for_database_target(["docker", "compose"], timeout=1)

    def test_restored_services_must_use_private_target_siteurl(self) -> None:
        module = load_module()
        ports = {"db": 15432, "geonode": 18000, "https": 18443, "geoserver": 18080, "frontend": 18081}
        completed = type("Completed", (), {"stdout": "http://localhost:18000/\n"})()
        with patch.object(module, "run", return_value=completed) as run:
            module.assert_restored_service_environment(["docker", "compose"], ports)
        self.assertEqual(run.call_count, len(module.RESTORED_ENVIRONMENT_ASSERTION_SERVICES))
        for call, service in zip(run.call_args_list, module.RESTORED_ENVIRONMENT_ASSERTION_SERVICES):
            self.assertEqual(call.args[0][-3:], [service, "printenv", "SITEURL"])

    def test_restored_services_reject_live_source_siteurl(self) -> None:
        module = load_module()
        ports = {"db": 15432, "geonode": 18000, "https": 18443, "geoserver": 18080, "frontend": 18081}
        completed = type("Completed", (), {"stdout": "http://localhost:8000/\n"})()
        with patch.object(module, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "did not load the restored SITEURL"):
                module.assert_restored_service_environment(["docker", "compose"], ports)

    def test_target_bootstrap_cleanup_removes_postgis_auxiliary_schemas(self) -> None:
        module = load_module()
        cleanup = module.TARGET_BOOTSTRAP_CLEANUP_SQL
        self.assertIn("DROP EXTENSION IF EXISTS postgis_tiger_geocoder CASCADE", cleanup)
        self.assertIn("DROP SCHEMA IF EXISTS tiger CASCADE", cleanup)
        self.assertIn("DROP SCHEMA IF EXISTS tiger_data CASCADE", cleanup)
        self.assertIn("DROP SCHEMA IF EXISTS topology CASCADE", cleanup)


if __name__ == "__main__":
    unittest.main()
