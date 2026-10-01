#!/usr/bin/env python3
from pathlib import Path
import re

try:
    import yaml
except ImportError:  # pragma: no cover - exercised on minimal local systems
    yaml = None

root = Path(__file__).resolve().parents[1]
config_text = (root / "minecraft-java" / "config.yaml").read_text()


def fallback_load_config(text):
    top_level = set()
    options = {}
    schema = {}
    section = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^[A-Za-z0-9_]+:", line):
            key = line.split(":", 1)[0]
            top_level.add(key)
            section = key if key in {"options", "schema"} else None
            continue
        if section and line.startswith("  ") and ":" in line:
            key, value = line.strip().split(":", 1)
            if section == "options":
                options[key] = value.strip()
            else:
                schema[key] = value.strip()
    return {"__top_level__": top_level, "options": options, "schema": schema}


if yaml:
    config = yaml.safe_load(config_text)
else:
    config = fallback_load_config(config_text)

required = {
    "name",
    "version",
    "slug",
    "arch",
    "options",
    "schema",
    "ports",
    "map",
}
missing = required - set(config)
if "__top_level__" in config:
    missing = required - config["__top_level__"]
if missing:
    raise SystemExit(f"Missing required config keys: {sorted(missing)}")

options = config["options"]
schema = config["schema"]
for key in options:
    if key not in schema:
        raise SystemExit(f"Option {key} has no schema entry")

if options["eula"] not in (False, "false"):
    raise SystemExit("Default eula must be false")
arch_contains_armv7 = "armv7" in config.get("arch", []) if "__top_level__" not in config else re.search(r"(?m)^\s*-\s*armv7\s*$", config_text)
if arch_contains_armv7:
    raise SystemExit("armv7 must not be listed without explicit support")

print("config.yaml smoke check passed")
