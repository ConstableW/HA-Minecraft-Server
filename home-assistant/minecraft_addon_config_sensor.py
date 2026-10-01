#!/usr/bin/env python3
import json
import os
from pathlib import Path


CONFIG_CANDIDATES = [Path(os.environ["MINECRAFT_ADDON_CONFIG"])] if os.environ.get("MINECRAFT_ADDON_CONFIG") else [
    Path("/media/minecraft-java/addon-options.yaml"),
    Path("/addon_configs/local_minecraft_java/options.yaml"),
    Path("/addon_configs/minecraft_java/options.yaml"),
    Path("/config/addons_config/minecraft_java/options.yaml"),
]


DEFAULTS = {
    "world_slot": "CUSTOM",
    "server_name": "",
    "server_owner": "",
    "level_name": "",
    "world_1_name": "",
    "world_1_server_name": "",
    "world_1_owner": "",
    "world_2_name": "",
    "world_2_server_name": "",
    "world_2_owner": "",
    "world_3_name": "",
    "world_3_server_name": "",
    "world_3_owner": "",
    "bedrock_enabled": False,
    "bedrock_port": 19132,
}


def parse_scalar(value):
    if value == "":
        return ""
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "~"}:
        return None
    if value.startswith(('"', "'")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value.strip("'\"")
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    return value


def read_simple_yaml(path):
    data = {}
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        index += 1
        if not stripped or stripped.startswith("#") or ":" not in line:
            continue
        if line.startswith((" ", "\t")):
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value in {"|", "|-"}:
            block = []
            while index < len(lines):
                next_line = lines[index]
                if next_line and not next_line.startswith((" ", "\t")):
                    break
                block.append(next_line[2:] if next_line.startswith("  ") else next_line.lstrip("\t"))
                index += 1
            data[key] = "\n".join(block).rstrip("\n")
        else:
            data[key] = parse_scalar(value)
    return data


def first_existing_config():
    for candidate in CONFIG_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def main():
    config_path = first_existing_config()
    data = DEFAULTS.copy()
    if config_path:
        data.update({k: v for k, v in read_simple_yaml(config_path).items() if v is not None})

    active_slot = str(data.get("world_slot") or "CUSTOM")
    if active_slot == "WORLD_1":
        active_world = data.get("world_1_name") or ""
        active_server = data.get("world_1_server_name") or data.get("server_name") or ""
        active_owner = data.get("world_1_owner") or data.get("server_owner") or ""
    elif active_slot == "WORLD_2":
        active_world = data.get("world_2_name") or ""
        active_server = data.get("world_2_server_name") or data.get("server_name") or ""
        active_owner = data.get("world_2_owner") or data.get("server_owner") or ""
    elif active_slot == "WORLD_3":
        active_world = data.get("world_3_name") or ""
        active_server = data.get("world_3_server_name") or data.get("server_name") or ""
        active_owner = data.get("world_3_owner") or data.get("server_owner") or ""
    else:
        active_world = data.get("level_name") or ""
        active_server = data.get("server_name") or ""
        active_owner = data.get("server_owner") or ""

    data.update(
        {
            "config_path": str(config_path) if config_path else "not found",
            "active_slot": active_slot,
            "active_world": active_world,
            "active_server_name": active_server,
            "active_owner": active_owner,
        }
    )
    print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
