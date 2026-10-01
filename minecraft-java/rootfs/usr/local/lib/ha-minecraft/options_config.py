#!/usr/bin/env python3
import json
import sys
from pathlib import Path


def read_json(path):
    source = Path(path)
    if not source.is_file():
        return {}
    with source.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"{source} must contain a JSON object")
    return data


def read_yaml(path):
    source = Path(path)
    if not source.is_file():
        return {}
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    data = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        index += 1
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith((" ", "\t")) or ":" not in line:
            raise SystemExit(f"{source} only supports top-level key: value options")

        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key:
            raise SystemExit(f"{source} contains an empty YAML key")

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


def write_yaml(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for key, value in data.items():
            handle.write(format_yaml_pair(key, value))


def format_yaml_pair(key, value):
    if value is True:
        return f"{key}: true\n"
    if value is False:
        return f"{key}: false\n"
    if value is None:
        return f"{key}: null\n"
    if isinstance(value, int):
        return f"{key}: {value}\n"
    text = str(value)
    if "\n" in text:
        indented = "\n".join(f"  {line}" for line in text.splitlines())
        return f"{key}: |\n{indented}\n"
    return f"{key}: {json.dumps(text, ensure_ascii=False)}\n"


def write_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def should_restore_from_persistent(supervisor, persistent):
    return (
        bool(persistent)
        and (supervisor.get("eula") is False or supervisor_looks_like_packaged_defaults(supervisor))
        and not config_looks_like_packaged_defaults(persistent)
    )


PACKAGED_DEFAULTS = {
    "eula": False,
    "server_autostart": False,
    "server_type": "PAPER",
    "minecraft_version": "LATEST",
    "memory_initial": "2G",
    "memory_max": "6G",
    "server_name": "Familienserver",
    "server_owner": "",
    "motd": "Willkommen auf unserem Minecraft-Server",
    "world_slot": "CUSTOM",
    "world_1_name": "world",
    "world_1_server_name": "",
    "world_1_owner": "",
    "world_2_name": "creative",
    "world_2_server_name": "",
    "world_2_owner": "",
    "world_3_name": "test",
    "world_3_server_name": "",
    "world_3_owner": "",
    "level_name": "world",
    "allow_create_worlds": True,
    "gamemode": "survival",
    "difficulty": "normal",
    "max_players": 10,
    "block_disruptive_actions_when_players_online": True,
    "enable_autopause": False,
    "idle_shutdown_minutes": 30,
    "autopause_timeout_seconds": 1800,
    "white_list_enabled": False,
    "whitelist": "",
    "ops": "",
    "bedrock_enabled": False,
    "bedrock_port": 19132,
    "security_enabled": True,
    "anti_xray_enabled": True,
    "anti_xray_engine_mode": 2,
    "anti_xray_manage_defaults": True,
    "anti_xray_manage_nether": True,
    "anti_xray_manage_end": False,
    "grim_enabled": True,
    "grim_auto_update": True,
    "grim_auto_compatible_beta": True,
    "grim_update_channel": "stable",
    "grim_punishments_managed": True,
    "grim_public_alerts": True,
    "grim_public_alert_threshold": 8,
    "grim_public_alert_interval": 8,
    "grim_kick_enabled": True,
    "grim_kick_threshold": 15,
    "grim_event_log_enabled": True,
    "grim_event_log_max_entries": 500,
    "xray_suspicion_enabled": True,
    "xray_suspicion_alert_threshold": 120,
    "xray_suspicion_window_minutes": 20,
    "xray_suspicion_rcon_alerts": True,
    "xray_suspicion_alert_cooldown_seconds": 300,
    "xray_suspicion_rcon_save_before_scan": True,
    "plugins_enabled": False,
    "plugin_preset": "MANUAL",
    "modrinth_plugins": "",
    "plugin_urls": "",
    "plugin_browser_enabled": True,
    "ingress_admin_password": "",
    "ingress_admin_users": "",
    "mqtt_enabled": False,
    "mqtt_host": "core-mosquitto",
    "mqtt_port": 1883,
    "mqtt_username": "",
    "mqtt_password": "",
    "mqtt_discovery_prefix": "homeassistant",
    "mqtt_topic_prefix": "minecraft_java",
    "mqtt_status_interval": 30,
    "playit_enabled": False,
    "playit_secret": "",
    "playit_public_address": "",
    "enable_rcon": False,
    "rcon_password": "",
    "rcon_message_stop_announcement": True,
    "rcon_message_stop_save": True,
    "rcon_message_backup_save": True,
    "enable_auto_backup": False,
    "backup_on_shutdown": False,
    "backup_on_world_switch": False,
    "backup_on_start": False,
    "backup_interval_hours": 6,
    "backup_retention_days": 14,
    "backup_min_interval_minutes": 30,
    "backup_keep_last": 10,
}


def config_looks_like_packaged_defaults(data):
    if not data:
        return True
    matches = sum(1 for key, value in PACKAGED_DEFAULTS.items() if data.get(key) == value)
    return matches >= 8


def supervisor_looks_like_packaged_defaults(supervisor):
    return config_looks_like_packaged_defaults(supervisor)


def supervisor_is_empty_or_exact_packaged_defaults(supervisor):
    if not supervisor:
        return True
    for key, value in supervisor.items():
        if key in PACKAGED_DEFAULTS and value != PACKAGED_DEFAULTS[key]:
            return False
    return True


ALLOWED_COMMAND_KEYS = {"world_slot"}
SUPERVISOR_OVERRIDE_KEYS = {
    "bedrock_enabled",
    "bedrock_port",
    "security_enabled",
    "anti_xray_enabled",
    "anti_xray_engine_mode",
    "anti_xray_manage_defaults",
    "anti_xray_manage_nether",
    "anti_xray_manage_end",
    "grim_enabled",
    "grim_auto_update",
    "grim_auto_compatible_beta",
    "grim_update_channel",
    "grim_punishments_managed",
    "grim_public_alerts",
    "grim_public_alert_threshold",
    "grim_public_alert_interval",
    "grim_kick_enabled",
    "grim_kick_threshold",
    "grim_event_log_enabled",
    "grim_event_log_max_entries",
    "xray_suspicion_enabled",
    "xray_suspicion_alert_threshold",
    "xray_suspicion_window_minutes",
    "xray_suspicion_rcon_alerts",
    "xray_suspicion_alert_cooldown_seconds",
    "xray_suspicion_rcon_save_before_scan",
}


def merge_persistent_defaults(supervisor, persistent):
    effective = supervisor.copy()
    for key, value in persistent.items():
        if value is None:
            continue
        if key not in supervisor:
            effective[key] = value
            continue
        default = PACKAGED_DEFAULTS.get(key, object())
        if supervisor.get(key) == default and value != default:
            effective[key] = value
    return effective


def merge_supervisor_overrides(supervisor, persistent):
    effective = with_packaged_defaults(persistent)
    if supervisor_is_empty_or_exact_packaged_defaults(supervisor):
        return effective
    for key in SUPERVISOR_OVERRIDE_KEYS:
        if key in supervisor and supervisor[key] is not None:
            effective[key] = supervisor[key]
    return effective


def clean_values(data):
    return {key: value for key, value in data.items() if value is not None}


def with_packaged_defaults(data):
    effective = PACKAGED_DEFAULTS.copy()
    effective.update(clean_values(data))
    return effective


def options_equal(left, right):
    return {key: value for key, value in left.items() if value is not None} == {
        key: value for key, value in right.items() if value is not None
    }


def read_command(path):
    command = read_yaml(path)
    if not command:
        return {}
    filtered = {key: value for key, value in command.items() if key in ALLOWED_COMMAND_KEYS and value is not None}
    slot = filtered.get("world_slot")
    if slot and slot not in {"CUSTOM", "WORLD_1", "WORLD_2", "WORLD_3"}:
        raise SystemExit(f"Invalid world_slot in command file: {slot}")
    return filtered


def main():
    if len(sys.argv) not in {5, 6}:
        raise SystemExit("usage: options_config.py SUPERVISOR_JSON PERSISTENT_YAML EFFECTIVE_JSON MODE [COMMAND_YAML]")

    supervisor_json, persistent_yaml, effective_json, mode = sys.argv[1:5]
    command_yaml = sys.argv[5] if len(sys.argv) == 6 else ""
    supervisor = read_json(supervisor_json)
    persistent_path = Path(persistent_yaml)

    if mode == "mirror-supervisor":
        write_yaml(persistent_path, supervisor)
        write_json(effective_json, supervisor)
        print("mirrored")
        return

    if mode == "load-persistent":
        persistent = read_yaml(persistent_yaml)
        if persistent:
            effective = merge_supervisor_overrides(supervisor, persistent)
            print("persistent")
        else:
            effective = with_packaged_defaults(supervisor)
            print("seeded")
        write_yaml(persistent_path, effective)
        write_json(effective_json, effective)
        return

    if mode == "restore-if-supervisor-default":
        persistent = read_yaml(persistent_yaml)
        if should_restore_from_persistent(supervisor, persistent):
            effective = supervisor.copy()
            effective.update({key: value for key, value in persistent.items() if value is not None})
            write_yaml(persistent_path, effective)
            write_json(effective_json, effective)
            print("restored")
        else:
            effective = merge_persistent_defaults(supervisor, persistent)
            write_yaml(persistent_path, effective)
            write_json(effective_json, effective)
            print("mirrored" if options_equal(effective, supervisor) else "merged")
        return

    if mode == "apply-dashboard-command":
        command = read_command(command_yaml)
        persistent = read_yaml(persistent_yaml)
        effective = with_packaged_defaults(persistent or supervisor)
        if command:
            effective.update(command)
            write_yaml(persistent_path, effective)
            write_json(effective_json, effective)
            Path(command_yaml).unlink(missing_ok=True)
            print("command-applied")
        else:
            write_yaml(persistent_path, effective)
            write_json(effective_json, effective)
            print("no-command")
        return

    if mode == "create-yaml-if-missing" and not persistent_path.exists():
        write_yaml(persistent_path, supervisor)

    persistent = read_yaml(persistent_yaml)
    effective = supervisor.copy()
    effective.update({key: value for key, value in persistent.items() if value is not None})
    write_json(effective_json, effective)


if __name__ == "__main__":
    main()
