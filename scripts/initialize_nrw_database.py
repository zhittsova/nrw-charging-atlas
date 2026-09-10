from __future__ import annotations

import argparse
import os
import subprocess
from urllib.parse import quote, urlsplit, urlunsplit


def cluster_initialization_sql() -> str:
    return r"""\set ON_ERROR_STOP on
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'owner_user', :'owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'owner_user')
\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'publish_user', :'publish_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'publish_user')
\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'scenario_user', :'scenario_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'scenario_user')
\gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'owner_user', :'owner_password') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'publish_user', :'publish_password') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'scenario_user', :'scenario_password') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'database_name', :'owner_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'database_name')
\gexec
"""


def database_grants_sql() -> str:
    return r"""\set ON_ERROR_STOP on
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'database_name') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'database_name', :'owner_user') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'database_name', :'publish_user') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'database_name', :'scenario_user') \gexec
SELECT format('REVOKE ALL ON SCHEMA publish FROM %I', :'scenario_user') \gexec
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA publish FROM %I', :'scenario_user') \gexec
SELECT format('REVOKE ALL ON SCHEMA publish FROM %I', :'publish_user') \gexec
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA publish FROM %I', :'publish_user') \gexec
SELECT format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA publish FROM %I', :'publish_user') \gexec
SELECT format('REVOKE ALL ON SCHEMA scenario FROM %I', :'publish_user') \gexec
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA scenario FROM %I', :'publish_user') \gexec
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA scenario FROM %I', :'scenario_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA publish TO %I', :'publish_user') \gexec
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA publish TO %I', :'publish_user') \gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA publish REVOKE ALL ON TABLES FROM %I',
  :'owner_user', :'publish_user'
) \gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA publish GRANT SELECT ON TABLES TO %I',
  :'owner_user', :'publish_user'
) \gexec
SELECT format('GRANT USAGE ON SCHEMA scenario TO %I', :'scenario_user') \gexec
SELECT format(
  'GRANT SELECT, INSERT, DELETE ON scenario.proposed_chargers TO %I',
  :'scenario_user'
) \gexec
SELECT format('REVOKE ALL ON SCHEMA raw, staging, analytics FROM %I', :'publish_user') \gexec
SELECT format('REVOKE ALL ON SCHEMA raw, staging, analytics FROM %I', :'scenario_user') \gexec
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA raw, staging, analytics FROM %I', :'publish_user') \gexec
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA raw, staging, analytics FROM %I', :'scenario_user') \gexec
"""


def database_url(admin_url: str, name: str) -> str:
    parsed = urlsplit(admin_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{quote(name, safe='')}", parsed.query, parsed.fragment))


def run_psql(url: str, sql: str, variables: dict[str, str]) -> None:
    command = ["psql", "--dbname", url, "--no-psqlrc", "--set", "ON_ERROR_STOP=1"]
    for name, value in variables.items():
        command.extend(["--set", f"{name}={value}"])
    result = subprocess.run(
        command,
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "psql failed").strip())


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the NRW project database and least-privilege roles")
    parser.add_argument("--grant-only", action="store_true")
    args = parser.parse_args()
    admin_url = required_environment("POSTGRES_ADMIN_URL")
    values = {
        "database_name": os.environ.get("NRW_DATABASE_NAME", "nrw_gis"),
        "owner_user": required_environment("NRW_DATABASE_USER"),
        "owner_password": required_environment("NRW_DATABASE_PASSWORD"),
        "publish_user": required_environment("NRW_GEOSERVER_READ_USER"),
        "publish_password": required_environment("NRW_GEOSERVER_READ_PASSWORD"),
        "scenario_user": required_environment("NRW_GEOSERVER_SCENARIO_USER"),
        "scenario_password": required_environment("NRW_GEOSERVER_SCENARIO_PASSWORD"),
    }
    if args.grant_only:
        run_psql(database_url(admin_url, values["database_name"]), database_grants_sql(), values)
        print("NRW database access grants are ready")
        return
    run_psql(admin_url, cluster_initialization_sql(), values)
    run_psql(
        database_url(admin_url, values["database_name"]),
        "CREATE EXTENSION IF NOT EXISTS postgis;\n",
        values,
    )
    print("NRW database and login roles are ready")


if __name__ == "__main__":
    main()
