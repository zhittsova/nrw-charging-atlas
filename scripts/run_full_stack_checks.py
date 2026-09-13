"""Run opt-in checks against a deliberately running local full stack.

The normal CI jobs deliberately never call this module: they cannot safely
assume a GeoNode stack, generated runtime assets, or local source data.  An
operator must opt in after bootstrapping the project stack.  A non-opted-in
invocation reports SKIPPED, never PASSED.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_TEST = "tests/integration/test_geonode_foundation.py"
E2E_VERIFIER = "scripts/verify_project_e2e.py"


def _preserved_state_error(
    *,
    geonode_env_file: Path | None,
    runtime_root: Path | None,
    deployment_revision_file: Path | None,
    expected_revision: str | None,
) -> str | None:
    """Return the opt-in state-contract error, without reading credentials."""
    required_paths = {
        "--geonode-env-file": geonode_env_file,
        "--runtime-root": runtime_root,
        "--deployment-revision-file": deployment_revision_file,
    }
    missing = [flag for flag, path in required_paths.items() if path is None]
    if missing:
        return f"--enabled requires {' '.join(missing)} and --expected-revision"
    if not expected_revision:
        return "--enabled requires a non-empty --expected-revision"

    source_root = ROOT.resolve()
    for flag, path in required_paths.items():
        assert path is not None
        if not path.is_absolute():
            return f"{flag} must be an absolute path outside the disposable source checkout"
        resolved = path.resolve()
        if resolved.is_relative_to(source_root):
            return f"{flag} must be outside the disposable source checkout"

    assert geonode_env_file is not None
    assert runtime_root is not None
    assert deployment_revision_file is not None
    if not geonode_env_file.is_file():
        return f"preserved GeoNode environment file is missing: {geonode_env_file}"
    if not runtime_root.is_dir():
        return f"preserved runtime root is missing: {runtime_root}"
    if not deployment_revision_file.is_file():
        return f"preserved deployment revision file is missing: {deployment_revision_file}"
    actual_revision = deployment_revision_file.read_text(encoding="utf-8").strip()
    if actual_revision != expected_revision:
        return "preserved deployment revision does not match --expected-revision"
    return None


def run_full_stack_checks(
    *,
    enabled: bool,
    geonode_env_file: Path | None = None,
    runtime_root: Path | None = None,
    deployment_revision_file: Path | None = None,
    expected_revision: str | None = None,
) -> int:
    """Report an explicit full-stack state and return its corresponding status."""
    if not enabled:
        print(
            "FULL-STACK CHECK: SKIPPED (opt-in only; run with --enabled after "
            "project_stack bootstrap has a healthy local stack)"
        )
        return 0
    if os.environ.get("GEONODE_INTEGRATION") != "1":
        print("FULL-STACK CHECK: FAILED (--enabled requires GEONODE_INTEGRATION=1)", file=sys.stderr)
        return 2

    state_error = _preserved_state_error(
        geonode_env_file=geonode_env_file,
        runtime_root=runtime_root,
        deployment_revision_file=deployment_revision_file,
        expected_revision=expected_revision,
    )
    if state_error:
        print(f"FULL-STACK CHECK: FAILED ({state_error})", file=sys.stderr)
        return 2
    assert geonode_env_file is not None
    assert runtime_root is not None

    commands = (
        [sys.executable, "-m", "pytest", "-q", FOUNDATION_TEST],
        [sys.executable, E2E_VERIFIER, "--runtime-root", str(runtime_root)],
    )
    command_environment = os.environ | {"GEONODE_ENV_FILE": str(geonode_env_file)}
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=False, env=command_environment)
        if result.returncode:
            print(f"FULL-STACK CHECK: FAILED ({' '.join(command)})", file=sys.stderr)
            return result.returncode
    print("FULL-STACK CHECK: PASSED (foundation and end-to-end verifier ran)")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run explicitly opted-in full-stack validation")
    parser.add_argument(
        "--enabled",
        action="store_true",
        help="confirm that a deliberately bootstrapped local stack may be probed",
    )
    parser.add_argument(
        "--geonode-env-file",
        type=Path,
        help="preserved absolute GeoNode credential file outside the Actions checkout",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        help="preserved absolute generated-runtime directory outside the Actions checkout",
    )
    parser.add_argument(
        "--deployment-revision-file",
        type=Path,
        help="preserved absolute file containing the revision used to prepare the stack",
    )
    parser.add_argument(
        "--expected-revision",
        help="revision expected by this verifier run (the dispatched GitHub SHA in CI)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(
        run_full_stack_checks(
            enabled=args.enabled,
            geonode_env_file=args.geonode_env_file,
            runtime_root=args.runtime_root,
            deployment_revision_file=args.deployment_revision_file,
            expected_revision=args.expected_revision,
        )
    )


if __name__ == "__main__":
    main()
