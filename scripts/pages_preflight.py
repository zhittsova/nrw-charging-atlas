"""Check the configured Pages project and return its actual deployment origin."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen


def project_origin(project: dict, expected_name: str) -> str:
    if project.get("name") != expected_name or project.get("production_branch") != "main":
        raise ValueError("Pages project name/production branch does not match this workflow")
    if project.get("source"):
        raise ValueError("Use a Direct Upload project; disable the separate Git integration")
    if project.get("uses_functions"):
        raise ValueError("This project contains Functions; use a static-only Pages project")
    for config in (project.get("deployment_configs") or {}).values():
        for key, value in (config or {}).items():
            if value and ("binding" in key or key in {
                "r2_buckets", "d1_databases", "kv_namespaces", "durable_object_namespaces",
                "services", "queue_producers", "analytics_engine_datasets", "browsers",
            }):
                raise ValueError(f"Unexpected Pages backend configuration: {key}")
    subdomain = project.get("subdomain", "")
    if not re.fullmatch(r"[a-z0-9-]+\.pages\.dev", subdomain):
        raise ValueError("Cloudflare returned an unexpected Pages subdomain")
    return f"https://{subdomain}"


def main() -> None:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    project = os.environ.get("PAGES_PROJECT", "nrw-ev-atlas")
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not re.fullmatch(r"[a-f0-9]{32}", account) or not re.fullmatch(r"[a-z0-9-]+", project) or not token:
        raise ValueError("Configure the Cloudflare Account ID, Pages project and Pages Edit token first")
    request = Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/{project}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not payload.get("success"):
        raise RuntimeError("Cloudflare project lookup failed; check the project and token permissions")
    origin = project_origin(payload["result"], project)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as stream:
            stream.write(f"origin={origin}\n")
    print(f"Verified static Direct Upload project: {origin}")


if __name__ == "__main__":
    main()
