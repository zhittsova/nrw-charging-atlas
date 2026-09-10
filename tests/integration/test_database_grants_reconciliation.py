"""Verify grant-only repair removes stale publication-role write access."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import initialize_nrw_database as initializer  # noqa: E402
from run_postgis_tests import psql_connection, require_disposable_database_url  # noqa: E402


DATABASE_URL = os.environ.get("SCENARIO_TEST_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "SCENARIO_TEST_DATABASE_URL is not configured")
class DatabaseGrantsReconciliationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert DATABASE_URL is not None
        run_id = os.environ.get("SCENARIO_TEST_RUN_ID") or ""
        endpoint = os.environ.get("SCENARIO_TEST_ENDPOINT")
        require_disposable_database_url(DATABASE_URL, run_id=run_id, endpoint=endpoint)
        cls.psql_base, cls.psql_environment = psql_connection(DATABASE_URL, run_id=run_id, endpoint=endpoint)
        cls.owner = f"s12_owner_{run_id}"
        cls.reader = f"s12_reader_{run_id}"
        cls.writer = f"s12_writer_{run_id}"

    @classmethod
    def psql(cls, sql: str, *, check: bool = True, variables: bool = False) -> subprocess.CompletedProcess[str]:
        command = list(cls.psql_base)
        if variables:
            command.extend(
                [
                    "-v",
                    f"database_name={os.environ['SCENARIO_TEST_DATABASE_URL'].rsplit('/', 1)[-1]}",
                    "-v",
                    f"owner_user={cls.owner}",
                    "-v",
                    f"publish_user={cls.reader}",
                    "-v",
                    f"scenario_user={cls.writer}",
                ]
            )
        result = subprocess.run(
            command,
            input=sql,
            text=True,
            capture_output=True,
            check=False,
            env=cls.psql_environment,
        )
        if check and result.returncode:
            raise RuntimeError(result.stderr or result.stdout or "psql failed")
        return result

    def setUp(self) -> None:
        self.psql(
            f"DROP SCHEMA IF EXISTS publish CASCADE;"
            f"DROP SCHEMA IF EXISTS scenario CASCADE;"
            f"DROP SCHEMA IF EXISTS raw CASCADE;"
            f"DROP SCHEMA IF EXISTS staging CASCADE;"
            f"DROP SCHEMA IF EXISTS analytics CASCADE;"
            f"DROP ROLE IF EXISTS {self.reader};"
            f"DROP ROLE IF EXISTS {self.writer};"
            f"DROP ROLE IF EXISTS {self.owner};"
            f"CREATE ROLE {self.owner};"
            f"CREATE ROLE {self.reader};"
            f"CREATE ROLE {self.writer};"
            f"CREATE SCHEMA publish AUTHORIZATION {self.owner};"
            f"CREATE SCHEMA scenario AUTHORIZATION {self.owner};"
            f"CREATE SCHEMA raw AUTHORIZATION {self.owner};"
            f"CREATE SCHEMA staging AUTHORIZATION {self.owner};"
            f"CREATE SCHEMA analytics AUTHORIZATION {self.owner};"
            f"SET ROLE {self.owner};"
            "CREATE TABLE raw.official (id integer PRIMARY KEY);"
            "INSERT INTO raw.official VALUES (1);"
            "CREATE VIEW publish.official AS SELECT * FROM raw.official;"
            "CREATE TABLE scenario.proposed_chargers (id integer PRIMARY KEY);"
            f"GRANT INSERT, UPDATE, DELETE ON publish.official TO {self.reader};"
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA publish GRANT INSERT, UPDATE, DELETE ON TABLES TO {self.reader};"
            "RESET ROLE;"
        )

    def test_reconciliation_removes_existing_and_default_publish_writes(self) -> None:
        grant_sql = initializer.database_grants_sql()
        self.psql(grant_sql, variables=True)
        self.psql(grant_sql, variables=True)

        privileges = self.psql(
            "SELECT "
            f"has_table_privilege('{self.reader}', 'publish.official', 'SELECT')::text || '|' || "
            f"has_table_privilege('{self.reader}', 'publish.official', 'INSERT')::text || '|' || "
            f"has_table_privilege('{self.reader}', 'publish.official', 'UPDATE')::text || '|' || "
            f"has_table_privilege('{self.reader}', 'publish.official', 'DELETE')::text || '|' || "
            f"has_table_privilege('{self.writer}', 'scenario.proposed_chargers', 'SELECT,INSERT,DELETE')::text || '|' || "
            f"has_table_privilege('{self.writer}', 'scenario.proposed_chargers', 'UPDATE')::text;"
        )
        self.assertEqual(privileges.stdout.strip(), "true|false|false|false|true|false")

        denied = self.psql(
            f"SET ROLE {self.reader}; UPDATE publish.official SET id = 2 WHERE id = 1;",
            check=False,
        )
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("permission denied", denied.stderr.lower())
        self.assertEqual(self.psql("SELECT id FROM raw.official;").stdout.strip(), "1")

        future = self.psql(
            f"SET ROLE {self.owner}; CREATE TABLE publish.future_official (id integer); RESET ROLE;"
            "SELECT "
            f"has_table_privilege('{self.reader}', 'publish.future_official', 'SELECT')::text || '|' || "
            f"has_table_privilege('{self.reader}', 'publish.future_official', 'INSERT,UPDATE,DELETE')::text;"
        )
        self.assertEqual(future.stdout.strip(), "true|false")


if __name__ == "__main__":
    unittest.main()
