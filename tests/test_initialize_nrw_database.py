from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
import initialize_nrw_database as initializer  # noqa: E402


class DatabaseInitializationTest(unittest.TestCase):
    def test_cluster_sql_creates_database_and_three_login_roles_idempotently(self) -> None:
        sql = initializer.cluster_initialization_sql()

        self.assertIn("CREATE ROLE", sql)
        self.assertIn("ALTER ROLE", sql)
        self.assertIn("CREATE DATABASE", sql)
        self.assertIn("pg_roles", sql)
        self.assertIn("pg_database", sql)
        self.assertIn(":'owner_password'", sql)
        self.assertIn("ALTER ROLE %I IN DATABASE %I SET jit = off", sql)
        self.assertIn(":'publish_user'", sql)
        self.assertNotIn("example-secret", sql)

    def test_grants_keep_publish_read_only_and_scenario_narrowly_writable(self) -> None:
        sql = initializer.database_grants_sql()

        self.assertIn("GRANT SELECT ON ALL TABLES IN SCHEMA publish", sql)
        self.assertIn("GRANT SELECT, INSERT, DELETE ON scenario.proposed_chargers", sql)
        self.assertIn("REVOKE ALL ON ALL TABLES IN SCHEMA publish FROM %I", sql)
        self.assertIn("ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA publish REVOKE ALL ON TABLES FROM %I", sql)
        self.assertNotIn("GRANT UPDATE ON scenario.proposed_chargers", sql)
        self.assertNotIn("GRANT INSERT ON ALL TABLES IN SCHEMA publish", sql)
        self.assertIn("REVOKE ALL ON ALL TABLES IN SCHEMA publish", sql)
        self.assertIn("REVOKE ALL ON ALL TABLES IN SCHEMA scenario", sql)
        self.assertIn("REVOKE ALL ON SCHEMA raw, staging, analytics", sql)

    def test_database_url_replacement_preserves_credentials_and_port(self) -> None:
        result = initializer.database_url(
            "postgresql://postgres:secret@db:5432/postgres",
            "nrw_gis",
        )

        self.assertEqual(result, "postgresql://postgres:secret@db:5432/nrw_gis")

    def test_scenario_validation_function_runs_with_owner_privileges(self) -> None:
        schema = (ROOT / "db" / "nrw_schema.sql").read_text(encoding="utf-8")

        self.assertIn("SECURITY DEFINER", schema)
        self.assertIn("SET search_path = pg_catalog, public", schema)
        self.assertIn("ALTER COLUMN geom_25832 DROP EXPRESSION", schema)
        self.assertIn("NEW.geom_25832 := ST_Transform(NEW.geom, 25832)", schema)


if __name__ == "__main__":
    unittest.main()
