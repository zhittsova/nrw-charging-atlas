from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_simple_region_config(path: Path = ROOT / "config" / "regions" / "nrw.yml") -> dict[str, object]:
    config: dict[str, object] = {}
    current_key: str | None = None
    current_list: list[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and current_key:
            current_list.append(line.replace("  - ", "", 1).strip())
            config[current_key] = current_list
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        current_list = []
        if value == "":
            config[key] = current_list
        elif value.startswith("[") and value.endswith("]"):
            config[key] = [float(item.strip()) for item in value.strip("[]").split(",")]
        elif value.startswith('"') and value.endswith('"'):
            config[key] = value.strip('"')
        elif value.isdigit():
            config[key] = int(value)
        else:
            try:
                config[key] = float(value)
            except ValueError:
                config[key] = value

    return config
