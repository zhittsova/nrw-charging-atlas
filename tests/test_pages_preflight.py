import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.pages_preflight import project_origin


def project() -> dict:
    return {"name": "nrw-ev-atlas", "production_branch": "main", "subdomain": "nrw-ev-atlas-123.pages.dev"}


def test_uses_actual_subdomain_returned_by_cloudflare() -> None:
    assert project_origin(project(), "nrw-ev-atlas") == "https://nrw-ev-atlas-123.pages.dev"


@pytest.mark.parametrize("change", [
    {"uses_functions": True},
    {"deployment_configs": {"preview": {"kv_namespaces": {"KV": {"namespace_id": "namespace"}}}}},
    {"source": {"type": "github"}},
    {"production_branch": "develop"},
    {"subdomain": "example.com/malicious"},
    {"deployment_configs": {"production": {"r2_buckets": {"DATA": {"name": "paid-storage"}}}}},
])
def test_refuses_wrong_or_non_static_project(change: dict) -> None:
    with pytest.raises(ValueError):
        project_origin(project() | change, "nrw-ev-atlas")
