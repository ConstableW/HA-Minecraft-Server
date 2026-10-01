#!/usr/bin/env python3
import html
import hmac
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request
from urllib.parse import parse_qs, quote, unquote, urlparse


DATA_DIR = Path(os.environ.get("MINECRAFT_DATA_DIR", "/media/minecraft-java")).resolve()
SENSOR_MODULE = Path(os.environ.get("MINECRAFT_SENSOR_MODULE", "/usr/local/share/ha-minecraft/home-assistant/minecraft_server_sensor.py"))
OPTIONS_CONFIG_MODULE = Path(os.environ.get("MINECRAFT_OPTIONS_CONFIG_MODULE", "/usr/local/lib/ha-minecraft/options_config.py"))
SECURITY_MODULE = Path(os.environ.get("MINECRAFT_SECURITY_MODULE", "/usr/local/lib/ha-minecraft/security.py"))
COMMAND_DIR = Path(os.environ.get("MINECRAFT_COMMAND_DIR", "/tmp/ha-minecraft-commands"))
STATE_FILE = Path(os.environ.get("MINECRAFT_CONTROL_STATE_FILE", "/tmp/ha-minecraft-control-state"))
PENDING_SLOT_FILE = Path(os.environ.get("MINECRAFT_PENDING_SLOT_FILE", "/tmp/ha-minecraft-pending-slot"))
PLAYIT_STATE_FILE = Path(os.environ.get("MINECRAFT_PLAYIT_STATE_FILE", "/tmp/ha-minecraft-playit-state"))
PLAYIT_ADDRESS_FILE = Path(os.environ.get("MINECRAFT_PLAYIT_ADDRESS_FILE", "/tmp/ha-minecraft-playit-address"))
AUTO_REFRESH_DISABLED_FILE = Path(os.environ.get("MINECRAFT_AUTO_REFRESH_DISABLED_FILE", "/tmp/ha-minecraft-autorefresh-disabled-until"))
OPTIONS_YAML = Path(os.environ.get("PERSISTENT_OPTIONS_YAML", str(DATA_DIR / "addon-options.yaml")))
RUNTIME_OPTIONS_YAML = Path(os.environ.get("RUNTIME_OPTIONS_YAML", "/ha-addon-config/options.yaml"))
EFFECTIVE_OPTIONS_FILE = Path(os.environ.get("EFFECTIVE_OPTIONS_FILE", "/tmp/ha-minecraft-options.json"))
SUPERVISOR_OPTIONS_FILE = Path(os.environ.get("SUPERVISOR_OPTIONS_FILE", "/data/options.json"))
FOLDERS = {
    "plugins": DATA_DIR / "plugins",
    "mods": DATA_DIR / "mods",
}
DISABLED_PLUGINS_DIR = DATA_DIR / "plugins-disabled"
BACKUP_DIR = DATA_DIR / "backups"
MAX_UPLOAD_BYTES = int(os.environ.get("MINECRAFT_PLUGIN_UPLOAD_LIMIT", str(128 * 1024 * 1024)))
VALID_WORLD_SLOTS = {"CUSTOM", "WORLD_1", "WORLD_2", "WORLD_3"}
VALID_GAMEMODES = {"survival", "creative", "adventure", "spectator"}
QUICK_GIVE_ITEMS = (
    ("64 Steak", "minecraft:cooked_beef", 64),
    ("64 Fackeln", "minecraft:torch", 64),
    ("Iron Tools", "minecraft:iron_pickaxe", 1),
    ("Diamond Pickaxe", "minecraft:diamond_pickaxe", 1),
    ("32 Fireworks", "minecraft:firework_rocket", 32),
)
ADMIN_COOKIE_NAME = "minecraft_java_admin"
ADMIN_SESSION_SECONDS = 12 * 60 * 60
HA_ADMIN_HEADERS = (
    "X-Hass-Is-Admin",
    "X-Hass-User-Is-Admin",
    "X-Home-Assistant-Is-Admin",
    "X-Home-Assistant-User-Is-Admin",
    "X-Supervisor-Is-Admin",
)
HA_USER_HEADERS = (
    "X-Hass-User",
    "X-Hass-User-Name",
    "X-Hass-User-ID",
    "X-Hass-Username",
    "X-Hass-User-Email",
    "X-Home-Assistant-User",
    "X-Home-Assistant-User-Name",
    "X-Home-Assistant-User-ID",
    "X-Home-Assistant-Username",
    "X-Home-Assistant-User-Email",
    "X-Forwarded-User",
    "X-Forwarded-Username",
    "X-Forwarded-Preferred-Username",
    "X-Forwarded-Email",
    "X-Auth-Request-User",
    "X-Auth-Request-Preferred-Username",
    "X-Auth-Request-Email",
    "X-Authenticated-User",
    "X-Authenticated-Userid",
    "X-Remote-User",
    "Remote-User",
)
BOOL_FIELDS = {
    "server_autostart",
    "allow_create_worlds",
    "block_disruptive_actions_when_players_online",
    "enable_autopause",
    "bedrock_enabled",
    "security_enabled",
    "anti_xray_enabled",
    "anti_xray_manage_defaults",
    "anti_xray_manage_nether",
    "anti_xray_manage_end",
    "grim_enabled",
    "grim_auto_update",
    "grim_auto_compatible_beta",
    "grim_punishments_managed",
    "grim_public_alerts",
    "grim_kick_enabled",
    "grim_event_log_enabled",
    "xray_suspicion_enabled",
    "xray_suspicion_rcon_alerts",
    "xray_suspicion_rcon_save_before_scan",
    "plugins_enabled",
    "plugin_browser_enabled",
    "mqtt_enabled",
    "playit_enabled",
    "enable_rcon",
    "rcon_message_stop_announcement",
    "rcon_message_stop_save",
    "rcon_message_backup_save",
    "enable_auto_backup",
    "backup_on_shutdown",
    "backup_on_world_switch",
    "backup_on_start",
}
INT_FIELDS = {
    "max_players",
    "idle_shutdown_minutes",
    "autopause_timeout_seconds",
    "bedrock_port",
    "anti_xray_engine_mode",
    "grim_public_alert_threshold",
    "grim_public_alert_interval",
    "grim_kick_threshold",
    "grim_event_log_max_entries",
    "xray_suspicion_alert_threshold",
    "xray_suspicion_window_minutes",
    "xray_suspicion_alert_cooldown_seconds",
    "backup_min_interval_minutes",
    "backup_keep_last",
    "backup_interval_hours",
    "backup_retention_days",
    "mqtt_status_interval",
}
SECRET_FIELDS = {"ingress_admin_password", "rcon_password", "playit_secret"}
CONFIG_FORM_FIELDS = [
    "server_autostart",
    "server_type",
    "minecraft_version",
    "memory_initial",
    "memory_max",
    "server_name",
    "server_owner",
    "motd",
    "level_name",
    "allow_create_worlds",
    "gamemode",
    "difficulty",
    "max_players",
    "block_disruptive_actions_when_players_online",
    "enable_autopause",
    "idle_shutdown_minutes",
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
    "world_1_name",
    "world_1_server_name",
    "world_1_owner",
    "world_2_name",
    "world_2_server_name",
    "world_2_owner",
    "world_3_name",
    "world_3_server_name",
    "world_3_owner",
    "plugins_enabled",
    "plugin_browser_enabled",
    "mqtt_enabled",
    "mqtt_status_interval",
    "playit_enabled",
    "playit_secret",
    "playit_public_address",
    "enable_rcon",
    "rcon_password",
    "rcon_message_stop_announcement",
    "rcon_message_stop_save",
    "rcon_message_backup_save",
    "enable_auto_backup",
    "backup_on_shutdown",
    "backup_on_world_switch",
    "backup_on_start",
    "backup_interval_hours",
    "backup_retention_days",
    "backup_min_interval_minutes",
    "backup_keep_last",
    "ingress_admin_password",
    "ingress_admin_users",
]


def safe_plugin_name(name):
    name = Path(name).name.strip()
    if not name or not name.lower().endswith(".jar"):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._+ -]+\.jar", name):
        return ""
    return name


def folder_key(value):
    return value if value in FOLDERS else "plugins"


def folder_dir(folder):
    return FOLDERS[folder_key(folder)].resolve()


def plugin_path(folder, name):
    safe_name = safe_plugin_name(name)
    if not safe_name:
        return None
    root = folder_dir(folder)
    path = (root / safe_name).resolve()
    if root not in path.parents:
        return None
    return path


def jar_plugin_descriptor(path):
    try:
        with zipfile.ZipFile(path) as jar:
            names = set(jar.namelist())
            descriptor_name = next((item for item in ("plugin.yml", "paper-plugin.yml") if item in names), "")
            if not descriptor_name:
                return {"name": "", "descriptor": "", "valid": None, "warning": "kein plugin.yml"}
            text = jar.read(descriptor_name).decode("utf-8", errors="replace")
            match = re.search(r"(?im)^name:\s*['\"]?([^'\"\r\n#]+)", text)
            plugin_name = match.group(1).strip() if match else ""
            warning = ""
            valid = True
            if not plugin_name:
                valid = False
                warning = "plugin.yml ohne name"
            elif not re.fullmatch(r"[A-Za-z0-9_.-]+", plugin_name):
                valid = False
                warning = "ungueltiger interner Name"
            return {"name": plugin_name, "descriptor": descriptor_name, "valid": valid, "warning": warning}
    except (OSError, zipfile.BadZipFile):
        return {"name": "", "descriptor": "", "valid": False, "warning": "keine gueltige JAR"}


def disable_plugin_jar(name):
    path = plugin_path("plugins", name)
    if not path or not path.is_file():
        raise ValueError("Plugin-JAR nicht gefunden.")
    DISABLED_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    destination = DISABLED_PLUGINS_DIR / path.name
    if destination.exists():
        destination = DISABLED_PLUGINS_DIR / f"{path.stem}-{int(time.time())}{path.suffix}"
    path.replace(destination)
    return destination


def load_sensor_module():
    spec = importlib.util.spec_from_file_location("minecraft_server_sensor", SENSOR_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_options_config_module():
    spec = importlib.util.spec_from_file_location("options_config", OPTIONS_CONFIG_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_security_module():
    spec = importlib.util.spec_from_file_location("security", SECURITY_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_options():
    module = load_options_config_module()
    options = module.PACKAGED_DEFAULTS.copy()
    persisted = module.read_yaml(OPTIONS_YAML)
    runtime = module.read_yaml(RUNTIME_OPTIONS_YAML)
    options.update({key: value for key, value in runtime.items() if value is not None})
    options.update({key: value for key, value in persisted.items() if value is not None})
    return options


def write_options(options):
    module = load_options_config_module()
    cleaned = {key: value for key, value in options.items() if value is not None}
    module.write_yaml(OPTIONS_YAML, cleaned)
    try:
        module.write_yaml(RUNTIME_OPTIONS_YAML, cleaned)
    except OSError as exc:
        print(f"[plugin-browser] could not write runtime options yaml: {exc}", flush=True)
    module.write_json(EFFECTIVE_OPTIONS_FILE, cleaned)
    try:
        module.write_json(SUPERVISOR_OPTIONS_FILE, cleaned)
    except OSError as exc:
        print(f"[plugin-browser] could not write local supervisor options json: {exc}", flush=True)
    update_supervisor_options(cleaned)


def update_supervisor_options(options):
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
    if not token:
        return
    body = json.dumps({"options": options}, ensure_ascii=False).encode("utf-8")
    endpoints = [
        "http://supervisor/addons/self/options",
        "http://supervisor/addons/local_minecraft_java/options",
    ]
    for endpoint in endpoints:
        req = request.Request(
            endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=5) as response:
                if 200 <= response.status < 300:
                    print(f"[plugin-browser] updated supervisor options through {endpoint}", flush=True)
                    return
        except (OSError, error.URLError, error.HTTPError) as exc:
            print(f"[plugin-browser] supervisor options update failed at {endpoint}: {exc}", flush=True)


def parse_urlencoded_form(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    if length > 1024 * 1024:
        handler.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        return None
    body = handler.rfile.read(length).decode("utf-8", errors="replace")
    return {key: values[-1] for key, values in parse_qs(body, keep_blank_values=True).items()}


def coerce_config_updates(form):
    updates = {}
    field_names = CONFIG_FORM_FIELDS
    if str(form.get("config_keys") or "").strip():
        requested = [item.strip() for item in str(form.get("config_keys") or "").split(",") if item.strip()]
        allowed = set(CONFIG_FORM_FIELDS)
        field_names = [item for item in requested if item in allowed]
    for key in field_names:
        if key in BOOL_FIELDS:
            if key in form:
                updates[key] = str(form.get(key)).strip().lower() not in {"0", "false", "off", "no", ""}
            else:
                updates[key] = False
            continue
        if key not in form:
            continue
        if key in SECRET_FIELDS:
            if not str(form.get(key, "")).strip():
                continue
        raw_value = form.get(key, "")
        if key in INT_FIELDS:
            try:
                updates[key] = int(raw_value)
            except (TypeError, ValueError):
                updates[key] = 0
            continue
        updates[key] = str(raw_value).strip()

    if "server_type" in updates and updates.get("server_type") not in {"PAPER", "PURPUR", "VANILLA"}:
        updates["server_type"] = "PAPER"
    if "gamemode" in updates and updates.get("gamemode") not in {"survival", "creative", "adventure", "spectator"}:
        updates["gamemode"] = "survival"
    if "difficulty" in updates and updates.get("difficulty") not in {"peaceful", "easy", "normal", "hard"}:
        updates["difficulty"] = "normal"
    if "grim_update_channel" in updates and updates.get("grim_update_channel") not in {"stable", "beta"}:
        updates["grim_update_channel"] = "stable"
    if "max_players" in updates:
        updates["max_players"] = min(max(int(updates["max_players"]), 1), 100)
    if "idle_shutdown_minutes" in updates:
        updates["idle_shutdown_minutes"] = min(max(int(updates["idle_shutdown_minutes"]), 1), 1440)
    if "bedrock_port" in updates:
        updates["bedrock_port"] = min(max(int(updates["bedrock_port"]), 1), 65535)
    if "anti_xray_engine_mode" in updates:
        updates["anti_xray_engine_mode"] = min(max(int(updates["anti_xray_engine_mode"]), 1), 3)
    if "grim_public_alert_threshold" in updates:
        updates["grim_public_alert_threshold"] = max(int(updates["grim_public_alert_threshold"]), 1)
    if "grim_public_alert_interval" in updates:
        updates["grim_public_alert_interval"] = max(int(updates["grim_public_alert_interval"]), 1)
    if "grim_kick_threshold" in updates:
        updates["grim_kick_threshold"] = max(int(updates["grim_kick_threshold"]), 1)
    if "grim_event_log_max_entries" in updates:
        updates["grim_event_log_max_entries"] = min(max(int(updates["grim_event_log_max_entries"]), 50), 5000)
    if "xray_suspicion_alert_threshold" in updates:
        updates["xray_suspicion_alert_threshold"] = min(max(int(updates["xray_suspicion_alert_threshold"]), 10), 500)
    if "xray_suspicion_window_minutes" in updates:
        updates["xray_suspicion_window_minutes"] = min(max(int(updates["xray_suspicion_window_minutes"]), 5), 240)
    if "xray_suspicion_alert_cooldown_seconds" in updates:
        updates["xray_suspicion_alert_cooldown_seconds"] = min(max(int(updates["xray_suspicion_alert_cooldown_seconds"]), 30), 3600)
    if "backup_interval_hours" in updates:
        updates["backup_interval_hours"] = min(max(int(updates["backup_interval_hours"]), 1), 168)
    if "backup_retention_days" in updates:
        updates["backup_retention_days"] = min(max(int(updates["backup_retention_days"]), 1), 365)
    if "backup_min_interval_minutes" in updates:
        updates["backup_min_interval_minutes"] = min(max(int(updates["backup_min_interval_minutes"]), 0), 1440)
    if "backup_keep_last" in updates:
        updates["backup_keep_last"] = min(max(int(updates["backup_keep_last"]), 0), 500)
    if "mqtt_status_interval" in updates:
        updates["mqtt_status_interval"] = min(max(int(updates["mqtt_status_interval"]), 5), 3600)
    return updates


def admin_password(options):
    return str(options.get("ingress_admin_password") or "")


def admin_user_allowlist(options):
    raw = str(options.get("ingress_admin_users") or "")
    entries = re.split(r"[\n,]+", raw)
    return {entry.strip().lower() for entry in entries if entry.strip()}


def cookie_values(header):
    values = {}
    for part in str(header or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        values[key] = value
    return values


def admin_signature(password, timestamp, nonce):
    payload = f"{timestamp}:{nonce}".encode("utf-8")
    return hmac.new(password.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def make_admin_cookie(password):
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(12)
    signature = admin_signature(password, timestamp, nonce)
    return f"{timestamp}:{nonce}:{signature}"


def valid_admin_cookie(password, value):
    if not password or not value:
        return False
    parts = value.split(":", 2)
    if len(parts) != 3:
        return False
    timestamp, nonce, signature = parts
    try:
        age = time.time() - int(timestamp)
    except ValueError:
        return False
    if age < 0 or age > ADMIN_SESSION_SECONDS:
        return False
    expected = admin_signature(password, timestamp, nonce)
    return hmac.compare_digest(expected, signature)


def set_cookie_header(value, max_age=ADMIN_SESSION_SECONDS):
    return f"{ADMIN_COOKIE_NAME}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"


def truthy_header(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "admin"}


def forwarded_user_values(headers):
    values = []
    seen = set()
    for header in HA_USER_HEADERS:
        value = str(headers.get(header) or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        values.append((header, value))
    return values


def forwarded_user_summary(headers):
    values = forwarded_user_values(headers)
    if not values:
        return "keiner"
    return ", ".join(f"{name}={value}" for name, value in values)


def matching_admin_user(headers, options):
    allowed = admin_user_allowlist(options)
    if not allowed:
        return ""
    for _header, value in forwarded_user_values(headers):
        if value and value.lower() in allowed:
            return value
    return ""


def safe_player_name(name):
    name = str(name or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_.]{1,32}", name):
        return name
    return ""


def safe_item_id(value):
    item = str(value or "").strip().lower()
    if not item:
        return ""
    if ":" not in item:
        item = f"minecraft:{item}"
    if re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_./-]+", item):
        return item
    return ""


def safe_amount(value, default=1, maximum=2304):
    try:
        amount = int(str(value or "").strip())
    except ValueError:
        amount = default
    return min(max(amount, 1), maximum)


def safe_broadcast_message(value):
    message = re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()
    return message[:160]


def rcon_settings(options):
    enabled = bool(options.get("enable_rcon"))
    password = str(options.get("rcon_password") or "")
    port = int(options.get("rcon_port") or 25575)
    return enabled, password, port


def rcon_available(options):
    enabled, password, _port = rcon_settings(options)
    return enabled and bool(password)


def rcon_command(options, command, timeout=5):
    enabled, password, port = rcon_settings(options)
    if not enabled:
        raise RuntimeError("RCON ist deaktiviert.")
    if not password:
        raise RuntimeError("RCON-Passwort fehlt.")
    result = subprocess.run(
        ["rcon-cli", "--host", "127.0.0.1", "--port", str(port), "--password", password, command],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        raise RuntimeError(output or "RCON-Befehl fehlgeschlagen.")
    return output


def parse_rcon_list(output):
    if ":" not in output:
        return []
    names = output.split(":", 1)[1].strip()
    if not names:
        return []
    return [name for name in (safe_player_name(part) for part in names.split(",")) if name]


def online_players(options, log_errors=False):
    if not rcon_available(options):
        return []
    try:
        return parse_rcon_list(rcon_command(options, "list"))
    except Exception as exc:
        if log_errors:
            print(f"[plugin-browser] could not read online players through rcon: {exc}", flush=True)
        return []


def banned_players():
    path = DATA_DIR / "banned-players.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    names = []
    for entry in data:
        if isinstance(entry, dict):
            name = safe_player_name(entry.get("name"))
            if name:
                names.append(name)
    return sorted(set(names), key=str.lower)


def op_players():
    path = DATA_DIR / "ops.json"
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    names = set()
    for entry in data:
        if isinstance(entry, dict):
            name = safe_player_name(entry.get("name"))
            if name:
                names.add(name.lower())
    return names


def resolve_slot_value(config, slot, suffix, fallback_key=""):
    if slot == "WORLD_1":
        value = config.get(f"world_1_{suffix}") or ""
    elif slot == "WORLD_2":
        value = config.get(f"world_2_{suffix}") or ""
    elif slot == "WORLD_3":
        value = config.get(f"world_3_{suffix}") or ""
    else:
        value = ""
    if value:
        return value
    return config.get(fallback_key, "") if fallback_key else ""


def read_pending_slot():
    try:
        slot = PENDING_SLOT_FILE.read_text(encoding="utf-8").strip().upper()
    except OSError:
        return ""
    return slot if slot in VALID_WORLD_SLOTS else ""


def write_pending_slot(slot):
    slot = str(slot or "").strip().upper()
    if slot not in VALID_WORLD_SLOTS:
        return
    PENDING_SLOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_SLOT_FILE.with_name(f".{PENDING_SLOT_FILE.name}.{os.getpid()}.tmp")
    tmp.write_text(slot, encoding="utf-8")
    tmp.replace(PENDING_SLOT_FILE)


def clear_pending_slot():
    try:
        PENDING_SLOT_FILE.unlink()
    except OSError:
        pass


def status_payload():
    try:
        module = load_sensor_module()
        config_path, config = module.first_config()
        control_state = read_control_state()
        active_slot = str(config.get("world_slot") or "CUSTOM")
        active_world = module.resolve_active_world(config)
        data = {
            "online": False,
            "control_state": control_state,
            "playit_state": read_playit_state(),
            "playit_address": read_playit_address(),
            "active_slot": active_slot,
            "active_world": active_world,
            "active_server_name": resolve_slot_value(config, active_slot, "server_name", "server_name"),
            "active_owner": resolve_slot_value(config, active_slot, "owner", "server_owner"),
            "world_count": len(module.list_worlds()),
            "plugin_count": module.directory_count(DATA_DIR / "plugins", "*.jar"),
            "mod_count": module.directory_count(DATA_DIR / "mods", "*.jar"),
            "server_type": config.get("server_type", ""),
            "configured_version": config.get("minecraft_version", ""),
            "config_path": str(config_path) if config_path else "not found",
            "world_1_name": config.get("world_1_name", ""),
            "world_2_name": config.get("world_2_name", ""),
            "world_3_name": config.get("world_3_name", ""),
        }
        data.update(module.backup_stats())
        try:
            port = int(config.get("server_port") or os.environ.get("MINECRAFT_PORT", 25565))
            data.update(module.minecraft_status("127.0.0.1", port))
        except Exception as exc:
            data["error"] = str(exc)
        if hasattr(module, "read_bedrock_status"):
            data.update(module.read_bedrock_status(config))
        try:
            security = load_security_module()
            data["security"] = security.get_security_status(config, security.data_paths(DATA_DIR))
        except Exception as exc:
            data["security"] = {"enabled": bool(config.get("security_enabled", True)), "healthy": False, "warnings": [], "error": str(exc)}
        if data.get("online") and getattr(module, "player_model", None):
            data.update(module.player_model.load_players(module.DATA_DIR, config, data.get("player_sample", "")))
        pending_slot = read_pending_slot()
        if pending_slot:
            if pending_slot == active_slot:
                clear_pending_slot()
            else:
                data["pending_slot"] = pending_slot
                data["active_slot"] = pending_slot
                data["active_world"] = resolve_slot_value(config, pending_slot, "name", "level_name")
                data["active_server_name"] = resolve_slot_value(config, pending_slot, "server_name", "server_name")
                data["active_owner"] = resolve_slot_value(config, pending_slot, "owner", "server_owner")
        if data.get("online"):
            data["control_state"] = "running"
        return data
    except Exception as exc:
        return {"online": False, "control_state": read_control_state(), "playit_state": read_playit_state(), "playit_address": read_playit_address(), "error": str(exc), "players_online": 0, "players_max": 0}


def read_json_file(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return data
    except (OSError, json.JSONDecodeError):
        return default


def format_duration_ticks(ticks):
    seconds = int(ticks or 0) // 20
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def stats_dirs_for_players(data_dir):
    root = Path(data_dir)
    candidates = [root / "stats", root / "players" / "stats"]
    try:
        for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if child.is_dir():
                candidates.append(child / "stats")
                candidates.append(child / "players" / "stats")
    except OSError:
        pass
    seen = set()
    result = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_dir():
            result.append(candidate)
    return result


def mined_stats_from_file(path):
    data = read_json_file(path, {})
    stats = data.get("stats") if isinstance(data, dict) else {}
    if not isinstance(stats, dict):
        return {}, 0
    mined = stats.get("minecraft:mined") or stats.get("minecraft:mine_block") or {}
    custom = stats.get("minecraft:custom") or {}
    play_time = 0
    if isinstance(custom, dict):
        play_time = int(custom.get("minecraft:play_time") or custom.get("minecraft:total_world_time") or 0)
    return mined if isinstance(mined, dict) else {}, play_time


def parse_latest_player_log():
    latest = DATA_DIR / "logs" / "latest.log"
    if not latest.is_file():
        return {}
    try:
        with latest.open("rb") as handle:
            if latest.stat().st_size > 700_000:
                handle.seek(-700_000, os.SEEK_END)
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    info = {}
    join_patterns = [
        re.compile(r"\]: (?P<player>[A-Za-z0-9_.]{1,32}) joined the game"),
        re.compile(r"\]: (?P<player>[A-Za-z0-9_.]{1,32})\[/[^\]]+\] logged in"),
    ]
    client_patterns = [
        re.compile(r"\[ViaVersion\].*?(?P<player>[A-Za-z0-9_.]{1,32}).*?(?:client|protocol).*?(?P<version>\d+(?:\.\d+){0,2}|protocol\s*\d+)", re.IGNORECASE),
        re.compile(r"(?P<player>[A-Za-z0-9_.]{1,32}).*?(?:joined|connected).*?(?:client|version|protocol).*?(?P<version>\d+(?:\.\d+){0,2}|protocol\s*\d+)", re.IGNORECASE),
    ]
    for line in text.splitlines():
        for pattern in join_patterns:
            match = pattern.search(line)
            if match:
                player = match.group("player")
                info.setdefault(player, {})["last_logon"] = line[:10] if re.match(r"\[\d\d:\d\d:\d\d", line) else "im latest.log"
        for pattern in client_patterns:
            match = pattern.search(line)
            if match:
                player = match.group("player")
                info.setdefault(player, {})["client"] = match.group("version")
    return info


def player_intelligence(status, options):
    online_names = {str(player.get("server_name") or player.get("name") or "") for player in status.get("players") or [] if isinstance(player, dict)}
    banned = {
        str(item.get("name") or "")
        for item in read_json_file(DATA_DIR / "banned-players.json", [])
        if isinstance(item, dict) and item.get("name")
    }
    security = load_security_module()
    paths = security.data_paths(DATA_DIR)
    uuid_names = security.player_names_by_uuid(paths)
    events = security.get_security_events(paths, limit=5000)
    xray_state = security.read_xray_state(paths)
    log_info = parse_latest_player_log()
    players = {}

    def ensure(name, uuid_value=""):
        safe = str(name or "").strip()
        if not safe:
            return None
        entry = players.setdefault(
            safe,
            {
                "name": safe,
                "uuid": uuid_value,
                "status": "offline",
                "last_logon": "",
                "client": "",
                "play_time_ticks": 0,
                "mined": {},
                "mined_total": 0,
                "events": [],
                "checks": {},
                "current_security_score": 0,
                "max_security_score": 0,
            },
        )
        if uuid_value and not entry.get("uuid"):
            entry["uuid"] = uuid_value
        return entry

    for item in read_json_file(DATA_DIR / "usercache.json", []):
        if isinstance(item, dict):
            entry = ensure(item.get("name"), item.get("uuid", ""))
            if entry and item.get("expiresOn"):
                entry["last_logon"] = str(item.get("expiresOn"))
    for name in online_names:
        entry = ensure(name)
        if entry:
            entry["status"] = "online"
    for name in banned:
        entry = ensure(name)
        if entry:
            entry["status"] = "banned" if entry.get("status") != "online" else "online/banned"

    for stats_dir in stats_dirs_for_players(DATA_DIR):
        for stat_file in sorted(stats_dir.glob("*.json"), key=lambda item: item.name.lower()):
            uuid_key = stat_file.stem.lower()
            name = uuid_names.get(uuid_key) or uuid_names.get(uuid_key.replace("-", "")) or uuid_key[:12]
            entry = ensure(name, uuid_key)
            if not entry:
                continue
            mined, play_time = mined_stats_from_file(stat_file)
            entry["play_time_ticks"] += play_time
            for block, count in mined.items():
                try:
                    entry["mined"][block] = entry["mined"].get(block, 0) + int(count)
                except (TypeError, ValueError):
                    continue

    max_score_resets = xray_state.get("max_score_resets") if isinstance(xray_state.get("max_score_resets"), dict) else {}
    for event in events:
        player = str(event.get("player") or "").strip()
        if not player:
            continue
        entry = ensure(player)
        if not entry:
            continue
        event_epoch = 0.0
        try:
            event_epoch = time.mktime(time.strptime(str(event.get("timestamp") or ""), "%Y-%m-%dT%H:%M:%SZ"))
        except (TypeError, ValueError):
            pass
        score = int(event.get("violation_level") or 0)
        if event_epoch >= float(max_score_resets.get(player) or 0):
            entry["max_security_score"] = max(entry["max_security_score"], score)
        check = str(event.get("check") or event.get("category") or "Unbekannt")
        entry["checks"][check] = entry["checks"].get(check, 0) + 1
        if len(entry["events"]) < 20:
            entry["events"].append(event)

    for name, payload in (xray_state.get("players") or {}).items():
        if not isinstance(payload, dict):
            continue
        entry = ensure(name)
        if entry:
            current_score = int(payload.get("score") or 0)
            entry["current_security_score"] = current_score
            last_seen_epoch = 0.0
            try:
                last_seen_epoch = time.mktime(time.strptime(str(payload.get("last_seen") or ""), "%Y-%m-%dT%H:%M:%SZ"))
            except (TypeError, ValueError):
                last_seen_epoch = float(payload.get("last_alert") or 0)
            if last_seen_epoch >= float(max_score_resets.get(name) or 0):
                entry["max_security_score"] = max(entry["max_security_score"], current_score)

    for name, info in log_info.items():
        entry = ensure(name)
        if entry:
            entry["last_logon"] = info.get("last_logon") or entry.get("last_logon")
            entry["client"] = info.get("client") or entry.get("client")

    for entry in players.values():
        entry["mined_total"] = sum(int(value or 0) for value in entry["mined"].values())
        entry["top_mined"] = sorted(entry["mined"].items(), key=lambda item: int(item[1] or 0), reverse=True)[:12]
        entry["top_checks"] = sorted(entry["checks"].items(), key=lambda item: int(item[1] or 0), reverse=True)[:8]
        entry["play_time"] = format_duration_ticks(entry["play_time_ticks"])
    return sorted(players.values(), key=lambda item: (item["status"] != "online", item["status"] != "banned", item["name"].lower()))


def sorted_player_intelligence(status, options, sort_key="name", direction="asc"):
    players = player_intelligence(status, options)
    reverse = str(direction).lower() == "desc"
    key = str(sort_key or "name").lower()
    sorters = {
        "name": lambda item: item.get("name", "").lower(),
        "status": lambda item: item.get("status", ""),
        "last_logon": lambda item: item.get("last_logon", ""),
        "online_time": lambda item: int(item.get("play_time_ticks") or 0),
        "current_score": lambda item: int(item.get("current_security_score") or 0),
        "max_score": lambda item: int(item.get("max_security_score") or 0),
        "mined": lambda item: int(item.get("mined_total") or 0),
    }
    return sorted(players, key=sorters.get(key, sorters["name"]), reverse=reverse)


def read_control_state():
    try:
        state = STATE_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""
    return state if state in {"starting", "running", "stopping", "stopped"} else ""


def write_control_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(f".{STATE_FILE.name}.{os.getpid()}.tmp")
    tmp.write_text(state, encoding="utf-8")
    tmp.replace(STATE_FILE)


def read_playit_state():
    try:
        state = PLAYIT_STATE_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""
    return state if state in {"disabled", "starting", "running", "stopping", "stopped", "error"} else ""


def read_playit_address():
    try:
        return PLAYIT_ADDRESS_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_playit_state(state):
    PLAYIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PLAYIT_STATE_FILE.with_name(f".{PLAYIT_STATE_FILE.name}.{os.getpid()}.tmp")
    tmp.write_text(state, encoding="utf-8")
    tmp.replace(PLAYIT_STATE_FILE)


def read_autorefresh_disabled_until(control_state=""):
    if control_state == "starting":
        clear_autorefresh_disabled()
        return 0
    try:
        until = int(AUTO_REFRESH_DISABLED_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0
    if until <= int(time.time()):
        clear_autorefresh_disabled()
        return 0
    return until


def write_autorefresh_disabled(seconds=300):
    AUTO_REFRESH_DISABLED_FILE.parent.mkdir(parents=True, exist_ok=True)
    until = int(time.time()) + int(seconds)
    tmp = AUTO_REFRESH_DISABLED_FILE.with_name(f".{AUTO_REFRESH_DISABLED_FILE.name}.{os.getpid()}.tmp")
    tmp.write_text(str(until), encoding="utf-8")
    tmp.replace(AUTO_REFRESH_DISABLED_FILE)
    return until


def clear_autorefresh_disabled():
    try:
        AUTO_REFRESH_DISABLED_FILE.unlink()
    except OSError:
        pass


def enqueue_command(action, **payload):
    COMMAND_DIR.mkdir(parents=True, exist_ok=True)
    command = {"action": action, **payload, "created_at": int(time.time() * 1000)}
    name = f"command-{command['created_at']}-{os.getpid()}.json"
    tmp_path = COMMAND_DIR / f".{name}.tmp"
    final_path = COMMAND_DIR / name
    tmp_path.write_text(json.dumps(command, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(final_path)


def safe_backup_name(name):
    name = Path(str(name or "")).name.strip()
    if re.fullmatch(r"minecraft-[A-Za-z0-9T._-]+\.tar\.gz", name):
        return name
    return ""


def backup_path(name):
    safe_name = safe_backup_name(name)
    if not safe_name:
        return None
    path = (BACKUP_DIR / safe_name).resolve()
    if path.parent != BACKUP_DIR.resolve():
        return None
    return path


def backup_sidecar(path, suffix):
    return path.with_name(path.name + suffix)


def read_backup_metadata(path):
    data = read_json_file(backup_sidecar(path, ".json"), {})
    return data if isinstance(data, dict) else {}


def write_backup_metadata(path, payload):
    tmp = backup_sidecar(path, f".json.{os.getpid()}.tmp")
    final = backup_sidecar(path, ".json")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(final)


def list_backups():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = []
    for path in sorted(BACKUP_DIR.glob("minecraft-*.tar.gz"), key=lambda item: item.name.lower(), reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        metadata = read_backup_metadata(path)
        backups.append(
            {
                "name": path.name,
                "size_mb": round(stat.st_size / 1024 / 1024, 1),
                "mtime": int(stat.st_mtime),
                "persistent": backup_sidecar(path, ".persistent").exists(),
                "world": str(metadata.get("world") or infer_backup_world(path.name)),
                "slot": str(metadata.get("slot") or ""),
                "reason": str(metadata.get("reason") or infer_backup_reason(path.name)),
            }
        )
    return backups


def infer_backup_reason(name):
    match = re.match(r"^minecraft-[^.]+\.([A-Za-z0-9_.-]+)\.tar\.gz$", name)
    return match.group(1) if match else ""


def infer_backup_world(name):
    reason = infer_backup_reason(name)
    if reason.startswith("manual-"):
        return reason[7:]
    return ""


def set_backup_persistent(name, enabled=True):
    path = backup_path(name)
    if not path or not path.is_file():
        raise ValueError("Backup nicht gefunden.")
    marker = backup_sidecar(path, ".persistent")
    if enabled:
        marker.write_text(str(int(time.time())), encoding="utf-8")
    else:
        try:
            marker.unlink()
        except OSError:
            pass


def delete_backup(name):
    path = backup_path(name)
    if not path or not path.is_file():
        raise ValueError("Backup nicht gefunden.")
    path.unlink()
    for suffix in (".persistent", ".json"):
        try:
            backup_sidecar(path, suffix).unlink()
        except OSError:
            pass


def parse_multipart(body, boundary):
    marker = b"--" + boundary
    for part in body.split(marker):
        part = part.strip(b"\r\n")
        if not part or part == b"--" or b"\r\n\r\n" not in part:
            continue
        raw_headers, content = part.split(b"\r\n\r\n", 1)
        if content.endswith(b"\r\n"):
            content = content[:-2]
        headers = raw_headers.decode("utf-8", errors="replace")
        disposition = next((line for line in headers.splitlines() if line.lower().startswith("content-disposition:")), "")
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        if filename_match:
            yield filename_match.group(1), content


def field_value(options, key):
    return html.escape(str(options.get(key, "")))


def text_input(options, key, label, input_type="text"):
    return (
        f"<label><span>{html.escape(label)}</span>"
        f"<input type='{input_type}' name='{key}' value='{field_value(options, key)}'></label>"
    )


def password_input(name, label, placeholder=""):
    return (
        f"<label><span>{html.escape(label)}</span>"
        f"<input type='password' name='{name}' value='' placeholder='{html.escape(placeholder)}'></label>"
    )


def checkbox_input(options, key, label):
    checked = " checked" if bool(options.get(key)) else ""
    return f"<label class='check'><input type='checkbox' name='{key}'{checked}><span>{html.escape(label)}</span></label>"


def bool_select_input(options, key, label):
    current = bool(options.get(key))
    off_selected = "" if current else " selected"
    on_selected = " selected" if current else ""
    return (
        f"<label><span>{html.escape(label)}</span><select name='{key}'>"
        f"<option value='true'{on_selected}>Aktiv</option>"
        f"<option value='false'{off_selected}>Aus</option>"
        "</select></label>"
    )


def select_input(options, key, label, values):
    current = str(options.get(key, ""))
    items = []
    for value in values:
        selected = " selected" if str(value) == current else ""
        items.append(f"<option value='{html.escape(value)}'{selected}>{html.escape(value)}</option>")
    return f"<label><span>{html.escape(label)}</span><select name='{key}'>{''.join(items)}</select></label>"


def world_config_card(options, index):
    return f"""
      <div class="subpanel">
        <h3>WORLD_{index}</h3>
        {text_input(options, f"world_{index}_name", "Weltordner")}
        {text_input(options, f"world_{index}_server_name", "Servername")}
        {text_input(options, f"world_{index}_owner", "OP / Owner UUID oder Name")}
      </div>"""


def render_config_panel(options):
    return f"""
  <h1>Konfiguration</h1>
  <form method="post" action="./save_config" class="config-form">
    <div class="panel">
      <h2>Server</h2>
      <div class="form-grid">
        {select_input(options, "server_type", "Server-Typ", ["PAPER", "PURPUR", "VANILLA"])}
        {text_input(options, "minecraft_version", "Minecraft-Version")}
        {text_input(options, "memory_initial", "RAM Start")}
        {text_input(options, "memory_max", "RAM Max")}
        {text_input(options, "server_name", "Fallback Servername")}
        {text_input(options, "server_owner", "Fallback OP / Owner")}
        {text_input(options, "motd", "MOTD")}
        {text_input(options, "level_name", "CUSTOM Weltordner")}
        {select_input(options, "gamemode", "Gamemode", ["survival", "creative", "adventure", "spectator"])}
        {select_input(options, "difficulty", "Difficulty", ["peaceful", "easy", "normal", "hard"])}
        {text_input(options, "max_players", "Max Spieler", "number")}
        {text_input(options, "idle_shutdown_minutes", "Idle Shutdown nach Minuten", "number")}
      </div>
      <div class="checks">
        {checkbox_input(options, "server_autostart", "Minecraft beim Add-on-Start automatisch starten")}
        {checkbox_input(options, "allow_create_worlds", "Fehlende Welten automatisch anlegen")}
        {checkbox_input(options, "block_disruptive_actions_when_players_online", "Neustart und Weltwechsel sperren, wenn Spieler online sind")}
        {checkbox_input(options, "enable_autopause", "Minecraft automatisch stoppen, wenn keine Spieler online sind")}
      </div>
    </div>
    <div class="panel">
      <h2>Bedrock</h2>
      <div class="form-grid">
        {text_input(options, "bedrock_port", "Bedrock UDP-Port", "number")}
      </div>
      <div class="checks">
        {checkbox_input(options, "bedrock_enabled", "Bedrock-Unterstuetzung mit Geyser und Floodgate aktivieren")}
      </div>
      <p class="hint">Der UDP-Port ist in der Add-on-Definition statisch freigegeben. Der Schalter steuert, ob Geyser-Spigot und Floodgate aktiv im Pluginordner liegen.</p>
      <p class="hint">Xbox, PlayStation und Nintendo Switch erlauben keine direkte Eingabe eigener Bedrock-Server. Ein lokaler DNS-Server kann eine offizielle Serverdomain wie <code>mco.cubecraft.net</code> auf die lokale Minecraft-IP umleiten. Die passende Domain sollte ueber das DNS-Query-Log ermittelt werden.</p>
    </div>
    <div class="panel">
      <h2>Security Center</h2>
      <div class="form-grid">
        {select_input(options, "anti_xray_engine_mode", "Anti-Xray Engine-Modus", ["1", "2", "3"])}
        {text_input(options, "grim_public_alert_threshold", "Oeffentliche Meldung ab VL", "number")}
        {text_input(options, "grim_public_alert_interval", "Meldungsintervall", "number")}
        {text_input(options, "grim_kick_threshold", "Kick ab VL", "number")}
        {text_input(options, "grim_event_log_max_entries", "Eventlog Eintraege", "number")}
        {select_input(options, "grim_update_channel", "Grim Update-Kanal", ["stable", "beta"])}
        {text_input(options, "xray_suspicion_alert_threshold", "Xray-Verdachtsschwelle", "number")}
        {text_input(options, "xray_suspicion_window_minutes", "Xray-Zeitfenster Minuten", "number")}
        {text_input(options, "xray_suspicion_alert_cooldown_seconds", "Xray-Meldungsabstand Sekunden", "number")}
      </div>
      <div class="checks">
        {checkbox_input(options, "security_enabled", "Security Center aktivieren")}
        {checkbox_input(options, "anti_xray_enabled", "Paper Anti-Xray verwalten")}
        {checkbox_input(options, "anti_xray_manage_defaults", "Default/Overworld verwalten")}
        {checkbox_input(options, "anti_xray_manage_nether", "Nether verwalten")}
        {checkbox_input(options, "anti_xray_manage_end", "End verwalten")}
        {checkbox_input(options, "grim_enabled", "Grim AntiCheat aktivieren")}
        {checkbox_input(options, "grim_auto_update", "Grim automatisch aktualisieren")}
        {checkbox_input(options, "grim_auto_compatible_beta", "Kompatible Grim-Vorabversion automatisch verwenden")}
        {checkbox_input(options, "grim_punishments_managed", "punishments.yml im Ingress verwalten")}
        {checkbox_input(options, "grim_public_alerts", "Oeffentliche Cheatverdacht-Meldungen im Preset verwenden")}
        {checkbox_input(options, "grim_kick_enabled", "Automatischen Kick im Preset verwenden")}
        {checkbox_input(options, "grim_event_log_enabled", "Security-Ereignisse speichern")}
        {checkbox_input(options, "xray_suspicion_enabled", "Xray-Verdachtsscore aktivieren")}
        {checkbox_input(options, "xray_suspicion_rcon_alerts", "Xray-Cheatverdacht per RCON melden")}
        {checkbox_input(options, "xray_suspicion_rcon_save_before_scan", "Vor Xray-Stats-Scan per RCON speichern")}
      </div>
      <p class="hint">Grim nutzt standardmaessig stabile Releases. Wenn der Auto-Beta-Schalter aktiv ist und nur eine kompatible Vorabversion zur aktuellen Minecraft-Version passt, wird diese automatisch verwendet. Bedrock-Spieler ueber Geyser werden von Grim nicht geprueft; Paper Anti-Xray bleibt fuer Weltdaten aktiv.</p>
    </div>
    <div class="panel">
      <h2>Welten</h2>
      <div class="world-grid">
        {world_config_card(options, 1)}
        {world_config_card(options, 2)}
        {world_config_card(options, 3)}
      </div>
      <p class="hint">Der aktive Slot wird oben ueber "Welt wechseln" gesetzt. Diese Felder benennen nur die Slots.</p>
    </div>
    <div class="panel">
      <h2>Plugins, MQTT und playit.gg</h2>
      <div class="form-grid">
        {text_input(options, "mqtt_status_interval", "MQTT Statusintervall", "number")}
        {text_input(options, "playit_public_address", "playit.gg Adresse fuer User")}
        {password_input("playit_secret", "playit.gg Secret Key", "Leer lassen = unveraendert")}
        {password_input("rcon_password", "RCON Passwort", "Leer lassen = unveraendert")}
      </div>
      <div class="checks">
        {checkbox_input(options, "plugins_enabled", "Plugin-Download/Preset beim Start verarbeiten")}
        {checkbox_input(options, "plugin_browser_enabled", "Ingress Adminpanel aktivieren")}
        {checkbox_input(options, "mqtt_enabled", "MQTT Discovery und Befehle aktivieren")}
        {checkbox_input(options, "playit_enabled", "playit.gg Steuerung im Ingress aktivieren")}
        {checkbox_input(options, "enable_rcon", "RCON fuer Kick/Ban/Backups aktivieren")}
        {checkbox_input(options, "rcon_message_stop_announcement", "RCON Stop-Ansage senden")}
        {checkbox_input(options, "rcon_message_stop_save", "RCON save-all beim Stop ausfuehren")}
        {checkbox_input(options, "rcon_message_backup_save", "RCON save-all/save-off fuer Backups ausfuehren")}
      </div>
    </div>
    <div class="panel">
      <h2>Adminmodus</h2>
      <div class="form-grid">
        {password_input("ingress_admin_password", "Neues Admin-Passwort", "Leer lassen = unveraendert")}
        {text_input(options, "ingress_admin_users", "Admin-User ohne Passwort")}
      </div>
      <p class="hint">Kommagetrennt: Home-Assistant-Username, User-ID oder E-Mail, falls HA sie als Ingress-Header liefert. Start, Stop, Weltwechsel und JAR-Explorer bleiben im User-Modus.</p>
    </div>
    <button type="submit">Konfiguration speichern</button>
  </form>"""


def render_backup_panel(options, status, is_admin):
    active_world = str(status.get("active_world") or options.get("level_name") or "world")
    world_choices = []
    seen = set()
    for value in (
        active_world,
        str(options.get("level_name") or ""),
        str(options.get("world_1_name") or ""),
        str(options.get("world_2_name") or ""),
        str(options.get("world_3_name") or ""),
    ):
        value = value.strip()
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        selected = " selected" if value == active_world else ""
        world_choices.append(f"<option value='{html.escape(value)}'{selected}>{html.escape(value)}</option>")
    backup_rows = []
    backups = list_backups()
    for backup in backups:
        quoted = quote(backup["name"])
        persistent = bool(backup["persistent"])
        status_label = "<span class='good'>Persistent</span>" if persistent else "<span class='muted'>normal</span>"
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(backup["mtime"] or 0)))
        world = html.escape(backup.get("world") or "unbekannt")
        reason = html.escape(backup.get("reason") or "")
        persist_action = "unpersist" if persistent else "persist"
        persist_label = "Persistent entfernen" if persistent else "Persistent setzen"
        admin_actions = ""
        if is_admin:
            admin_actions = (
                f"<form method='post' action='./backup_action?action={persist_action}&backup={quoted}'>"
                f"<button type='submit'>{persist_label}</button></form>"
                f"<form method='post' action='./backup_action?action=delete&backup={quoted}' onsubmit=\"return confirm('Backup wirklich loeschen?')\">"
                "<button class='danger' type='submit'>Loeschen</button></form>"
                f"<form method='post' action='./backup_action?action=restore&backup={quoted}' onsubmit=\"return confirm('Restore ersetzt den Minecraft-Datenstand. Wirklich fortfahren?')\">"
                "<input type='hidden' name='confirm' value='restore'>"
                "<button class='warning' type='submit'>Restore</button></form>"
            )
        backup_rows.append(
            f"<tr><td><code>{html.escape(backup['name'])}</code></td><td>{mtime}</td><td>{world}</td>"
            f"<td>{reason}</td><td>{backup['size_mb']:.1f} MB</td><td>{status_label}</td><td>{admin_actions}</td></tr>"
        )
    table = "\n".join(backup_rows) or "<tr><td colspan='7' class='empty'>Keine Backups vorhanden.</td></tr>"
    disabled = "" if is_admin else " disabled"
    return f"""
  <h1>Backup</h1>
  <div class="panel">
    <div class="grid">
      <div class="metric"><div class="label">Backups</div><div class="value">{html.escape(str(status.get('backup_count', 0)))}</div></div>
      <div class="metric"><div class="label">Gesamtgroesse</div><div class="value">{html.escape(str(status.get('backup_total_mb', 0)))} MB</div></div>
      <div class="metric"><div class="label">Letztes Backup</div><div class="value">{html.escape(str(status.get('latest_backup') or 'keins'))}</div></div>
      <div class="metric"><div class="label">Aktive Welt</div><div class="value">{html.escape(active_world)}</div></div>
    </div>
  </div>
  <div class="panel">
    <h2>Backup-Konfiguration</h2>
    <form method="post" action="./save_config" class="config-form">
      <input type="hidden" name="return_tab" value="backup">
      <input type="hidden" name="config_keys" value="backup_interval_hours,backup_retention_days,backup_min_interval_minutes,backup_keep_last,enable_auto_backup,backup_on_start,backup_on_shutdown,backup_on_world_switch">
      <div class="form-grid">
        {text_input(options, "backup_interval_hours", "Auto-Backup Intervall Stunden", "number")}
        {text_input(options, "backup_retention_days", "Backups nach Tagen loeschen", "number")}
        {text_input(options, "backup_min_interval_minutes", "Backup Mindestabstand Minuten", "number")}
        {text_input(options, "backup_keep_last", "Normale Backups behalten", "number")}
      </div>
      <div class="checks">
        {checkbox_input(options, "enable_auto_backup", "Auto-Backup aktivieren")}
        {checkbox_input(options, "backup_on_start", "Backup beim Server-Start")}
        {checkbox_input(options, "backup_on_shutdown", "Backup beim Server-Stop")}
        {checkbox_input(options, "backup_on_world_switch", "Backup beim Weltwechsel")}
      </div>
      <p class="hint">Persistent markierte Backups werden von Tages- und Anzahl-Rotation ausgenommen, bis das Flag entfernt oder das Backup geloescht wird.</p>
      <button type="submit"{disabled}>Backup-Konfiguration speichern</button>
    </form>
  </div>
  <div class="panel">
    <h2>Manuelles Backup</h2>
    <form method="post" action="./backup_action?action=backup_now">
      <label>Welt
        <select name="world">{''.join(world_choices)}</select>
      </label>
      <button type="submit"{disabled}>Backup now</button>
    </form>
    <p class="hint">Backups sichern den gesamten Minecraft-Datenordner. Die Welt-Auswahl wird im Dateinamen/Eintrag vermerkt, damit Restore-Listen pro Welt nachvollziehbar bleiben.</p>
  </div>
  <div class="panel">
    <h2>Vorhandene Backups</h2>
    <table>
      <thead><tr><th>Datei</th><th>Datum</th><th>Welt</th><th>Grund</th><th>Groesse</th><th>Status</th><th>Aktionen</th></tr></thead>
      <tbody>{table}</tbody>
    </table>
  </div>"""


def render_systemmessages_panel(options, status, is_admin):
    rows = [
        (
            "Server-Stop Ansage",
            "rcon_message_stop_announcement",
            "say Server is stopping",
            "Chat: Server is stopping",
        ),
        (
            "Server-Stop Save",
            "rcon_message_stop_save",
            "save-all flush",
            "Log/Chat: Saved the game",
        ),
        (
            "Backup Save/Frieren",
            "rcon_message_backup_save",
            "save-all flush, save-off, save-on",
            "Log/Chat: Saved the game / saving disabled/enabled",
        ),
        (
            "Xray Stats Save",
            "xray_suspicion_rcon_save_before_scan",
            "save-all",
            "Log/Chat: Saved the game",
        ),
        (
            "Xray Cheatverdacht",
            "xray_suspicion_rcon_alerts",
            "say [Security] Cheatverdacht ...",
            "Chat: [Security] Cheatverdacht ...",
        ),
    ]
    body = []
    for label, key, command, visible in rows:
        active = bool(options.get(key))
        status_label = "<span class='good'>Aktiv</span>" if active else "<span class='muted'>Aus</span>"
        body.append(
            f"<tr><td>{html.escape(label)}</td><td><code>{html.escape(command)}</code></td>"
            f"<td>{html.escape(visible)}</td><td>{status_label}</td></tr>"
        )
    disabled = "" if is_admin else " disabled"
    rcon_state = "<span class='good'>Aktiv</span>" if rcon_available(options) else "<span class='bad'>Nicht verfuegbar</span>"
    return f"""
  <h1>Systemmessages</h1>
  <div class="panel">
    <div class="grid">
      <div class="metric"><div class="label">RCON</div><div class="value">{rcon_state}</div></div>
      <div class="metric"><div class="label">Server</div><div class="value">{html.escape(str(status.get('control_state') or 'unknown'))}</div></div>
      <div class="metric"><div class="label">Aktive Welt</div><div class="value">{html.escape(str(status.get('active_world') or 'unknown'))}</div></div>
    </div>
  </div>
  <div class="panel">
    <h2>Aktuelle RCON-Systemmeldungen</h2>
    <table>
      <thead><tr><th>Quelle</th><th>RCON-Kommando</th><th>Sichtbare Meldung</th><th>Status</th></tr></thead>
      <tbody>{''.join(body)}</tbody>
    </table>
  </div>
  <div class="panel">
    <h2>Schalter</h2>
    <form method="post" action="./save_config" class="config-form">
      <input type="hidden" name="return_tab" value="systemmessages">
      <input type="hidden" name="config_keys" value="rcon_message_stop_announcement,rcon_message_stop_save,rcon_message_backup_save,xray_suspicion_rcon_save_before_scan,xray_suspicion_rcon_alerts">
      <div class="form-grid">
        {bool_select_input(options, "rcon_message_stop_announcement", "Server-Stop Ansage senden")}
        {bool_select_input(options, "rcon_message_stop_save", "Beim Server-Stop save-all flush ausfuehren")}
        {bool_select_input(options, "rcon_message_backup_save", "Bei Backups per RCON speichern und Saving kurz einfrieren")}
        {bool_select_input(options, "xray_suspicion_rcon_save_before_scan", "Vor Xray-Stats-Scan per RCON speichern")}
        {bool_select_input(options, "xray_suspicion_rcon_alerts", "Xray-Cheatverdacht per RCON melden")}
      </div>
      <p class="hint">Einige Meldungen wie <code>Saved the game</code> kommen direkt vom Minecraft-Server als Antwort auf <code>save-all</code>. Abschalten bedeutet deshalb, das jeweilige RCON-save-all fuer diesen Zweck nicht mehr auszufuehren.</p>
      <button type="submit"{disabled}>Systemmessages speichern</button>
    </form>
  </div>"""


def render_autorefresh_panel(disabled_until=0):
    now = int(time.time())
    disabled = disabled_until > now
    checked = " checked" if disabled else ""
    remaining = max(0, disabled_until - now)
    status = f"Aus fuer noch ca. {remaining // 60 + 1} Minuten" if disabled else "Aktiv"
    status_class = "warning" if disabled else "good"
    return f"""
  <div class="panel">
    <h2>Auto-Refresh</h2>
    <form method="post" action="./autorefresh_action" class="inline-controls">
      <label class="check"><input type="checkbox" name="disable_autorefresh"{checked}><span>Auto-Refresh im Ingress fuer 5 Minuten pausieren</span></label>
      <button type="submit">Uebernehmen</button>
    </form>
    <p class="{status_class}">Status: {html.escape(status)}</p>
    <p class="hint">Die Pause endet automatisch nach 5 Minuten oder beim naechsten Serverstart.</p>
  </div>"""


def render_admin_gate(options, is_admin, is_ha_admin, admin_user="", detected_users="keiner"):
    password = admin_password(options)
    allowlist = admin_user_allowlist(options)
    detected_user_hint = (
        f"Erkannter HA-User: <code>{html.escape(detected_users)}</code>"
        if detected_users != "keiner"
        else ""
    )
    user_mode_text = "Start, Stop, Neustart, Weltwechsel, JAR-Explorer, Spieler-Kick/Ban, Banliste und playit Stop sind freigegeben."
    auto_admin_hint = (
        f"<p class=\"hint\">{detected_user_hint}</p>"
        if detected_user_hint
        else (
            "<p class=\"hint\">Automatischer Adminmodus ist in dieser Ingress-Umgebung nicht verfuegbar. Nutze das Admin-Passwort.</p>"
            if allowlist
            else ""
        )
    )
    if is_ha_admin or admin_user:
        source = (
            "weil dein Home-Assistant-User Admin ist"
            if is_ha_admin
            else f"weil dein Home-Assistant-User `{html.escape(admin_user)}` in der Admin-User-Liste steht"
        )
        password_note = (
            " Normale User koennen den Adminmodus zusaetzlich per Passwort entsperren."
            if password
            else " Setze unten ein Passwort, wenn normale User den Adminmodus ebenfalls entsperren duerfen."
        )
        return f"""
  <div class="panel mode-panel">
    <h2>Adminmodus</h2>
    <p class="good">Automatisch aktiv, {source}.</p>
    <p>{password_note}</p>
  </div>"""
    if not password:
        return f"""
  <div class="panel mode-panel">
    <h2>User-Modus</h2>
    <p>{user_mode_text}</p>
    {auto_admin_hint}
    <p class="hint">Adminmodus ist nur fuer Home-Assistant-Admins aktiv. Ein Admin kann optional ein Passwort fuer normale User setzen.</p>
  </div>"""
    if is_admin:
        return """
  <div class="panel mode-panel">
    <h2>Adminmodus</h2>
    <p class="good">Aktiv. Konfiguration ist freigeschaltet.</p>
    <form method="post" action="./admin_logout"><button type="submit">Adminmodus verlassen</button></form>
  </div>"""
    return f"""
  <div class="panel mode-panel">
    <h2>User-Modus</h2>
    <p>{user_mode_text}</p>
    {auto_admin_hint}
    <form method="post" action="./admin_login" class="login-form">
      {password_input("ingress_admin_password", "Admin-Passwort")}
      <button type="submit">Adminmodus entsperren</button>
    </form>
  </div>"""


def render_playit_panel(options, status, is_admin=False):
    enabled = bool(options.get("playit_enabled"))
    secret_set = bool(str(options.get("playit_secret") or ""))
    state = str(status.get("playit_state") or ("disabled" if not enabled else "unknown"))
    detected_address = str(status.get("playit_address") or "").strip()
    configured_address = str(options.get("playit_public_address") or "").strip()
    address = detected_address or configured_address
    state_class = "good" if state == "running" else "pending" if state in {"starting", "stopping"} else "muted"
    start_disabled = "" if secret_set else " disabled"
    restart_disabled = "" if secret_set else " disabled"
    hint = (
        "Secret Key fehlt. Erzeuge ihn im playit.gg Setup und speichere ihn in der Konfiguration."
        if not secret_set
        else "Tunnel-Ziel in playit.gg: Minecraft Java auf 127.0.0.1:25565."
    )
    address_value = html.escape(address if address else ("Adresse nicht hinterlegt" if state == "running" else "nicht verfuegbar"))
    address_class = "good" if address else "muted"
    start_controls = (
        f"""
      <form method="post" action="./playit_action?action=start"><input type="hidden" name="return_tab" value="dashboard"><button type="submit"{start_disabled}>playit Start</button></form>
      <form method="post" action="./playit_action?action=restart"><input type="hidden" name="return_tab" value="dashboard"><button class="warning" type="submit"{restart_disabled}>playit Neustart</button></form>"""
        if is_admin
        else ""
    )
    mode_hint = "User duerfen den Tunnel stoppen. Start und Neustart bleiben Adminmodus." if not is_admin else hint
    return f"""
  <div class="panel">
    <h2>playit.gg</h2>
    <div class="grid">
      <div class="metric"><div class="label">Steuerung</div><div class="value">{'Aktiv' if enabled else 'Aus'}</div></div>
      <div class="metric"><div class="label">Secret</div><div class="value">{'Gesetzt' if secret_set else 'Fehlt'}</div></div>
      <div class="metric"><div class="label">Status</div><div class="value {state_class}">{html.escape(state)}</div></div>
      <div class="metric"><div class="label">Adresse</div><div class="value {address_class}">{address_value}</div></div>
    </div>
    <div style="margin-top: 14px;">
      {start_controls}
      <form method="post" action="./playit_action?action=stop"><input type="hidden" name="return_tab" value="dashboard"><button class="danger" type="submit">playit Stop</button></form>
    </div>
    <p class="hint">{html.escape(mode_hint)}</p>
  </div>"""


def render_bedrock_panel(status, options, is_admin=False):
    enabled = bool(status.get("bedrock_enabled"))
    state = str(status.get("bedrock_state") or ("inactive" if not enabled else "unknown"))
    if not enabled:
        controls = (
            """
    <div class="button-row" style="margin-top:14px">
      <form method="post" action="./bedrock_action?action=enable"><button type="submit">Bedrock aktivieren</button></form>
    </div>"""
            if is_admin
            else ""
        )
        return f"""
  <div class="panel">
    <h2>Bedrock</h2>
    <div class="grid">
      <div class="metric"><div class="label">Bedrock-Unterstuetzung</div><div class="value muted">Inaktiv</div></div>
    </div>
    {controls}
  </div>"""
    state_class = "good" if state == "active" else "pending" if state in {"prepared", "unknown"} else "warning"
    state_text = "Aktiv" if state == "active" else "Fehler" if state == "error" else "Vorbereitet"
    restart_disabled = "" if status.get("online") else " disabled"
    controls = (
        f"""
    <div class="button-row" style="margin-top:14px">
      <form method="post" action="./bedrock_action?action=disable"><button class="danger" type="submit">Bedrock deaktivieren</button></form>
      <form method="post" action="./bedrock_action?action=restart"><button class="warning" type="submit"{restart_disabled}>Neustart anwenden</button></form>
    </div>
    <p class="hint">Bedrock laeuft ueber Geyser/Floodgate-Plugins. Aktivieren oder Deaktivieren wird beim naechsten Minecraft-Start wirksam.</p>"""
        if is_admin
        else ""
    )
    return f"""
  <div class="panel">
    <h2>Bedrock</h2>
    <div class="grid">
      <div class="metric"><div class="label">Bedrock-Unterstuetzung</div><div class="value {state_class}">{state_text}</div></div>
      <div class="metric"><div class="label">Geyser</div><div class="value">{html.escape(str(status.get('geyser_status') or 'unknown'))}</div></div>
      <div class="metric"><div class="label">Floodgate</div><div class="value">{html.escape(str(status.get('floodgate_status') or 'unknown'))}</div></div>
      <div class="metric"><div class="label">UDP-Port</div><div class="value">{html.escape(str(status.get('bedrock_port') or 19132))}</div></div>
      <div class="metric"><div class="label">Authentifizierung</div><div class="value">{html.escape(str(status.get('bedrock_auth') or 'floodgate'))}</div></div>
    </div>
    {f"<p class='hint'>{html.escape(str(status.get('bedrock_message') or ''))}</p>" if status.get('bedrock_message') else ""}
    {controls}
  </div>"""


def status_class(ok, warning=False, disabled=False):
    if disabled:
        return "muted"
    if ok:
        return "good"
    return "pending" if warning else "warning"


def render_security_panel(options, status, is_admin=False, show_moderation=False):
    security = status.get("security") if isinstance(status.get("security"), dict) else {}
    enabled = bool(security.get("enabled", options.get("security_enabled", True)))
    anti = security.get("anti_xray") if isinstance(security.get("anti_xray"), dict) else {}
    grim = security.get("grim") if isinstance(security.get("grim"), dict) else {}
    punishments = security.get("punishments") if isinstance(security.get("punishments"), dict) else {}
    events = security.get("events") if isinstance(security.get("events"), dict) else {}
    xray = security.get("xray_suspicion") if isinstance(security.get("xray_suspicion"), dict) else {}
    via = security.get("via_version") if isinstance(security.get("via_version"), dict) else {}
    bedrock = security.get("bedrock_compatibility") if isinstance(security.get("bedrock_compatibility"), dict) else {}
    overall_class = status_class(bool(security.get("healthy")), bool(security.get("warnings")), not enabled)
    overall_text = "Aktiv" if enabled and security.get("healthy") else "Warnung" if enabled else "Deaktiviert"
    grim_text = "Fehlerhaft" if grim.get("healthy") is False else "Installiert" if grim.get("installed") else "Fehlt" if grim.get("requested") else "Deaktiviert"
    grim_class = "warning" if grim.get("healthy") is False else status_class(bool(grim.get("installed")), bool(grim.get("warnings")) or grim.get("loaded") is None, not grim.get("requested"))
    anti_text = "Aktiv" if anti.get("configured") and anti.get("requested") else "Deaktiviert" if not anti.get("requested") else "Nicht bestaetigt"
    punish_text = f"{punishments.get('categories', 0)} Kategorien" if punishments.get("file_exists") else "Noch nicht initialisiert"
    warning = bedrock.get("warning") or ""
    beta_button = (
        "<form method='post' action='./?security_action=grim_beta'><button class='warning' type='submit'>Kompatible Vorabversion testen</button></form>"
        if grim.get("latest_available_channel") == "beta_or_snapshot" and grim.get("update_available")
        else ""
    )
    via_button = (
        "<form method='post' action='./?security_action=fix_viaversion'><button class='warning' type='submit'>ViaVersion-Einstellung korrigieren</button></form>"
        if via.get("placement_rotation_fix_enabled")
        else ""
    )
    xray_import_button = (
        "<form method='post' action='./?security_action=xray_import' onsubmit=\"return confirm('Aktuelle Stats als Test-Beobachtungen importieren? Das kann bestehende Erz-Zaehler in den Verdachtsscore uebernehmen.')\"><button class='warning' type='submit'>Aktuelle Xray-Stats importieren</button></form>"
        if is_admin and xray.get("stats_polling")
        else ""
    )
    xray_reset_button = (
        "<form method='post' action='./?security_action=xray_reset' onsubmit=\"return confirm('Xray-Verdachtsscore wirklich zuruecksetzen? Beim naechsten Scan wird eine neue Baseline aus den aktuellen Stats angelegt.')\"><button class='danger' type='submit'>Xray-Score zuruecksetzen</button></form>"
        if is_admin
        else ""
    )
    xray_rows = []
    for player in xray.get("top_players") or []:
        cls = "warning" if player.get("alerting") else "pending" if int(player.get("score") or 0) else "muted"
        player_name = str(player.get("player") or "")
        xray_action = (
            f"<form method='post' action='./?security_action=xray_reset_player' onsubmit=\"return confirm('Xray-Score fuer {html.escape(player_name)} zuruecksetzen?')\"><input type='hidden' name='player' value='{html.escape(player_name)}'><button class='danger' type='submit'>Reset</button></form>"
            if is_admin
            else ""
        )
        xray_rows.append(
            f"<tr><td>{html.escape(player_name)}</td>"
            f"<td class='{cls}'>{html.escape(str(player.get('score') or 0))}</td>"
            f"<td>{html.escape(str(player.get('hidden_hits') or 0))}</td>"
            f"<td>{html.escape(str(player.get('unknown_visibility_hits') or 0))}</td>"
            f"<td>{html.escape(str(player.get('ore_hits') or 0))}</td>"
            f"<td>{html.escape(str(player.get('last_block') or ''))}</td>"
            f"<td>{xray_action}</td></tr>"
        )
    xray_table = (
        "<table style='margin-top:12px'><thead><tr><th>Spieler</th><th>Score</th><th>Verdeckt</th><th>Stats/unklar</th><th>Erzfunde</th><th>Letzter Block</th><th>Aktion</th></tr></thead>"
        f"<tbody>{''.join(xray_rows) or '<tr><td colspan=\"7\" class=\"empty\">Noch keine Xray-Beobachtungen.</td></tr>'}</tbody></table>"
    )
    controls = (
        """
    <div style="margin-top: 14px;">
      <a class="button" href="./security/punishments">Punishments oeffnen</a>
      <a class="button" href="./security/events">Ereignisse anzeigen</a>
      <form method="post" action="./?security_action=reconcile"><button type="submit">Konfiguration pruefen</button></form>
      PLACEHOLDER_BETA
      PLACEHOLDER_VIA
      PLACEHOLDER_XRAY_IMPORT
      PLACEHOLDER_XRAY_RESET
    </div>""".replace("PLACEHOLDER_BETA", beta_button).replace("PLACEHOLDER_VIA", via_button).replace("PLACEHOLDER_XRAY_IMPORT", xray_import_button).replace("PLACEHOLDER_XRAY_RESET", xray_reset_button)
        if is_admin
        else ""
    )
    baseline_rows = []
    for player in xray.get("baseline_players") or []:
        baseline_rows.append(
            f"<tr><td>{html.escape(str(player.get('player') or ''))}</td>"
            f"<td>{html.escape(str(player.get('tracked_total') or 0))}</td>"
            f"<td>{html.escape(str(player.get('diamond_ore') or 0))}</td>"
            f"<td>{html.escape(str(player.get('deepslate_diamond_ore') or 0))}</td>"
            f"<td>{html.escape(str(player.get('ancient_debris') or 0))}</td>"
            f"<td>{html.escape(str(player.get('gold_ore') or 0))}</td></tr>"
        )
    baseline_table = (
        "<table style='margin-top:12px'><thead><tr><th>Baseline-Spieler</th><th>Getrackt</th><th>Diamond</th><th>Deep Diamond</th><th>Ancient Debris</th><th>Gold</th></tr></thead>"
        f"<tbody>{''.join(baseline_rows)}</tbody></table>"
        if baseline_rows and not xray_rows
        else ""
    )
    stats_dirs = ", ".join(xray.get("stats_dirs") or [xray.get("stats_dir") or ""])
    rules = xray.get("rules") if isinstance(xray.get("rules"), dict) else {}
    rule_blocks = rules.get("blocks") if isinstance(rules.get("blocks"), dict) else {}
    rule_rows = []
    for block, rule in rule_blocks.items():
        if not isinstance(rule, dict):
            continue
        block_id = str(block).replace("minecraft:", "")
        rule_rows.append(
            f"<tr><td><code>{html.escape(block_id)}</code></td>"
            f"<td><input type='checkbox' name='xray_enabled_{html.escape(block_id)}' {'checked' if rule.get('enabled', True) else ''}></td>"
            f"<td><input type='number' min='0' max='50' name='xray_weight_{html.escape(block_id)}' value='{html.escape(str(rule.get('weight', 0)))}'></td></tr>"
        )
    xray_rules_form = (
        f"""
    <form method="post" action="./?security_action=xray_rules" style="margin-top:14px">
      <h3>Xray-Score-Regeln</h3>
      <div class="grid">
        <label><span>Zeitfenster Minuten</span><input type="number" min="5" max="240" name="window_minutes" value="{html.escape(str(rules.get('window_minutes') or xray.get('window_minutes') or 20))}"></label>
        <label><span>Alarm-Schwelle</span><input type="number" min="10" max="500" name="alert_threshold" value="{html.escape(str(rules.get('alert_threshold') or xray.get('threshold') or 120))}"></label>
        <label><span>RCON-Cooldown Sekunden</span><input type="number" min="30" max="3600" name="alert_cooldown_seconds" value="{html.escape(str(rules.get('alert_cooldown_seconds') or xray.get('alert_cooldown_seconds') or 300))}"></label>
      </div>
      <table style="margin-top:12px"><thead><tr><th>Material</th><th>Aktiv</th><th>Punkte</th></tr></thead><tbody>{''.join(rule_rows)}</tbody></table>
      <button type="submit">Xray-Regeln speichern</button>
    </form>"""
        if is_admin and rule_rows
        else ""
    )
    moderation_panel = ""
    if show_moderation:
        known_players = player_intelligence(status, options)
        moderation_rows = []
        for player in known_players:
            name = str(player.get("name") or "")
            if not name:
                continue
            quoted = quote(name)
            state = str(player.get("status") or "offline")
            kick_button = (
                f"<form method='post' action='./player_action?action=kick&player={quoted}'><input type='hidden' name='return_tab' value='security'><button type='submit'>Kick</button></form>"
                if state.startswith("online")
                else ""
            )
            ban_button = (
                f"<form method='post' action='./player_action?action=ban&player={quoted}' onsubmit=\"return confirm('Spieler wirklich bannen?')\"><input type='hidden' name='return_tab' value='security'><button class='danger' type='submit'>Ban</button></form>"
                if "banned" not in state
                else ""
            )
            unban_button = (
                f"<form method='post' action='./player_action?action=unban&player={quoted}'><input type='hidden' name='return_tab' value='security'><button type='submit'>Unban</button></form>"
                if "banned" in state
                else ""
            )
            moderation_rows.append(
                f"<tr><td>{html.escape(name)}</td><td>{html.escape(state)}</td><td>{kick_button}{ban_button}{unban_button}</td></tr>"
            )
        banned_rows = []
        for player in banned_players():
            quoted = quote(player)
            banned_rows.append(
                f"<tr><td>{html.escape(player)}</td><td><form method='post' action='./player_action?action=unban&player={quoted}'><input type='hidden' name='return_tab' value='security'><button type='submit'>Unban</button></form></td></tr>"
            )
        moderation_panel = f"""
    <h3>Security-Moderation</h3>
    <table><thead><tr><th>Spieler</th><th>Status</th><th>Aktionen</th></tr></thead><tbody>{''.join(moderation_rows) or "<tr><td colspan='3' class='empty'>Keine Spieler bekannt.</td></tr>"}</tbody></table>
    <h3>Banliste</h3>
    <table><thead><tr><th>Spieler</th><th>Aktion</th></tr></thead><tbody>{''.join(banned_rows) or "<tr><td colspan='2' class='empty'>Keine Spieler gebannt.</td></tr>"}</tbody></table>"""
    return f"""
  <div class="panel">
    <h2>Security Center</h2>
    <div class="grid">
      <div class="metric"><div class="label">Gesamtstatus</div><div class="value {overall_class}">{overall_text}</div></div>
      <div class="metric"><div class="label">Paper Anti-Xray</div><div class="value {status_class(bool(anti.get('configured')), bool(anti.get('warnings')), not anti.get('requested'))}">{anti_text}</div></div>
      <div class="metric"><div class="label">Anti-Xray-Modus</div><div class="value">{html.escape(str(anti.get('engine_mode') or options.get('anti_xray_engine_mode') or 2))}</div></div>
      <div class="metric"><div class="label">Grim AntiCheat</div><div class="value {grim_class}">{grim_text}</div></div>
      <div class="metric"><div class="label">Grim-Version</div><div class="value">{html.escape(str(grim.get('version') or grim.get('latest_version') or 'unbekannt'))}</div></div>
      <div class="metric"><div class="label">Plugin erkannt</div><div class="value">{'Ja' if grim.get('plugin_discovered') else 'Nein' if grim.get('plugin_discovered') is False else 'Unbekannt'}</div></div>
      <div class="metric"><div class="label">Cheatpruefung</div><div class="value {'good' if grim.get('healthy') else 'warning' if grim.get('healthy') is False else 'pending'}">{'Aktiv' if grim.get('healthy') else 'Nicht aktiv' if grim.get('healthy') is False else 'Unbekannt'}</div></div>
      <div class="metric"><div class="label">PacketManager</div><div class="value">{'Aktiv' if grim.get('packet_manager_active') else 'Fehlgeschlagen' if grim.get('packet_manager_active') is False else 'Unbekannt'}</div></div>
      <div class="metric"><div class="label">Grim-Kommandos</div><div class="value">{'Registriert' if grim.get('commands_registered') else 'Fehlgeschlagen' if grim.get('commands_registered') is False else 'Unbekannt'}</div></div>
      <div class="metric"><div class="label">Punishment-Regeln</div><div class="value {status_class(bool(punishments.get('valid')), bool(punishments.get('warnings')), not punishments.get('managed'))}">{punish_text}</div></div>
      <div class="metric"><div class="label">Oeffentliche Meldungen</div><div class="value">{'Aktiv' if punishments.get('public_alerts_detected') else 'Nicht erkannt'}</div></div>
      <div class="metric"><div class="label">Automatischer Kick</div><div class="value">{'Aktiv' if punishments.get('kick_actions_detected') else 'Nicht erkannt'}</div></div>
      <div class="metric"><div class="label">Ereignisse</div><div class="value">{html.escape(str(events.get('stored_entries', 0)))}</div></div>
      <div class="metric"><div class="label">Xray-Verdacht</div><div class="value">{html.escape(str(xray.get('tracked_players', 0)))} Spieler</div></div>
    </div>
    <p class="hint">Paper Anti-Xray erzeugt keine Cheat-Warnungen. Es verfaelscht die an Clients uebertragenen versteckten Bloecke.</p>
    {f"<p class='hint'>Aktive Welt: <code>{html.escape(str(anti.get('active_world') or 'unbekannt'))}</code>; wirksame Datei: <code>{html.escape(', '.join(anti.get('config_files') or []))}</code></p>" if anti else ""}
    {f"<p class='hint'>Ursache: {html.escape(str(grim.get('runtime_error')))}</p>" if grim.get('runtime_error') else ""}
    {f"<p class='hint'>Neueste verfuegbare Grim-Version: {html.escape(str(grim.get('latest_available_version')))} ({html.escape(str(grim.get('latest_available_channel') or 'stable'))}). Stable bleibt Standard.</p>" if grim.get('latest_available_version') else ""}
    <p class="hint">Xray/Yray wird nicht direkt als Clientmod bewiesen. Der Score bewertet auffaellige Erz- und Ancient-Debris-Funde als Cheatverdacht.</p>
    <p class="hint">Datenquelle: {'Minecraft-Stats aktiv' if xray.get('stats_polling') else 'API/Bridge wartet auf Beobachtungen'}{f"; letzter Stats-Scan: {html.escape(str(xray.get('last_stats_scan')))}" if xray.get('last_stats_scan') else ""}; Stats-Dateien: {html.escape(str(xray.get('last_stats_files_scanned') or 0))}; getrackte Erz-Zaehler: {html.escape(str(xray.get('last_stats_tracked_total') or 0))}; Pfade: <code>{html.escape(stats_dirs or 'keine')}</code></p>
    {xray_table}
    {baseline_table}
    {moderation_panel}
    {xray_rules_form}
    {f"<p class='hint'>{html.escape(str(xray.get('warning')))}</p>" if xray.get('warning') else ""}
    {f"<p class='hint'>ViaVersion-Konflikt: {html.escape(str(via.get('warning')))}</p>" if via.get('warning') else ""}
    {f"<p class='hint'>{html.escape(warning)}</p>" if warning else ""}
    {f"<p class='hint'>{html.escape(str(security.get('error')))}</p>" if security.get('error') else ""}
    {controls}
  </div>"""


def render_security_punishments_page(message="", is_admin=False):
    security = load_security_module()
    document = security.load_punishments(security.data_paths(DATA_DIR))
    raw = html.escape(document.get("raw") or "Punishments:\n")
    sha = html.escape(str((document.get("revision") or {}).get("sha256") or ""))
    categories = document.get("categories") or []
    rows = []
    for category in categories:
        checks = ", ".join(str(item) for item in category.get("checks", []))
        commands = category.get("commands", [])
        rows.append(
            f"<tr><td>{html.escape(category.get('name', ''))}</td><td>{html.escape(checks)}</td><td>{len(commands)}</td><td><code>{html.escape('; '.join(str(item) for item in commands[:3]))}</code></td></tr>"
        )
    table = "\n".join(rows) or "<tr><td colspan='4' class='empty'>Keine Kategorien gefunden.</td></tr>"
    warnings = document.get("warnings") or document.get("errors") or []
    save_controls = (
        f"""
    <form method="post" action="./punishments/save">
      <input type="hidden" name="expected_sha" value="{sha}">
      <label><span>YAML-Expertenmodus</span><textarea name="yaml" rows="24">{raw}</textarea></label>
      <button type="submit">Speichern</button>
    </form>"""
        if is_admin
        else "<p class='hint'>Speichern ist nur im Adminmodus verfuegbar.</p>"
    )
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grim Punishments</title>{base_style()}</head><body>
  <h1>Grim Punishments</h1>
  <p><a class="button" href="../">Zurueck</a></p>
  {f"<div class='message'>{html.escape(message)}</div>" if message else ""}
  <div class="panel">
    <h2>Regel-Editor</h2>
    <table><thead><tr><th>Kategorie</th><th>Checks</th><th>Aktionen</th><th>Auszug</th></tr></thead><tbody>{table}</tbody></table>
    {''.join(f"<p class='hint'>{html.escape(str(item))}</p>" for item in warnings)}
  </div>
  <div class="panel">
    {save_controls}
  </div>
</body></html>"""


def render_security_events_page():
    security = load_security_module()
    events = security.get_security_events(security.data_paths(DATA_DIR), 200)
    rows = []
    for event in events:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(event.get('timestamp') or ''))}</td>"
            f"<td>{html.escape(str(event.get('player') or ''))}</td>"
            f"<td>{html.escape(str(event.get('check') or ''))}</td>"
            f"<td>{html.escape(str(event.get('violation_level') or ''))}</td>"
            f"<td>{html.escape(str(event.get('action') or ''))}</td>"
            f"<td>{html.escape(str(event.get('verbose') or event.get('raw') or ''))}</td>"
            "</tr>"
        )
    table = "\n".join(rows) or "<tr><td colspan='6' class='empty'>Noch keine Security-Ereignisse gespeichert.</td></tr>"
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Security Ereignisse</title>{base_style()}</head><body>
  <h1>Security Ereignisse</h1>
  <p><a class="button" href="../">Zurueck</a></p>
  <div class="panel">
    <table><thead><tr><th>Zeit</th><th>Spieler</th><th>Check</th><th>VL</th><th>Aktion</th><th>Details</th></tr></thead><tbody>{table}</tbody></table>
  </div>
</body></html>"""


def render_player_intelligence_panel(status, options, selected_player="", is_admin=False, sort_key="name", direction="asc"):
    sort_key = str(sort_key or "name").lower()
    direction = str(direction or "asc").lower()
    players = sorted_player_intelligence(status, options, sort_key, direction)
    selected_player = str(selected_player or "").strip()
    selected = None
    rows = []
    def sort_link(key, label):
        next_direction = "asc"
        if sort_key == key and direction != "desc":
            next_direction = "desc"
        active = " *" if sort_key == key else ""
        return f"<a href='./?tab=players&sort={key}&dir={next_direction}'>{html.escape(label + active)}</a>"
    for player in players:
        if selected_player and player["name"].lower() == selected_player.lower():
            selected = player
        state = str(player.get("status") or "offline")
        cls = "good" if state.startswith("online") else "warning" if "banned" in state else "muted"
        checks = ", ".join(f"{name} ({count})" for name, count in player.get("top_checks", [])[:3]) or "-"
        rows.append(
            f"<tr><td><a class='button' href='./?tab=players&player={quote(player['name'])}'>{html.escape(player['name'])}</a></td>"
            f"<td class='{cls}'>{html.escape(state)}</td>"
            f"<td>{html.escape(str(player.get('last_logon') or '-'))}</td>"
            f"<td>{html.escape(str(player.get('play_time') or '0m'))}</td>"
            f"<td>{html.escape(str(player.get('current_security_score') or 0))}</td>"
            f"<td>{html.escape(str(player.get('max_security_score') or 0))}</td>"
            f"<td>{html.escape(checks)}</td>"
            f"<td>{html.escape(str(player.get('client') or 'unbekannt'))}</td></tr>"
        )
    table = "\n".join(rows) or "<tr><td colspan='8' class='empty'>Noch keine Spieler bekannt.</td></tr>"
    detail = ""
    if selected:
        event_rows = []
        for event in selected.get("events", [])[:20]:
            event_rows.append(
                f"<tr><td>{html.escape(str(event.get('timestamp') or ''))}</td>"
                f"<td>{html.escape(str(event.get('check') or event.get('category') or ''))}</td>"
                f"<td>{html.escape(str(event.get('violation_level') or 0))}</td>"
                f"<td>{html.escape(str(event.get('action') or ''))}</td>"
                f"<td>{html.escape(str(event.get('verbose') or ''))}</td></tr>"
            )
        mined_rows = [
            f"<tr><td><code>{html.escape(str(block).replace('minecraft:', ''))}</code></td><td>{html.escape(str(count))}</td></tr>"
            for block, count in selected.get("top_mined", [])[:20]
        ]
        cheat_rows = []
        for check, count in selected.get("top_checks", []):
            latest = next((event.get("timestamp") for event in selected.get("events", []) if (event.get("check") or event.get("category")) == check), "")
            cheat_rows.append(f"<tr><td>{html.escape(str(check))}</td><td>{html.escape(str(count))}</td><td>{html.escape(str(latest or '-'))}</td></tr>")
        reset = (
            f"<form method='post' action='./?security_action=xray_reset_player' onsubmit=\"return confirm('Xray-Score fuer {html.escape(selected['name'])} zuruecksetzen?')\"><input type='hidden' name='player' value='{html.escape(selected['name'])}'><button class='danger' type='submit'>Xray-Score zuruecksetzen</button></form>"
            if is_admin
            else ""
        )
        reset_max = (
            f"<form method='post' action='./?security_action=max_score_reset_player' onsubmit=\"return confirm('Max-Score fuer {html.escape(selected['name'])} zuruecksetzen?')\"><input type='hidden' name='player' value='{html.escape(selected['name'])}'><button class='warning' type='submit'>Max-Score zuruecksetzen</button></form>"
            if is_admin
            else ""
        )
        detail = f"""
  <div class="panel">
    <h2>Spielerakte: {html.escape(selected['name'])}</h2>
    <div class="grid">
      <div class="metric"><div class="label">Status</div><div class="value">{html.escape(str(selected.get('status') or 'offline'))}</div></div>
      <div class="metric"><div class="label">Last Logon</div><div class="value">{html.escape(str(selected.get('last_logon') or '-'))}</div></div>
      <div class="metric"><div class="label">Onlinezeit</div><div class="value">{html.escape(str(selected.get('play_time') or '0m'))}</div></div>
      <div class="metric"><div class="label">Aktueller Security Score</div><div class="value">{html.escape(str(selected.get('current_security_score') or 0))}</div></div>
      <div class="metric"><div class="label">Max Security Score</div><div class="value">{html.escape(str(selected.get('max_security_score') or 0))}</div></div>
      <div class="metric"><div class="label">Client/Version</div><div class="value">{html.escape(str(selected.get('client') or 'unbekannt'))}</div></div>
      <div class="metric"><div class="label">Mined Blocks</div><div class="value">{html.escape(str(selected.get('mined_total') or 0))}</div></div>
    </div>
    <div style="margin-top:12px">{reset}{reset_max}</div>
    <h3>Erkannte Cheats / Checks</h3>
    <table><thead><tr><th>Check</th><th>Anzahl</th><th>Zuletzt</th></tr></thead><tbody>{''.join(cheat_rows) or "<tr><td colspan='3' class='empty'>Keine Checks gefunden.</td></tr>"}</tbody></table>
    <h3>Mined Materials / Blocks</h3>
    <table><thead><tr><th>Block</th><th>Anzahl</th></tr></thead><tbody>{''.join(mined_rows) or "<tr><td colspan='2' class='empty'>Keine Mining-Stats gefunden.</td></tr>"}</tbody></table>
    <h3>Security-Timeline</h3>
    <table><thead><tr><th>Zeit</th><th>Check</th><th>Score/VL</th><th>Aktion</th><th>Details</th></tr></thead><tbody>{''.join(event_rows) or "<tr><td colspan='5' class='empty'>Keine Ereignisse gefunden.</td></tr>"}</tbody></table>
  </div>"""
    return f"""
  <div class="panel">
    <h2>Spieler</h2>
    <table>
      <thead><tr><th>{sort_link('name', 'Spieler')}</th><th>{sort_link('status', 'Status')}</th><th>{sort_link('last_logon', 'Last Logon')}</th><th>{sort_link('online_time', 'Onlinezeit')}</th><th>{sort_link('current_score', 'Aktueller Score')}</th><th>{sort_link('max_score', 'Max Score')}</th><th>Regelverstoesse</th><th>Client</th></tr></thead>
      <tbody>{table}</tbody>
    </table>
  </div>
  {detail}"""


def base_style():
    return """
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; background: #111; color: #f5f5f5; }
    body { margin: 0; padding: 20px; }
    h1 { margin: 0 0 16px; font-size: 26px; }
    h2 { margin: 0 0 14px; font-size: 22px; }
    .panel { background: #1b1b1b; border: 1px solid #333; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; border-bottom: 1px solid #333; padding: 10px 8px; vertical-align: top; }
    th { color: #9cc9ef; font-weight: 700; }
    button, .button { background: #1f6fa8; color: white; border: 0; border-radius: 6px; padding: 8px 12px; text-decoration: none; cursor: pointer; margin-right: 8px; }
    textarea { width: 100%; box-sizing: border-box; background: #101010; color: #f5f5f5; border: 1px solid #555; border-radius: 6px; padding: 10px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
    label { display: flex; flex-direction: column; gap: 6px; color: #9cc9ef; font-size: 13px; font-weight: 700; }
    .message, .good { color: #9ee493; }
    .pending, .hint { color: #ffd080; }
    .warning { color: #ff9c9c; }
    .muted, .empty { color: #aaa; }
    code { background: #090909; padding: 2px 5px; border-radius: 4px; }
  </style>"""


def status_players(status):
    players = status.get("players") or []
    if isinstance(players, list):
        return [player for player in players if isinstance(player, dict)]
    return []


def render_player_identity(player):
    name = html.escape(str(player.get("display_name") or player.get("name") or player.get("server_name") or ""))
    platform = str(player.get("platform") or "java")
    platform_label = "Bedrock" if platform == "bedrock" else "Java"
    detail = ""
    if platform == "bedrock" and player.get("server_name"):
        detail = f"<div class='muted'>Servername: <code>{html.escape(str(player.get('server_name')))}</code></div>"
    return name, platform_label, detail


def render_player_admin_panel(options, status):
    if not status.get("online"):
        state = str(status.get("control_state") or "")
        message = "Server startet gerade. Spieler-Verwaltung wird aktiv, sobald Minecraft online ist." if state == "starting" else "Minecraft ist offline. Spieler-Verwaltung ist erst bei laufendem Server verfuegbar."
        return f"""
  <div class="panel">
    <h2>Spieler verwalten</h2>
    <p class="hint">{message}</p>
  </div>"""
    if not rcon_available(options):
        return """
  <div class="panel">
    <h2>Spieler verwalten</h2>
    <p class="hint">Kick/Ban braucht RCON. Aktiviere im Adminmodus `enable_rcon` und setze ein `rcon_password`, dann Server neu starten.</p>
  </div>"""
    players = status_players(status)
    if not players:
        players = [{"display_name": name, "server_name": name, "platform": "java"} for name in online_players(options)]
    bans = banned_players()
    ops = op_players()
    player_cards = []
    for player in players:
        server_name = safe_player_name(player.get("server_name") or player.get("name") or player.get("display_name"))
        if not server_name:
            continue
        escaped, platform_label, detail = render_player_identity(player)
        quoted = quote(server_name)
        is_op = server_name.lower() in ops
        op_badge = "<span class='badge warning'>OP</span>" if is_op else "<span class='badge muted'>Spieler</span>"
        platform_badge = f"<span class='badge muted'>{platform_label}</span>"
        mode_options = "".join(f"<option value='{mode}'>{mode}</option>" for mode in ("survival", "creative", "adventure", "spectator"))
        quick_give_buttons = "".join(
            "<form method='post' action='./player_action?action=give_quick&player={player}'>"
            "<input type='hidden' name='return_tab' value='admin'>"
            "<input type='hidden' name='item' value='{item}'>"
            "<input type='hidden' name='amount' value='{amount}'>"
            "<button class='warning' type='submit' title='{item}'>{label}</button>"
            "</form>".format(
                player=quoted,
                item=html.escape(item),
                amount=amount,
                label=html.escape(label),
            )
            for label, item, amount in QUICK_GIVE_ITEMS
        )
        player_cards.append(
            f"""
      <div class="player-card">
        <div class="player-head">
          <div>
            <h3>{escaped}</h3>
            <div class="muted">Online-Spieler</div>
            {detail}
          </div>
          <div class="badges"><span class="badge good">ONLINE</span>{platform_badge}{op_badge}</div>
        </div>
        <div class="action-section">
          <div class="section-title">Moderation</div>
          <form method="post" action="./player_action?action=kick&player={quoted}"><input type="hidden" name="return_tab" value="admin"><button type="submit">Kick</button></form>
          <form method="post" action="./player_action?action=ban&player={quoted}" onsubmit="return confirm('Spieler wirklich bannen?')"><input type="hidden" name="return_tab" value="admin"><button class="danger" type="submit">Ban</button></form>
        </div>
        <div class="action-section">
          <div class="section-title">Rechte</div>
          <form method="post" action="./player_action?action=op&player={quoted}" onsubmit="return confirm('OP-Rechte wirklich vergeben?')"><input type="hidden" name="return_tab" value="admin"><button class="warning" type="submit">OP geben</button></form>
          <form method="post" action="./player_action?action=deop&player={quoted}" onsubmit="return confirm('OP-Rechte wirklich entfernen?')"><input type="hidden" name="return_tab" value="admin"><button class="warning" type="submit">OP entfernen</button></form>
        </div>
        <div class="action-section">
          <div class="section-title">Gameplay</div>
          <form class="inline-controls" method="post" action="./player_action?action=gamemode&player={quoted}">
            <input type="hidden" name="return_tab" value="admin">
            <select name="gamemode">{mode_options}</select>
            <button type="submit">Setzen</button>
          </form>
        </div>
        <div class="action-section">
          <div class="section-title">Quick Give</div>
          <div class="button-row">{quick_give_buttons}</div>
          <form class="inline-controls give-form" method="post" action="./player_action?action=give&player={quoted}" onsubmit="return confirm('Item wirklich geben?')">
            <input type="hidden" name="return_tab" value="admin">
            <input name="item" value="minecraft:diamond" placeholder="minecraft:diamond">
            <input class="small-input" name="amount" type="number" min="1" max="2304" value="64">
            <button class="warning" type="submit">Geben</button>
          </form>
        </div>
      </div>"""
        )
    banned_rows = []
    for player in bans:
        escaped = html.escape(player)
        quoted = quote(player)
        banned_rows.append(
            f"<tr><td>{escaped}</td><td>"
            f"<form method='post' action='./player_action?action=unban&player={quoted}'><input type='hidden' name='return_tab' value='admin'><button type='submit'>Unban</button></form>"
            "</td></tr>"
        )
    player_grid = "\n".join(player_cards) or "<div class='empty'>Keine Spieler online.</div>"
    banned_table = "\n".join(banned_rows) or "<tr><td colspan='2' class='empty'>Keine Spieler gebannt.</td></tr>"
    return f"""
  <h1>Spieler</h1>
  <div class="panel">
    <div class="grid">
      <div class="metric"><div class="label">Online</div><div class="value">{html.escape(str(status.get('players_online', 0)))} / {html.escape(str(status.get('players_max', 0)))}</div></div>
      <div class="metric"><div class="label">Java</div><div class="value">{html.escape(str(status.get('players_java', 0)))}</div></div>
      <div class="metric"><div class="label">Bedrock</div><div class="value">{html.escape(str(status.get('players_bedrock', 0)))}</div></div>
      <div class="metric"><div class="label">RCON</div><div class="value good">Aktiv</div></div>
      <div class="metric"><div class="label">Adminmodus</div><div class="value good">Aktiv</div></div>
    </div>
    <div style="margin-top: 14px;">
      <form method="post" action="./player_action?action=broadcast" class="inline-controls">
        <input type="hidden" name="return_tab" value="admin">
        <input name="message" placeholder="Nachricht an alle Spieler">
        <button type="submit">Broadcast senden</button>
      </form>
    </div>
  </div>
  <div class="player-grid">
    {player_grid}
  </div>
  <div class="panel">
    <h2>Banliste</h2>
    <table>
      <thead><tr><th>Spieler</th><th>Aktionen</th></tr></thead>
      <tbody>{banned_table}</tbody>
    </table>
  </div>"""


def render_player_user_panel(options, status):
    if not status.get("online"):
        state = str(status.get("control_state") or "")
        message = "Server startet gerade. Spieleranzeige wird aktiv, sobald Minecraft online ist." if state == "starting" else "Minecraft ist offline. Spieleranzeige ist erst bei laufendem Server verfuegbar."
        return f"""
  <div class="panel">
    <h2>Spieler</h2>
    <p class="hint">{message}</p>
  </div>"""
    if not rcon_available(options):
        return """
  <div class="panel">
    <h2>Spieler</h2>
    <p class="hint">Kick/Ban braucht RCON. Bitte einen Admin, RCON zu aktivieren.</p>
  </div>"""

    players = status_players(status)
    if not players:
        players = [{"display_name": name, "server_name": name, "platform": "java"} for name in online_players(options)]
    bans = banned_players()
    player_rows = []
    for player in players:
        server_name = safe_player_name(player.get("server_name") or player.get("name") or player.get("display_name"))
        if not server_name:
            continue
        escaped, platform_label, detail = render_player_identity(player)
        quoted = quote(server_name)
        player_rows.append(
            f"<tr><td>{escaped}<div class='muted'>Plattform: {platform_label}</div>{detail}</td><td>"
            f"<form method='post' action='./player_action?action=kick&player={quoted}'><button type='submit'>Kick</button></form>"
            f"<form method='post' action='./player_action?action=ban&player={quoted}' onsubmit=\"return confirm('Spieler wirklich bannen?')\"><button class='danger' type='submit'>Ban</button></form>"
            "</td></tr>"
        )
    banned_rows = []
    for player in bans:
        escaped = html.escape(player)
        quoted = quote(player)
        banned_rows.append(
            f"<tr><td>{escaped}</td><td>"
            f"<form method='post' action='./player_action?action=unban&player={quoted}'><button type='submit'>Unban</button></form>"
            "</td></tr>"
        )
    player_table = "\n".join(player_rows) or "<tr><td colspan='2' class='empty'>Keine Spieler online.</td></tr>"
    banned_table = "\n".join(banned_rows) or "<tr><td colspan='2' class='empty'>Keine Spieler gebannt.</td></tr>"
    return f"""
  <h1>Spieler</h1>
  <div class="panel">
    <h2>Online</h2>
    <table>
      <thead><tr><th>Spieler</th><th>Aktionen</th></tr></thead>
      <tbody>{player_table}</tbody>
    </table>
  </div>
  <div class="panel">
    <h2>Banliste</h2>
    <table>
      <thead><tr><th>Spieler</th><th>Aktionen</th></tr></thead>
      <tbody>{banned_table}</tbody>
    </table>
  </div>"""


class PluginBrowser(BaseHTTPRequestHandler):
    server_version = "ha-minecraft-plugin-browser/1.0"

    def send_no_cache_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_no_cache_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, page, status=HTTPStatus.OK):
        body = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_no_cache_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self, limit=1024 * 1024):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        if length > limit:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return None
        return payload if isinstance(payload, dict) else {}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in {"", "/", "/index.html"}:
            params = parse_qs(parsed.query)
            self.render_index(
                folder=params.get("folder", ["plugins"])[0],
                tab=params.get("tab", ["dashboard"])[0],
                selected_player=params.get("player", [""])[0],
                sort_key=params.get("sort", ["name"])[0],
                direction=params.get("dir", ["asc"])[0],
            )
            return
        if parsed.path == "/api/security/status":
            options = load_options()
            security = load_security_module()
            self.send_json({"security": security.get_security_status(options, security.data_paths(DATA_DIR))})
            return
        if parsed.path in {"/api/security/anti-xray", "/api/security/grim"}:
            options = load_options()
            security = load_security_module()
            payload = security.get_security_status(options, security.data_paths(DATA_DIR))
            key = "anti_xray" if parsed.path.endswith("anti-xray") else "grim"
            self.send_json({key: payload.get(key, {})})
            return
        if parsed.path == "/api/security/punishments":
            security = load_security_module()
            self.send_json(security.load_punishments(security.data_paths(DATA_DIR)))
            return
        if parsed.path == "/api/security/events":
            params = parse_qs(parsed.query)
            limit = safe_amount(params.get("limit", ["100"])[0], default=100, maximum=5000)
            security = load_security_module()
            self.send_json({"events": security.get_security_events(security.data_paths(DATA_DIR), limit)})
            return
        if parsed.path == "/api/security/xray":
            options = load_options()
            security = load_security_module()
            self.send_json({"xray_suspicion": security.xray_suspicion_status(options, security.data_paths(DATA_DIR))})
            return
        if parsed.path == "/security/punishments":
            self.send_html(render_security_punishments_page(is_admin=self.is_admin(load_options())))
            return
        if parsed.path == "/security/events":
            self.send_html(render_security_events_page())
            return
        if parsed.path == "/download":
            params = parse_qs(parsed.query)
            self.download(params.get("folder", ["plugins"])[0], params.get("name", [""])[0])
            return
        if parsed.path == "/playit_action":
            params = parse_qs(parsed.query)
            self.playit_action(params.get("action", [""])[0])
            return
        if parsed.path == "/command":
            self.redirect_home()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path in {"", "/", "/index.html"} and params.get("security_action"):
            self.handle_security_page_action(params.get("security_action", [""])[0])
            return
        if parsed.path == "/api/security/reconcile":
            options = load_options()
            if not self.is_admin(options):
                self.send_json({"error": "admin required"}, HTTPStatus.FORBIDDEN)
                return
            security = load_security_module()
            self.send_json({"security": security.prepare_security(options, security.data_paths(DATA_DIR))})
            return
        if parsed.path in {"/api/security/grim/install", "/api/security/grim/update", "/api/security/grim/enable", "/api/security/grim/disable", "/api/security/grim/test-beta"}:
            options = load_options()
            if not self.is_admin(options):
                self.send_json({"error": "admin required"}, HTTPStatus.FORBIDDEN)
                return
            if parsed.path.endswith("/disable"):
                options["grim_enabled"] = False
                write_options(options)
            elif parsed.path.endswith("/enable"):
                options["grim_enabled"] = True
                write_options(options)
            elif parsed.path.endswith("/test-beta"):
                options["grim_enabled"] = True
                options["grim_update_channel"] = "beta"
                write_options(options)
            security = load_security_module()
            payload = security.prepare_security(options, security.data_paths(DATA_DIR))
            self.send_json({"grim": payload.get("grim", {}), "security": payload})
            return
        if parsed.path == "/api/security/viaversion/fix":
            options = load_options()
            if not self.is_admin(options):
                self.send_json({"error": "admin required"}, HTTPStatus.FORBIDDEN)
                return
            security = load_security_module()
            try:
                self.send_json({"via_version": security.fix_viaversion_placement_rotation(security.data_paths(DATA_DIR))})
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/security/anti-xray":
            options = load_options()
            if not self.is_admin(options):
                self.send_json({"error": "admin required"}, HTTPStatus.FORBIDDEN)
                return
            body = self.read_json_body()
            if body is None:
                return
            for key in ("anti_xray_enabled", "anti_xray_engine_mode", "anti_xray_manage_defaults", "anti_xray_manage_nether", "anti_xray_manage_end"):
                if key in body:
                    options[key] = body[key]
            write_options(options)
            security = load_security_module()
            payload = security.prepare_security(options, security.data_paths(DATA_DIR))
            self.send_json({"anti_xray": payload.get("anti_xray", {})})
            return
        if parsed.path in {"/api/security/punishments/validate", "/api/security/punishments"}:
            options = load_options()
            if not self.is_admin(options):
                self.send_json({"error": "admin required"}, HTTPStatus.FORBIDDEN)
                return
            body = self.read_json_body()
            if body is None:
                return
            raw = str(body.get("yaml") or body.get("raw") or "")
            security = load_security_module()
            paths = security.data_paths(DATA_DIR)
            try:
                data = security.yaml.safe_load(raw) if security.yaml else None
                validation = security.validate_punishments_document(data)
                if parsed.path.endswith("/validate"):
                    self.send_json(validation)
                else:
                    payload = security.save_punishments(paths, raw, body.get("expected_sha") or None, bool(body.get("force")))
                    self.send_json(payload)
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/security/events/clear":
            options = load_options()
            if not self.is_admin(options):
                self.send_json({"error": "admin required"}, HTTPStatus.FORBIDDEN)
                return
            body = self.read_json_body()
            if body is None:
                return
            if body.get("confirm") != "clear":
                self.send_json({"error": "confirmation required"}, HTTPStatus.BAD_REQUEST)
                return
            security = load_security_module()
            security.atomic_write_text(security.data_paths(DATA_DIR)["events"], "")
            self.send_json({"events": []})
            return
        if parsed.path == "/api/security/xray/observation":
            options = load_options()
            if not self.is_admin(options):
                self.send_json({"error": "admin required"}, HTTPStatus.FORBIDDEN)
                return
            body = self.read_json_body()
            if body is None:
                return
            security = load_security_module()
            result = security.record_xray_observation(security.data_paths(DATA_DIR), options, body)
            self.send_json(result, HTTPStatus.OK if result.get("recorded") else HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/security/xray/import-current":
            options = load_options()
            if not self.is_admin(options):
                self.send_json({"error": "admin required"}, HTTPStatus.FORBIDDEN)
                return
            security = load_security_module()
            self.send_json(security.import_current_xray_stats(security.data_paths(DATA_DIR), options))
            return
        if parsed.path == "/api/security/xray/rules":
            options = load_options()
            if not self.is_admin(options):
                self.send_json({"error": "admin required"}, HTTPStatus.FORBIDDEN)
                return
            body = self.read_json_body()
            if body is None:
                return
            security = load_security_module()
            self.send_json({"rules": security.save_xray_rules(security.data_paths(DATA_DIR), body, options)})
            return
        if parsed.path == "/api/security/xray/reset":
            options = load_options()
            if not self.is_admin(options):
                self.send_json({"error": "admin required"}, HTTPStatus.FORBIDDEN)
                return
            security = load_security_module()
            self.send_json(security.reset_xray_suspicion(security.data_paths(DATA_DIR)))
            return
        if parsed.path == "/security/punishments/save":
            options = load_options()
            if not self.is_admin(options):
                self.send_html(render_security_punishments_page("Speichern ist nur im Adminmodus verfuegbar.", False), HTTPStatus.FORBIDDEN)
                return
            form = parse_urlencoded_form(self)
            if form is None:
                return
            security = load_security_module()
            try:
                security.save_punishments(security.data_paths(DATA_DIR), str(form.get("yaml") or ""), form.get("expected_sha") or None, False)
                self.send_html(render_security_punishments_page("punishments.yml gespeichert.", True))
            except Exception as exc:
                self.send_html(render_security_punishments_page(f"Speichern fehlgeschlagen: {exc}", True), HTTPStatus.BAD_REQUEST)
            return
        if parsed.path.startswith("/security/"):
            self.handle_security_page_action(parsed.path.rsplit("/", 1)[-1])
            return
        if parsed.path == "/command":
            params = parse_qs(parsed.query)
            form = parse_urlencoded_form(self)
            if form is None:
                return
            self.handle_command(params.get("action", [""])[0], form.get("slot", ""))
            return
        if parsed.path == "/upload":
            self.upload(parse_qs(parsed.query).get("folder", ["plugins"])[0])
            return
        if parsed.path == "/delete":
            params = parse_qs(parsed.query)
            self.delete(params.get("folder", ["plugins"])[0], params.get("name", [""])[0])
            return
        if parsed.path == "/jar_action":
            params = parse_qs(parsed.query)
            self.jar_action(params.get("action", [""])[0], params.get("name", [""])[0])
            return
        if parsed.path == "/save_config":
            self.save_config()
            return
        if parsed.path == "/backup_action":
            params = parse_qs(parsed.query)
            self.backup_action(params.get("action", [""])[0], params.get("backup", [""])[0])
            return
        if parsed.path == "/autorefresh_action":
            self.autorefresh_action()
            return
        if parsed.path == "/bedrock_action":
            params = parse_qs(parsed.query)
            self.bedrock_action(params.get("action", [""])[0])
            return
        if parsed.path == "/admin_login":
            self.admin_login()
            return
        if parsed.path == "/admin_logout":
            self.admin_logout()
            return
        if parsed.path == "/set_admin_password":
            self.set_admin_password()
            return
        if parsed.path == "/player_action":
            params = parse_qs(parsed.query)
            self.player_action(params.get("action", [""])[0], params.get("player", [""])[0])
            return
        if parsed.path == "/playit_action":
            params = parse_qs(parsed.query)
            self.playit_action(params.get("action", [""])[0])
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def handle_security_page_action(self, action):
        options = load_options()
        if action == "reconcile":
            if not self.is_admin(options):
                self.render_index("Security-Konfiguration ist nur im Adminmodus verfuegbar.", tab="admin")
                return
            security = load_security_module()
            security.prepare_security(options, security.data_paths(DATA_DIR))
            self.render_index("Security-Konfiguration wurde geprueft. Aenderungen wirken beim naechsten Neustart.", tab="admin")
            return
        if action == "grim_beta":
            if not self.is_admin(options):
                self.render_index("Grim-Vorabversionen koennen nur im Adminmodus aktiviert werden.", tab="admin")
                return
            options["grim_enabled"] = True
            options["grim_update_channel"] = "beta"
            write_options(options)
            security = load_security_module()
            security.prepare_security(options, security.data_paths(DATA_DIR))
            self.render_index("Grim Update-Kanal auf Vorabversion gestellt. Starte den Server neu, um die neue JAR zu testen.", tab="admin")
            return
        if action == "fix_viaversion":
            if not self.is_admin(options):
                self.render_index("ViaVersion kann nur im Adminmodus korrigiert werden.", tab="admin")
                return
            security = load_security_module()
            try:
                security.fix_viaversion_placement_rotation(security.data_paths(DATA_DIR))
                self.render_index("ViaVersion fix-1_21-placement-rotation wurde deaktiviert. Bitte Minecraft neu starten.", tab="admin")
            except Exception as exc:
                self.render_index(f"ViaVersion konnte nicht korrigiert werden: {exc}", tab="admin")
            return
        if action == "xray_import":
            if not self.is_admin(options):
                self.render_index("Xray-Stats importieren ist nur im Adminmodus verfuegbar.", tab="admin")
                return
            security = load_security_module()
            result = security.import_current_xray_stats(security.data_paths(DATA_DIR), options)
            self.render_index(f"Xray-Stats importiert: {result.get('imported', 0)} Beobachtungen.", tab="admin")
            return
        if action == "xray_reset":
            if not self.is_admin(options):
                self.render_index("Xray-Score zuruecksetzen ist nur im Adminmodus verfuegbar.", tab="admin")
                return
            security = load_security_module()
            security.reset_xray_suspicion(security.data_paths(DATA_DIR))
            self.render_index("Xray-Score wurde zurueckgesetzt. Beim naechsten Scan wird eine neue Baseline angelegt.", tab="admin")
            return
        if action == "xray_reset_player":
            if not self.is_admin(options):
                self.render_index("Xray-Score zuruecksetzen ist nur im Adminmodus verfuegbar.")
                return
            form = parse_urlencoded_form(self)
            if form is None:
                return
            security = load_security_module()
            try:
                result = security.reset_xray_player(security.data_paths(DATA_DIR), form.get("player", ""))
                self.render_index(f"Xray-Score fuer {result.get('player')} wurde zurueckgesetzt.", tab="players", selected_player=result.get("player", ""))
            except Exception as exc:
                self.render_index(f"Xray-Score konnte nicht zurueckgesetzt werden: {exc}", tab="players")
            return
        if action == "max_score_reset_player":
            if not self.is_admin(options):
                self.render_index("Max-Score zuruecksetzen ist nur im Adminmodus verfuegbar.")
                return
            form = parse_urlencoded_form(self)
            if form is None:
                return
            security = load_security_module()
            try:
                result = security.reset_player_max_score(security.data_paths(DATA_DIR), form.get("player", ""))
                self.render_index(f"Max-Score fuer {result.get('player')} wurde zurueckgesetzt.", tab="players", selected_player=result.get("player", ""))
            except Exception as exc:
                self.render_index(f"Max-Score konnte nicht zurueckgesetzt werden: {exc}", tab="players")
            return
        if action == "xray_rules":
            if not self.is_admin(options):
                self.render_index("Xray-Regeln speichern ist nur im Adminmodus verfuegbar.", tab="admin")
                return
            form = parse_urlencoded_form(self)
            if form is None:
                return
            security = load_security_module()
            rules = security.load_xray_rules(security.data_paths(DATA_DIR), options)
            payload = {
                "window_minutes": form.get("window_minutes"),
                "alert_threshold": form.get("alert_threshold"),
                "alert_cooldown_seconds": form.get("alert_cooldown_seconds"),
                "blocks": {},
            }
            for block, rule in (rules.get("blocks") or {}).items():
                block_id = str(block).replace("minecraft:", "")
                payload["blocks"][block] = {
                    "enabled": f"xray_enabled_{block_id}" in form,
                    "weight": form.get(f"xray_weight_{block_id}", rule.get("weight", 0)),
                }
            security.save_xray_rules(security.data_paths(DATA_DIR), payload, options)
            self.render_index("Xray-Regeln wurden gespeichert. Bestehende Beobachtungen werden beim naechsten Status-Poll neu bewertet.", tab="admin")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/security/events":
            options = load_options()
            if not self.is_admin(options):
                self.send_json({"error": "admin required"}, HTTPStatus.FORBIDDEN)
                return
            body = self.read_json_body()
            if body is None:
                return
            if body.get("confirm") != "clear":
                self.send_json({"error": "confirmation required"}, HTTPStatus.BAD_REQUEST)
                return
            security = load_security_module()
            security.atomic_write_text(security.data_paths(DATA_DIR)["events"], "")
            self.send_json({"events": []})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def is_admin(self, options):
        if self.is_ha_admin():
            return True
        if self.admin_user(options):
            return True
        password = admin_password(options)
        cookie = cookie_values(self.headers.get("Cookie")).get(ADMIN_COOKIE_NAME, "")
        return valid_admin_cookie(password, cookie)

    def is_ha_admin(self):
        return any(truthy_header(self.headers.get(name)) for name in HA_ADMIN_HEADERS)

    def admin_user(self, options):
        return matching_admin_user(self.headers, options)

    def render_index(self, message="", folder="plugins", cookie_header="", tab="dashboard", selected_player="", sort_key="name", direction="asc"):
        folder = folder_key(folder)
        tab = str(tab or "dashboard").lower()
        if tab in {"plugins", "mods"}:
            folder = tab
        if tab not in {"dashboard", "players", "security", "systemmessages", "backup", "plugins", "mods", "admin"}:
            tab = "dashboard"
        root = folder_dir(folder)
        status = status_payload()
        options = load_options()
        is_ha_admin = self.is_ha_admin()
        admin_user = self.admin_user(options)
        detected_users = forwarded_user_summary(self.headers)
        is_admin = self.is_admin(options)
        players_online = int(status.get("players_online") or 0)
        disruptive_disabled = players_online > 0
        control_state = str(status.get("control_state") or "")
        if status.get("online"):
            online_text = "Online"
            online_class = "good"
        elif control_state == "starting":
            online_text = "Start pending"
            online_class = "pending"
        elif control_state == "stopping":
            online_text = "Stop pending"
            online_class = "pending"
        else:
            online_text = "Offline"
            online_class = "muted"
        playit_state = str(status.get("playit_state") or "")
        pending_slot = str(status.get("pending_slot") or "")
        autorefresh_disabled_until = read_autorefresh_disabled_until(control_state)
        autorefresh_paused = autorefresh_disabled_until > int(time.time())
        auto_refresh = (
            "<meta http-equiv=\"refresh\" content=\"3\">"
            if not autorefresh_paused and (pending_slot or (control_state in {"starting", "stopping"} and not status.get("online")) or playit_state in {"starting", "stopping"})
            else ""
        )
        active_slot_value = str(status.get("active_slot", "CUSTOM"))
        active_slot = html.escape(active_slot_value)
        active_world = html.escape(str(status.get("active_world", "unknown")))
        server_name = html.escape(str(status.get("active_server_name", "unknown")))
        server_type = html.escape(str(status.get("server_type") or status.get("configured_version") or "unknown"))
        player_text = f"{players_online} / {status.get('players_max', 0)}"
        if int(status.get("players_bedrock") or 0):
            player_text = f"{players_online} (Java {status.get('players_java', 0)}, Bedrock {status.get('players_bedrock', 0)})"
        disable_attr = " disabled" if disruptive_disabled else ""
        restart_title = " title='Gesperrt, solange Spieler online sind'" if disruptive_disabled else ""
        server_online = bool(status.get("online"))
        server_pending = control_state in {"starting", "stopping"}
        can_start = not server_online and control_state != "starting"
        can_stop = server_online or control_state == "starting"
        can_restart = server_online and not disruptive_disabled
        slot_options = []
        slot_labels = {
            "CUSTOM": status.get("active_world", "CUSTOM"),
            "WORLD_1": status.get("world_1_name", "WORLD_1"),
            "WORLD_2": status.get("world_2_name", "WORLD_2"),
            "WORLD_3": status.get("world_3_name", "WORLD_3"),
        }
        for slot in ("CUSTOM", "WORLD_1", "WORLD_2", "WORLD_3"):
            selected = " selected" if slot == active_slot_value else ""
            label = html.escape(f"{slot} - {slot_labels.get(slot) or slot}")
            slot_options.append(f"<option value='{slot}'{selected}>{label}</option>")
        start_button = "<button type='submit' formaction='./command?action=start'>Start</button>" if can_start else ""
        switch_button = f"<button type='submit' formaction='./command?action=world_slot'{disable_attr}{restart_title}>Welt wechseln</button>"
        world_control_form = (
            "<form method='post' class='inline-controls control-form'>"
            f"<select name='slot'>{''.join(slot_options)}</select>"
            f"{start_button}"
            f"{switch_button}"
            "</form>"
        )
        pending_slot_hint = (
            f"<div class='hint'>Weltwechsel zu <code>{html.escape(pending_slot)}</code> ist angefordert.</div>"
            if pending_slot
            else ""
        )
        stop_form = (
            '<form method="post" action="./command?action=stop"><button class="danger" type="submit">Stop</button></form>'
            if can_stop
            else ""
        )
        restart_form = (
            f'<form method="post" action="./command?action=restart"><button class="warning" type="submit"{restart_title}>Neustart</button></form>'
            if can_restart
            else ""
        )
        kick_all_form = (
            "<form method='post' action='./player_action?action=kick_all' "
            "onsubmit=\"return confirm('Alle Spieler wirklich kicken?')\">"
            f"<input type='hidden' name='return_tab' value='{html.escape(tab)}'>"
            "<button class='warning' type='submit'>Kick all</button></form>"
            if is_admin
            else ""
        )
        for configured in FOLDERS.values():
            configured.mkdir(parents=True, exist_ok=True)
        rows = []
        for path in sorted(root.glob("*.jar"), key=lambda item: item.name.lower()):
            size_mb = path.stat().st_size / 1024 / 1024
            name = html.escape(path.name)
            url_name = quote(path.name)
            descriptor = jar_plugin_descriptor(path) if folder == "plugins" else {"name": "", "valid": None, "warning": ""}
            internal_name = html.escape(descriptor.get("name") or "-")
            if descriptor.get("valid") is False:
                jar_status = f"<span class='bad'>{html.escape(descriptor.get('warning') or 'ungueltig')}</span>"
                disable_form = (
                    f"<form method='post' action='./jar_action?action=disable&name={url_name}' "
                    "onsubmit=\"return confirm('Plugin-JAR deaktivieren und nach plugins-disabled verschieben?')\">"
                    "<button class='warning' type='submit'>Deaktivieren</button></form>"
                )
            elif descriptor.get("valid") is True:
                jar_status = "<span class='good'>OK</span>"
                disable_form = ""
            else:
                jar_status = "<span class='muted'>n/a</span>"
                disable_form = ""
            rows.append(
                f"<tr><td>{name}</td><td>{internal_name}</td><td>{jar_status}</td><td>{size_mb:.1f} MB</td>"
                f"<td><a class='button' href='./download?folder={folder}&name={url_name}'>Download</a>"
                f"{disable_form}"
                f"<form method='post' action='./delete?folder={folder}&name={url_name}' onsubmit=\"return confirm('Datei wirklich loeschen?')\">"
                f"<button class='danger' type='submit'>Loeschen</button></form></td></tr>"
            )
        table = "\n".join(rows) or "<tr><td colspan='5' class='empty'>Keine JARs vorhanden.</td></tr>"
        tabs = " ".join(
            f"<a class='tab {'active' if key == folder else ''}' href='./?tab={key}'>{label}</a>"
            for key, label in (("plugins", "Plugins"), ("mods", "Mods"))
        )
        top_tabs = " ".join(
            f"<a class='tab {'active' if key == tab else ''}' href='./?tab={key}'>{label}</a>"
            for key, label in (("dashboard", "Dashboard"), ("players", "Spieler"), ("security", "Security"), ("systemmessages", "Systemmessages"), ("backup", "Backup"), ("plugins", "Plugins"), ("mods", "Mods"), ("admin", "Admin"))
        )
        status_panel = f"""
  <div class="panel">
    <div class="grid">
      <div class="metric"><div class="label">Status</div><div class="value {online_class}">{online_text}</div></div>
      <div class="metric"><div class="label">Spieler</div><div class="value">{html.escape(str(player_text))}</div></div>
      <div class="metric"><div class="label">Welt</div><div class="value">{active_world}</div></div>
      <div class="metric"><div class="label">Slot</div><div class="value">{active_slot}</div></div>
      <div class="metric"><div class="label">Server</div><div class="value">{server_name}</div></div>
      <div class="metric"><div class="label">Typ</div><div class="value">{server_type}</div></div>
    </div>
  </div>"""
        control_panel = f"""
  <div class="panel control-bar">
    {world_control_form}
    {stop_form}
    {restart_form}
    {kick_all_form}
    {pending_slot_hint}
    {f"<div class='hint'>Neustart und Weltwechsel sind gesperrt, solange {players_online} Spieler online sind.</div>" if disruptive_disabled else ""}
  </div>"""
        jar_panel = f"""
  <h1>JAR Explorer</h1>
  <div class="tabs">{tabs}</div>
  <div class="panel">
    <div><strong>Ordner:</strong> <code>{html.escape(str(root))}</code></div>
    {f"<div class='message'>{html.escape(message)}</div>" if message else ""}
    <form method="post" action="./upload?folder={folder}" enctype="multipart/form-data">
      <input type="file" name="plugin" accept=".jar" required>
      <button type="submit">JAR hochladen</button>
    </form>
  </div>
  <div class="panel">
    <table>
      <thead><tr><th>Datei</th><th>Interner Name</th><th>Status</th><th>Groesse</th><th>Aktionen</th></tr></thead>
      <tbody>{table}</tbody>
    </table>
  </div>"""
        dashboard_content = f"""
  {status_panel}
  {control_panel}
  {render_security_panel(options, status, False)}
  {render_bedrock_panel(status, options, is_admin)}
  {render_playit_panel(options, status, is_admin) if (is_admin or bool(options.get("playit_enabled")) or str(options.get("playit_public_address") or "").strip()) else ""}
  {render_player_user_panel(options, status)}"""
        players_content = render_player_intelligence_panel(status, options, selected_player=selected_player, is_admin=is_admin, sort_key=sort_key, direction=direction)
        security_content = render_security_panel(options, status, False, show_moderation=True)
        systemmessages_content = render_systemmessages_panel(options, status, is_admin)
        backup_content = render_backup_panel(options, status, is_admin)
        admin_content = (
            f"""
  {render_admin_gate(options, is_admin, is_ha_admin, admin_user, detected_users)}
  {render_autorefresh_panel(autorefresh_disabled_until)}
  {render_security_panel(options, status, True)}
  {render_player_admin_panel(options, status)}
  {render_config_panel(options)}"""
            if is_admin
            else render_admin_gate(options, is_admin, is_ha_admin, admin_user, detected_users)
        )
        tab_content = {
            "dashboard": dashboard_content,
            "players": players_content,
            "security": security_content,
            "systemmessages": systemmessages_content,
            "backup": backup_content,
            "plugins": jar_panel,
            "mods": jar_panel,
            "admin": admin_content,
        }[tab]
        page = f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {auto_refresh}
  <title>Minecraft JAR Explorer</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, sans-serif; background: #111; color: #f5f5f5; }}
    body {{ margin: 0; padding: 20px; }}
    h1 {{ margin: 0 0 16px; font-size: 26px; }}
    .panel {{ background: #1b1b1b; border: 1px solid #333; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
    .tabs {{ margin-bottom: 16px; }}
    .tab {{ display: inline-block; background: #2a2a2a; color: white; border-radius: 6px; padding: 8px 12px; margin-right: 8px; text-decoration: none; }}
    .tab.active {{ background: #1f6fa8; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; border-bottom: 1px solid #333; padding: 10px 8px; vertical-align: middle; }}
    th {{ color: #9cc9ef; font-weight: 700; }}
    form {{ display: inline; }}
    h2 {{ margin: 0 0 14px; font-size: 22px; }}
    h3 {{ margin: 0 0 12px; font-size: 18px; color: #9cc9ef; }}
    input[type=file] {{ display: block; margin: 10px 0 14px; }}
    input, select {{ background: #303030; color: white; border: 1px solid #555; border-radius: 6px; padding: 9px; }}
    select {{ margin-right: 8px; }}
    button, .button {{ background: #1f6fa8; color: white; border: 0; border-radius: 6px; padding: 8px 12px; text-decoration: none; cursor: pointer; margin-right: 8px; }}
    button:disabled, select:disabled {{ opacity: .45; cursor: not-allowed; }}
    .danger {{ background: #a83232; }}
    .warning {{ background: #8a5d1d; }}
    .message {{ color: #9ee493; margin-bottom: 12px; }}
    .empty {{ color: #aaa; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
    .form-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }}
    .world-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }}
    .player-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; margin-bottom: 16px; }}
    .player-card {{ background: #1b1b1b; border: 1px solid #333; border-radius: 8px; padding: 16px; }}
    .player-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 14px; }}
    .player-head h3 {{ margin: 0 0 4px; color: #f5f5f5; }}
    .badges {{ display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }}
    .badge {{ display: inline-block; border: 1px solid #444; border-radius: 999px; padding: 3px 8px; font-size: 12px; font-weight: 800; }}
    .badge.good {{ border-color: #3f7d4c; background: #17351f; }}
    .badge.warning {{ border-color: #8a5d1d; background: #38260d; color: #ffd080; }}
    .badge.muted {{ color: #bbb; background: #252525; }}
    .action-section {{ border-top: 1px solid #333; padding-top: 12px; margin-top: 12px; }}
    .section-title {{ color: #9cc9ef; font-size: 13px; font-weight: 800; margin-bottom: 8px; }}
    .button-row {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .inline-controls {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .inline-controls input {{ flex: 1 1 220px; }}
    .inline-controls select {{ margin-right: 0; }}
    .small-input {{ max-width: 90px; }}
    .metric {{ background: #111; border: 1px solid #333; border-radius: 8px; padding: 12px; }}
    .metric .label {{ color: #9cc9ef; font-size: 13px; margin-bottom: 6px; }}
    .metric .value {{ font-size: 20px; font-weight: 700; }}
    .subpanel {{ background: #111; border: 1px solid #333; border-radius: 8px; padding: 12px; }}
    label {{ display: flex; flex-direction: column; gap: 6px; color: #9cc9ef; font-size: 13px; font-weight: 700; }}
    label.check {{ flex-direction: row; align-items: center; color: #f5f5f5; font-size: 14px; font-weight: 600; }}
    label.check input {{ width: 18px; height: 18px; }}
    .checks {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; margin-top: 14px; }}
    .config-form {{ display: block; }}
    .login-form {{ display: grid; grid-template-columns: minmax(180px, 320px) auto; gap: 10px; align-items: end; }}
    .control-bar {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .control-form {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .control-form select {{ min-width: 210px; }}
    .mode-panel p {{ margin-top: 0; }}
    .good {{ color: #9ee493; }}
    .pending {{ color: #ffd080; }}
    .muted {{ color: #aaa; }}
    .hint {{ color: #ffd080; margin-top: 10px; }}
    code {{ background: #090909; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Minecraft Server</h1>
  <div class="tabs">{top_tabs}</div>
  {f"<div class='message'>{html.escape(message)}</div>" if message else ""}
  {tab_content}
</body>
</html>"""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_no_cache_headers()
        if cookie_header:
            self.send_header("Set-Cookie", cookie_header)
        self.send_header("Content-Length", str(len(page.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))

    def redirect_home(self, cookie_header="", tab="dashboard"):
        tab = str(tab or "dashboard").lower()
        if tab not in {"dashboard", "players", "security", "systemmessages", "backup", "plugins", "mods", "admin"}:
            tab = "dashboard"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "./" if tab == "dashboard" else f"./?tab={tab}")
        self.send_no_cache_headers()
        if cookie_header:
            self.send_header("Set-Cookie", cookie_header)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def admin_login(self):
        form = parse_urlencoded_form(self)
        if form is None:
            return
        options = load_options()
        if self.is_admin(options):
            self.redirect_home(tab="admin")
            return
        password = admin_password(options)
        entered = str(form.get("ingress_admin_password") or "")
        if password and hmac.compare_digest(password, entered):
            self.redirect_home(cookie_header=set_cookie_header(make_admin_cookie(password)), tab="admin")
            return
        print(
            f"[plugin-browser] admin login failed; forwarded users: {forwarded_user_summary(self.headers)}; allowlist: {', '.join(sorted(admin_user_allowlist(options))) or 'empty'}",
            flush=True,
        )
        self.render_index("Admin-Passwort stimmt nicht.", tab="admin")

    def admin_logout(self):
        self.redirect_home(cookie_header=set_cookie_header("", max_age=0), tab="admin")

    def set_admin_password(self):
        form = parse_urlencoded_form(self)
        if form is None:
            return
        options = load_options()
        existing = admin_password(options)
        if not self.is_admin(options):
            self.render_index("Admin-Passwort kann nur im Adminmodus geaendert werden.", tab="admin")
            return
        new_password = str(form.get("ingress_admin_password") or "").strip()
        if len(new_password) < 4:
            self.render_index("Admin-Passwort muss mindestens 4 Zeichen haben.", tab="admin")
            return
        options["ingress_admin_password"] = new_password
        write_options(options)
        self.redirect_home(cookie_header=set_cookie_header(make_admin_cookie(new_password)), tab="admin")

    def player_action(self, action, player=""):
        options = load_options()
        is_admin = self.is_admin(options)
        user_actions = {"kick", "ban", "unban"}
        if not is_admin and action not in user_actions:
            self.render_index("Diese Spieler-Aktion ist nur im Adminmodus verfuegbar.")
            return
        form = parse_urlencoded_form(self)
        if form is None:
            return
        return_tab = str(form.get("return_tab") or "dashboard").lower()
        if return_tab not in {"dashboard", "players", "security", "systemmessages", "backup", "plugins", "mods", "admin"}:
            return_tab = "dashboard"
        def render_action_result(message):
            self.render_index(message, tab=return_tab)
        try:
            if action == "kick_all":
                players = online_players(options, log_errors=True)
                if not players:
                    render_action_result("Keine Spieler online oder RCON-Liste nicht verfuegbar.")
                    return
                for name in players:
                    rcon_command(options, f"kick {name} Serverwartung")
                render_action_result(f"{len(players)} Spieler wurden gekickt.")
                return
            if action == "broadcast":
                message = safe_broadcast_message(form.get("message", ""))
                if not message:
                    render_action_result("Broadcast-Nachricht ist leer.")
                    return
                rcon_command(options, f"say [Home Assistant] {message}")
                render_action_result("Broadcast wurde gesendet.")
                return

            safe_name = safe_player_name(unquote(player))
            if not safe_name:
                render_action_result("Ungueltiger Spielername.")
                return
            if action == "kick":
                rcon_command(options, f"kick {safe_name} Serverwartung")
                render_action_result(f"{safe_name} wurde gekickt.")
            elif action == "ban":
                rcon_command(options, f"ban {safe_name} Banned by Home Assistant")
                render_action_result(f"{safe_name} wurde gebannt.")
            elif action == "unban":
                rcon_command(options, f"pardon {safe_name}")
                render_action_result(f"{safe_name} wurde entbannt.")
            elif action == "op":
                rcon_command(options, f"op {safe_name}")
                render_action_result(f"{safe_name} hat OP-Rechte bekommen.")
            elif action == "deop":
                rcon_command(options, f"deop {safe_name}")
                render_action_result(f"{safe_name} hat keine OP-Rechte mehr.")
            elif action == "gamemode":
                gamemode = str(form.get("gamemode") or "").strip().lower()
                if gamemode not in VALID_GAMEMODES:
                    render_action_result("Ungueltiger Gamemode.")
                    return
                rcon_command(options, f"gamemode {gamemode} {safe_name}")
                render_action_result(f"{safe_name} ist jetzt im Gamemode {gamemode}.")
            elif action in {"give", "give_quick"}:
                item = safe_item_id(form.get("item", ""))
                amount = safe_amount(form.get("amount", "1"))
                if not item:
                    render_action_result("Ungueltige Item-ID.")
                    return
                rcon_command(options, f"give {safe_name} {item} {amount}")
                render_action_result(f"{safe_name} hat {amount}x {item} bekommen.")
            else:
                render_action_result("Unbekannte Spieler-Aktion.")
        except Exception as exc:
            render_action_result(f"Spieler-Aktion fehlgeschlagen: {exc}")

    def playit_action(self, action):
        options = load_options()
        is_admin = self.is_admin(options)
        form = parse_urlencoded_form(self)
        if form is None:
            return
        return_tab = str(form.get("return_tab") or "dashboard").lower()
        if return_tab not in {"dashboard", "admin"}:
            return_tab = "dashboard"
        if not is_admin and action != "stop":
            self.render_index("playit.gg Start und Neustart sind nur im Adminmodus verfuegbar.", tab=return_tab)
            return
        if not bool(options.get("playit_enabled")):
            self.render_index("", tab=return_tab)
            return
        if action in {"start", "restart"} and not str(options.get("playit_secret") or ""):
            self.render_index("playit.gg Secret Key fehlt.", tab=return_tab)
            return
        if action == "start":
            write_playit_state("starting")
            enqueue_command("playit_start")
            self.render_index("playit.gg Start wurde angefordert.", tab=return_tab)
        elif action == "stop":
            write_playit_state("stopping")
            enqueue_command("playit_stop")
            self.render_index("playit.gg Stop wurde angefordert.", tab=return_tab)
        elif action == "restart":
            write_playit_state("starting")
            enqueue_command("playit_restart")
            self.render_index("playit.gg Neustart wurde angefordert.", tab=return_tab)
        else:
            self.render_index("Unbekannte playit.gg-Aktion.", tab=return_tab)

    def backup_action(self, action, backup=""):
        options = load_options()
        if not self.is_admin(options):
            self.render_index("Backup-Aktionen sind nur im Adminmodus verfuegbar.", tab="backup")
            return
        form = parse_urlencoded_form(self)
        if form is None:
            return
        try:
            if action == "backup_now":
                world = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(form.get("world") or options.get("level_name") or "world")).strip("-")
                enqueue_command("backup_now", slot=world or "world")
                self.render_index("Backup wurde angefordert. Der Controller erstellt es im Hintergrund.", tab="backup")
                return
            safe_name = safe_backup_name(unquote(backup))
            if not safe_name:
                self.render_index("Ungueltiger Backup-Name.", tab="backup")
                return
            if action == "persist":
                set_backup_persistent(safe_name, True)
                self.render_index(f"{safe_name} ist jetzt persistent und wird von der Rotation ausgenommen.", tab="backup")
            elif action == "unpersist":
                set_backup_persistent(safe_name, False)
                self.render_index(f"Persistent-Flag fuer {safe_name} wurde entfernt.", tab="backup")
            elif action == "delete":
                delete_backup(safe_name)
                self.render_index(f"{safe_name} wurde geloescht.", tab="backup")
            elif action == "restore":
                if form.get("confirm") != "restore":
                    self.render_index("Restore braucht eine Bestaetigung.", tab="backup")
                    return
                write_control_state("stopping")
                enqueue_command("restore_backup", backup=safe_name)
                self.render_index(f"Restore von {safe_name} wurde angefordert. Der Server wird dafuer gestoppt.", tab="backup")
            else:
                self.render_index("Unbekannte Backup-Aktion.", tab="backup")
        except Exception as exc:
            self.render_index(f"Backup-Aktion fehlgeschlagen: {exc}", tab="backup")

    def jar_action(self, action, name=""):
        options = load_options()
        if not self.is_admin(options):
            self.render_index("JAR-Aktionen sind nur im Adminmodus verfuegbar.", tab="plugins")
            return
        form = parse_urlencoded_form(self)
        if form is None:
            return
        if action == "disable":
            try:
                destination = disable_plugin_jar(unquote(name))
                self.render_index(f"{Path(name).name} wurde nach {destination.parent.name} verschoben.", tab="plugins")
            except Exception as exc:
                self.render_index(f"Plugin-JAR konnte nicht deaktiviert werden: {exc}", tab="plugins")
            return
        self.render_index("Unbekannte JAR-Aktion.", tab="plugins")

    def autorefresh_action(self):
        options = load_options()
        if not self.is_admin(options):
            self.render_index("Auto-Refresh kann nur im Adminmodus geaendert werden.", tab="admin")
            return
        form = parse_urlencoded_form(self)
        if form is None:
            return
        if "disable_autorefresh" in form:
            write_autorefresh_disabled(300)
            self.render_index("Auto-Refresh ist fuer 5 Minuten pausiert.", tab="admin")
        else:
            clear_autorefresh_disabled()
            self.render_index("Auto-Refresh ist wieder aktiv.", tab="admin")

    def bedrock_action(self, action):
        options = load_options()
        if not self.is_admin(options):
            self.render_index("Bedrock-Steuerung ist nur im Adminmodus verfuegbar.", tab="dashboard")
            return
        form = parse_urlencoded_form(self)
        if form is None:
            return
        if action == "enable":
            options["bedrock_enabled"] = True
            write_options(options)
            self.render_index("Bedrock wurde aktiviert. Bitte Minecraft neu starten, damit Geyser/Floodgate vorbereitet werden.", tab="dashboard")
        elif action == "disable":
            options["bedrock_enabled"] = False
            write_options(options)
            self.render_index("Bedrock wurde deaktiviert. Bitte Minecraft neu starten, damit Geyser/Floodgate verschoben werden.", tab="dashboard")
        elif action == "restart":
            clear_autorefresh_disabled()
            write_control_state("starting")
            enqueue_command("restart")
            self.render_index("Minecraft-Neustart wurde angefordert, um Bedrock-Aenderungen anzuwenden.", tab="dashboard")
        else:
            self.render_index("Unbekannte Bedrock-Aktion.", tab="dashboard")

    def handle_command(self, action, slot=""):
        if action == "start":
            clear_autorefresh_disabled()
            write_control_state("starting")
            slot = slot.strip().upper()
            if slot in VALID_WORLD_SLOTS:
                write_pending_slot(slot)
                enqueue_command("world_slot", slot=slot)
            else:
                enqueue_command("start")
            self.redirect_home()
        elif action == "stop":
            write_control_state("stopping")
            enqueue_command("stop")
            self.redirect_home()
        elif action == "restart":
            clear_autorefresh_disabled()
            write_control_state("starting")
            enqueue_command("restart")
            self.redirect_home()
        elif action == "world_slot":
            slot = slot.strip().upper()
            if slot in VALID_WORLD_SLOTS:
                clear_autorefresh_disabled()
                write_control_state("starting")
                write_pending_slot(slot)
                enqueue_command("world_slot", slot=slot)
                self.redirect_home()
            else:
                self.render_index("Ungueltiger Welt-Slot.")
        else:
            self.render_index("Unbekannter Befehl.")

    def save_config(self):
        form = parse_urlencoded_form(self)
        if form is None:
            return
        try:
            options = load_options()
            if not self.is_admin(options):
                self.render_index("Konfiguration ist nur im Adminmodus verfuegbar.")
                return
            updates = coerce_config_updates(form)
            options.update(updates)
            write_options(options)
            new_password = updates.get("ingress_admin_password", "")
            cookie_header = set_cookie_header(make_admin_cookie(new_password)) if new_password else ""
            return_tab = str(form.get("return_tab") or "").strip().lower()
            if return_tab not in {"dashboard", "players", "security", "systemmessages", "backup", "plugins", "mods", "admin"}:
                return_tab = "admin"
            self.render_index(
                "Konfiguration gespeichert. Aenderungen wirken beim naechsten Serverstart oder Neustart.",
                cookie_header=cookie_header,
                tab=return_tab,
            )
        except Exception as exc:
            self.render_index(f"Konfiguration konnte nicht gespeichert werden: {exc}")

    def upload(self, folder):
        folder = folder_key(folder)
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        content_type = self.headers.get("Content-Type", "")
        boundary_match = re.search(r"boundary=([^;]+)", content_type)
        if not boundary_match:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        body = self.rfile.read(length)
        uploaded = 0
        folder_dir(folder).mkdir(parents=True, exist_ok=True)
        for filename, content in parse_multipart(body, boundary_match.group(1).encode("utf-8")):
            target = plugin_path(folder, filename)
            if target is None:
                continue
            tmp = target.with_suffix(target.suffix + ".uploading")
            tmp.write_bytes(content)
            tmp.replace(target)
            uploaded += 1
        self.render_index("Upload abgeschlossen." if uploaded else "Keine gueltige .jar-Datei gefunden.", folder=folder, tab=folder)

    def delete(self, folder, name):
        folder = folder_key(folder)
        path = plugin_path(folder, unquote(name))
        if path and path.is_file():
            path.unlink()
            self.render_index("Datei geloescht.", folder=folder, tab=folder)
        else:
            self.render_index("Datei nicht gefunden.", folder=folder, tab=folder)

    def download(self, folder, name):
        path = plugin_path(folder_key(folder), unquote(name))
        if not path or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/java-archive")
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile)

    def log_message(self, fmt, *args):
        print(f"[plugin-browser] {self.address_string()} {fmt % args}", flush=True)


def main():
    port = int(os.environ.get("MINECRAFT_PLUGIN_BROWSER_PORT", "8099"))
    for configured in FOLDERS.values():
        configured.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), PluginBrowser)
    print(f"[plugin-browser] Serving {DATA_DIR}/plugins and {DATA_DIR}/mods on port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
