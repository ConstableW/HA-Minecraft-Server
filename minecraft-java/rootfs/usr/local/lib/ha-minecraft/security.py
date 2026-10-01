#!/usr/bin/env python3
import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the add-on image installs python3-yaml
    yaml = None


MODRINTH_GRIM_VERSIONS = "https://api.modrinth.com/v2/project/grimac/version"
GRIM_ACTIVE = "GrimAC.jar"
GRIM_DISABLED = "GrimAC.jar.disabled"
GRIM_DOWNLOAD = ".GrimAC.jar.download"
GRIM_PATTERNS = (
    r"^GrimAC(?:[-_.].*)?\.jar$",
    r"^grimac(?:[-_.].*)?\.jar$",
    r"^grim(?:[-_.].*)?\.jar$",
    r"^Grim[-_.].*\.jar$",
)
MIN_GRIM_JAR_BYTES = 100_000
DEFAULT_EVENT_LIMIT = 500
MAX_YAML_BYTES = 512 * 1024
CRITICAL_GRIM_PATTERNS = {
    "packet_events_load": r"Failed to load PacketEventsInit",
    "packet_manager": r"Failed to start PacketManager",
    "packet_registration": r"Failed to register packets",
    "commands": r"Failed to register commands",
    "server_version_fallback": r"We will assume the Server version is V_1_8_8",
}


def log(message):
    print(f"[security] {message}", file=sys.stderr, flush=True)


def now_stamp():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bool_option(options, key, default=False):
    return bool(options.get(key, default))


def int_option(options, key, default, minimum=None, maximum=None):
    try:
        value = int(options.get(key, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def option_text(options, key, default=""):
    value = options.get(key, default)
    return str(value if value is not None else default).strip()


def active_world_name(options):
    slot = option_text(options, "world_slot", "CUSTOM").upper()
    if slot in {"WORLD_1", "WORLD_2", "WORLD_3"}:
        value = option_text(options, f"{slot.lower()}_name", "")
        if value:
            return value
    return option_text(options, "level_name", "world") or "world"


def data_paths(data_dir):
    root = Path(data_dir).resolve()
    state = root / "security"
    plugins = root / "plugins"
    return {
        "data": root,
        "plugins": plugins,
        "disabled_plugins": root / "plugins-disabled",
        "state": state,
        "state_file": state / "security-state.json",
        "events": state / "grim-events.jsonl",
        "xray_state": state / "xray-suspicion.json",
        "xray_rules": state / "xray-rules.json",
        "backups": state / "backups",
        "plugin_backups": plugins / ".security-backups",
        "punishments": plugins / "GrimAC" / "punishments.yml",
    }


def safe_load_yaml(path):
    if yaml is None:
        raise RuntimeError("PyYAML is required")
    if not path.is_file():
        return {}
    if path.stat().st_size > MAX_YAML_BYTES:
        raise RuntimeError(f"{path.name} is too large")
    with path.open("r", encoding="utf-8-sig") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{path.name} must contain a YAML mapping")
    return data


def atomic_write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_json(path, payload):
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def backup_file(path, backup_dir, keep=10):
    if not path.is_file():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{path.stem}-{now_stamp()}{path.suffix}"
    shutil.copy2(path, target)
    backups = sorted(backup_dir.glob(f"{path.stem}-*{path.suffix}"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def dump_yaml(data):
    if yaml is None:
        raise RuntimeError("PyYAML is required")
    return yaml.safe_dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def configure_paper_world(path, engine_mode, enabled, backup_dir, dimension):
    status = {"path": str(path), "changed": False, "created": False, "error": None}
    original_exists = path.is_file()
    data = safe_load_yaml(path) if original_exists else {}
    anti = data.setdefault("anticheat", {}).setdefault("anti-xray", {})
    if not isinstance(anti, dict):
        data["anticheat"]["anti-xray"] = {}
        anti = data["anticheat"]["anti-xray"]
    if anti.get("enabled") != enabled:
        anti["enabled"] = enabled
        status["changed"] = True
    if enabled and anti.get("engine-mode") != engine_mode:
        anti["engine-mode"] = engine_mode
        status["changed"] = True
    if enabled and "update-radius" not in anti:
        anti["update-radius"] = 2
        status["changed"] = True
    if enabled and "use-permission" not in anti:
        anti["use-permission"] = False
        status["changed"] = True
    if enabled and dimension == "nether":
        if "hidden-blocks" not in anti:
            anti["hidden-blocks"] = ["ancient_debris", "nether_gold_ore", "nether_quartz_ore"]
            status["changed"] = True
        if "replacement-blocks" not in anti:
            anti["replacement-blocks"] = ["netherrack", "basalt", "blackstone"]
            status["changed"] = True
    elif enabled and "hidden-blocks" not in anti and dimension == "default":
        anti["hidden-blocks"] = ["diamond_ore", "deepslate_diamond_ore", "ancient_debris", "gold_ore", "deepslate_gold_ore", "emerald_ore", "deepslate_emerald_ore"]
        anti["replacement-blocks"] = ["stone", "deepslate", "tuff"]
        status["changed"] = True
    if status["changed"] or not original_exists:
        if original_exists:
            backup_file(path, backup_dir)
        text = dump_yaml(data)
        yaml.safe_load(text)
        atomic_write_text(path, text)
        status["created"] = not original_exists
    return status


def read_anti_xray_values(path):
    if not path.is_file():
        return {}
    try:
        data = safe_load_yaml(path)
        anti = ((data.get("anticheat") or {}).get("anti-xray") or {})
        if not isinstance(anti, dict):
            return {}
        hidden = anti.get("hidden-blocks") if isinstance(anti.get("hidden-blocks"), list) else []
        replacement = anti.get("replacement-blocks") if isinstance(anti.get("replacement-blocks"), list) else []
        return {
            "enabled": anti.get("enabled"),
            "engine_mode": anti.get("engine-mode"),
            "use_permission": anti.get("use-permission"),
            "hidden_blocks_count": len(hidden),
            "replacement_blocks_count": len(replacement),
        }
    except Exception:
        return {}


def configure_anti_xray(options, paths):
    warnings = []
    error = None
    files = []
    configured = False
    engine_mode = int_option(options, "anti_xray_engine_mode", 2)
    if engine_mode not in {1, 2, 3}:
        warnings.append("Invalid anti_xray_engine_mode; using 2")
        engine_mode = 2
    requested = bool_option(options, "security_enabled", True) and bool_option(options, "anti_xray_enabled", True)
    server_type = str(options.get("server_type") or "PAPER").upper()
    if server_type not in {"PAPER", "PURPUR"}:
        warnings.append("Anti-Xray is only managed for Paper or Purpur")
        requested = False
    level = active_world_name(options)
    targets = []
    if bool_option(options, "anti_xray_manage_defaults", True):
        targets.append((paths["data"] / "config" / "paper-world-defaults.yml", "default"))
    if bool_option(options, "anti_xray_manage_nether", True):
        nether = paths["data"] / level / "dimensions" / "minecraft" / "the_nether" / "paper-world.yml"
        if nether.is_file():
            targets.append((nether, "nether"))
        else:
            warnings.append("No active Nether paper-world.yml found; using paper-world-defaults.yml only")
    if bool_option(options, "anti_xray_manage_end", False):
        end = paths["data"] / level / "dimensions" / "minecraft" / "the_end" / "paper-world.yml"
        if end.is_file():
            targets.append((end, "end"))
        else:
            warnings.append("No active End paper-world.yml found; using paper-world-defaults.yml only")
    try:
        for path, dimension in targets:
            result = configure_paper_world(path, engine_mode, requested, paths["backups"], dimension)
            files.append(str(path.relative_to(paths["data"])))
            configured = configured or result["changed"] or path.is_file()
    except Exception as exc:
        error = str(exc)
        warnings.append(error)
    effective_path = next((path for path, dimension in targets if dimension == "default" and path.is_file()), None)
    if effective_path is None:
        effective_path = next((path for path, _dimension in targets if path.is_file()), None)
    effective = read_anti_xray_values(effective_path) if effective_path else {}
    hidden_count = int(effective.get("hidden_blocks_count") or 0)
    replacement_count = int(effective.get("replacement_blocks_count") or 0)
    if requested and engine_mode in {2, 3} and (hidden_count == 0 or replacement_count == 0):
        warnings.append("Anti-Xray block lists are empty or missing; protection may be weak")
    return {
        "requested": requested,
        "configured": configured if requested else False,
        "effective_config_resolved": bool(effective),
        "active_world": level,
        "effective_enabled": effective.get("enabled"),
        "effective_engine_mode": effective.get("engine_mode"),
        "use_permission": effective.get("use_permission"),
        "hidden_blocks_count": hidden_count,
        "replacement_blocks_count": replacement_count,
        "engine_mode": engine_mode,
        "default_world_managed": bool_option(options, "anti_xray_manage_defaults", True),
        "nether_managed": bool_option(options, "anti_xray_manage_nether", True),
        "end_managed": bool_option(options, "anti_xray_manage_end", False),
        "config_files": files,
        "restart_required": False,
        "warnings": warnings,
        "error": error,
    }


def jar_plugin_name(path):
    try:
        with zipfile.ZipFile(path) as jar:
            for name in ("plugin.yml", "paper-plugin.yml"):
                if name in jar.namelist():
                    text = jar.read(name).decode("utf-8", errors="replace")
                    match = re.search(r"(?im)^name:\s*['\"]?([^'\"\r\n#]+)", text)
                    if match:
                        return match.group(1).strip()
    except (OSError, zipfile.BadZipFile):
        return ""
    return ""


def is_grim_jar(path):
    if not path.is_file() or path.stat().st_size < MIN_GRIM_JAR_BYTES:
        return False
    name_match = any(re.match(pattern, path.name, re.IGNORECASE) for pattern in GRIM_PATTERNS)
    plugin_name = jar_plugin_name(path).lower()
    return name_match and ("grim" in plugin_name or plugin_name == "grimac")


def find_grim_jars(directory):
    if not directory.is_dir():
        return []
    candidates = []
    for path in sorted(directory.glob("*.jar"), key=lambda item: item.name.lower()):
        if any(re.match(pattern, path.name, re.IGNORECASE) for pattern in GRIM_PATTERNS):
            candidates.append(path)
    return candidates


def validate_downloaded_grim(path):
    if not path.is_file() or path.stat().st_size < MIN_GRIM_JAR_BYTES:
        raise RuntimeError("downloaded Grim jar is too small")
    if not zipfile.is_zipfile(path):
        raise RuntimeError("downloaded Grim file is not a valid jar")
    plugin_name = jar_plugin_name(path).lower()
    if "grim" not in plugin_name:
        raise RuntimeError("downloaded jar does not identify as Grim")


def active_server_label(options):
    slot = option_text(options, "world_slot", "CUSTOM").upper()
    if slot in {"WORLD_1", "WORLD_2", "WORLD_3"}:
        index = slot.rsplit("_", 1)[-1].lower()
        server_name = option_text(options, f"world_{index}_server_name", "")
        world_name = option_text(options, f"world_{index}_name", "")
        if server_name:
            return server_name
        if world_name:
            return world_name
    return option_text(options, "server_name", "") or option_text(options, "level_name", "minecraft")


def replace_grim_server_name_keys(value, server_name):
    changed = False
    if isinstance(value, dict):
        updated = {}
        for key, child in value.items():
            if str(key) in {"serverName", "server-name", "server_name"} and child != server_name:
                updated[key] = server_name
                changed = True
            else:
                new_child, child_changed = replace_grim_server_name_keys(child, server_name)
                updated[key] = new_child
                changed = changed or child_changed
        return updated, changed
    if isinstance(value, list):
        updated_items = []
        for item in value:
            new_item, item_changed = replace_grim_server_name_keys(item, server_name)
            updated_items.append(new_item)
            changed = changed or item_changed
        return updated_items, changed
    return value, False


def configure_grim_datastore_server_name(options, paths):
    grim_dir = paths["plugins"] / "GrimAC"
    server_name = active_server_label(options)
    if not server_name or not grim_dir.is_dir() or yaml is None:
        return {"changed": False, "server_name": server_name, "files": []}
    changed_files = []
    for config in sorted(list(grim_dir.rglob("*.yml")) + list(grim_dir.rglob("*.yaml")), key=lambda item: str(item).lower()):
        if config.name.lower() == "punishments.yml" or config.stat().st_size > MAX_YAML_BYTES:
            continue
        try:
            data = safe_load_yaml(config)
            updated, changed = replace_grim_server_name_keys(data, server_name)
            if not changed:
                continue
            backup_file(config, paths["backups"], keep=5)
            text = dump_yaml(updated)
            yaml.safe_load(text)
            atomic_write_text(config, text)
            changed_files.append(str(config.relative_to(paths["data"])))
        except Exception:
            continue
    return {"changed": bool(changed_files), "server_name": server_name, "files": changed_files}


def modrinth_grim_versions(timeout=20):
    query = "?loaders=%5B%22bukkit%22,%22paper%22,%22purpur%22,%22spigot%22,%22folia%22%5D"
    req = urllib.request.Request(MODRINTH_GRIM_VERSIONS + query, headers={"User-Agent": "ha-minecraft-addon/1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"Modrinth returned HTTP {response.status}")
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def version_key(value):
    pieces = re.split(r"[^0-9A-Za-z]+", str(value or ""))
    key = []
    for piece in pieces:
        if not piece:
            continue
        key.append((0, int(piece)) if piece.isdigit() else (1, piece.lower()))
    return key


def version_newer(left, right):
    return version_key(left) > version_key(right)


def select_modrinth_file(version):
    files = version.get("files") or []
    return next((item for item in files if item.get("primary")), None) or (files[0] if files else None)


def version_payload(version):
    primary = select_modrinth_file(version)
    if not primary or not primary.get("url"):
        return None
    version_type = str(version.get("version_type") or "release")
    return {
        "version": str(version.get("version_number") or ""),
        "url": primary["url"],
        "sha512": (primary.get("hashes") or {}).get("sha512", ""),
        "source": "modrinth",
        "channel": "stable" if version_type == "release" else "beta_or_snapshot",
        "version_type": version_type,
        "game_versions": version.get("game_versions") or [],
        "loaders": version.get("loaders") or [],
    }


def server_minecraft_version(options, paths):
    configured = option_text(options, "minecraft_version", "")
    if configured and configured.upper() != "LATEST":
        return configured
    text = read_latest_log(paths)
    match = re.search(r"Loading Purpur [^\n]+ for Minecraft ([0-9][0-9A-Za-z_.-]*)", text)
    if match:
        return match.group(1)
    match = re.search(r"Starting minecraft server version ([0-9][0-9A-Za-z_.-]*)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def latest_modrinth_grim_info(game_version="", timeout=20):
    versions = modrinth_grim_versions(timeout)
    payloads = [payload for payload in (version_payload(version) for version in versions) if payload]
    if not payloads:
        raise RuntimeError("no Grim releases found on Modrinth")
    considered = payloads
    if game_version:
        compatible = [payload for payload in payloads if game_version in payload.get("game_versions", [])]
        if compatible:
            considered = compatible
    stable = next((payload for payload in payloads if payload["version_type"] == "release"), None)
    compatible_stable = next((payload for payload in considered if payload["version_type"] == "release"), None)
    available = max(considered, key=lambda item: version_key(item["version"]))
    return {
        "stable": compatible_stable or stable,
        "available": available,
        "all": payloads,
        "game_version": game_version,
        "game_version_matched": considered is not payloads,
    }


def latest_modrinth_grim(timeout=20):
    info = latest_modrinth_grim_info(timeout=timeout)
    if info["stable"]:
        return info["stable"]
    raise RuntimeError("no stable Grim release found on Modrinth")


def download_file(url, target, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "ha-minecraft-addon/1"})
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"download returned HTTP {response.status}")
        tmp = target
        with tmp.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def move_to_disabled(path, paths):
    paths["disabled_plugins"].mkdir(parents=True, exist_ok=True)
    destination = paths["disabled_plugins"] / path.name
    if destination.exists():
        destination = paths["disabled_plugins"] / f"{path.stem}-{now_stamp()}{path.suffix}"
    shutil.move(str(path), str(destination))
    return destination


def read_latest_log(paths, max_bytes=750_000):
    path = paths["data"] / "logs" / "latest.log"
    if not path.is_file():
        return ""
    try:
        with path.open("rb") as handle:
            if path.stat().st_size > max_bytes:
                handle.seek(-max_bytes, os.SEEK_END)
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def parse_grim_update_from_log(text):
    match = re.search(r"New GrimAC version found!\s+Version\s+([A-Za-z0-9_.+-]+)\s+is available", text)
    if not match:
        return None
    version = match.group(1)
    return {"version": version, "channel": "beta_or_snapshot" if "-" in version else "stable", "source": "grim_log"}


def parse_grim_runtime_status(paths):
    text = read_latest_log(paths)
    status = {
        "plugin_discovered": False,
        "loaded": None,
        "enabled": None,
        "packet_manager_active": None,
        "commands_registered": None,
        "healthy": None,
        "runtime_compatible": None,
        "runtime_detection": "unknown",
        "detected_server_version_by_grim": None,
        "runtime_error": None,
        "runtime_warnings": [],
        "grim_reported_update": parse_grim_update_from_log(text),
        "runtime_version": None,
    }
    if not text:
        return status
    grim_lines = [line for line in text.splitlines() if "GrimAC" in line or "Grim Version:" in line]
    if not grim_lines:
        return status
    joined = "\n".join(grim_lines)
    status["plugin_discovered"] = bool(re.search(r"(GrimAC \(|Loading server plugin GrimAC|Enabling GrimAC)", joined))
    if re.search(r"Loading server plugin GrimAC", joined):
        status["loaded"] = True
    if re.search(r"Enabling GrimAC", joined):
        status["enabled"] = True
    if re.search(r"Registering packets", joined):
        status["packet_manager_active"] = None
    if re.search(r"Registering tick schedulers", joined):
        status["packet_manager_active"] = True
    if re.search(r"Successfully registered internal expansion: grim", text, re.IGNORECASE):
        status["commands_registered"] = None
    version_match = re.search(r"(?:Loading server plugin GrimAC v|Enabling GrimAC v|Grim Version:\s*)([A-Za-z0-9_.+-]+)", joined)
    if version_match:
        status["runtime_version"] = version_match.group(1)
    critical_hits = []
    for key, pattern in CRITICAL_GRIM_PATTERNS.items():
        if re.search(pattern, joined, re.IGNORECASE):
            critical_hits.append(key)
    if "packet_events_load" in critical_hits:
        status["loaded"] = False
    if "packet_manager" in critical_hits or "packet_registration" in critical_hits:
        status["packet_manager_active"] = False
        status["enabled"] = False
    if "commands" in critical_hits:
        status["commands_registered"] = False
    if "server_version_fallback" in critical_hits:
        status["runtime_detection"] = "failed"
        status["detected_server_version_by_grim"] = "V_1_8_8 fallback"
        status["runtime_compatible"] = False
    found_match = re.search(r"Your server software is preventing us from checking the Minecraft Server version\.\s+This is what we found:\s+([^.]+(?:\.[^.\s]+)*)", joined)
    if found_match:
        status["server_version_seen_by_grim"] = found_match.group(1)
    if critical_hits:
        status["healthy"] = False
        status["runtime_compatible"] = False
        if status["runtime_detection"] == "unknown":
            status["runtime_detection"] = "failed"
        if "packet_events_load" in critical_hits or "packet_manager" in critical_hits:
            status["runtime_error"] = "PacketEvents could not start on this server version"
        elif "commands" in critical_hits:
            status["runtime_error"] = "Grim commands could not be registered"
        else:
            status["runtime_error"] = "Grim runtime check failed"
        status["runtime_warnings"] = [line[-300:] for line in grim_lines if any(re.search(pattern, line, re.IGNORECASE) for pattern in CRITICAL_GRIM_PATTERNS.values())]
    elif status["plugin_discovered"] and status["loaded"] and status["enabled"]:
        status["healthy"] = status["packet_manager_active"] is not False and status["commands_registered"] is not False
        status["runtime_compatible"] = True
        status["runtime_detection"] = "ok"
    return status


def prepare_grim(options, paths):
    requested = bool_option(options, "security_enabled", True) and bool_option(options, "grim_enabled", True)
    auto_update = bool_option(options, "grim_auto_update", True)
    auto_compatible_beta = bool_option(options, "grim_auto_compatible_beta", True)
    update_channel = option_text(options, "grim_update_channel", "stable")
    if update_channel not in {"stable", "beta"}:
        update_channel = "stable"
    plugins = paths["plugins"]
    plugins.mkdir(parents=True, exist_ok=True)
    paths["disabled_plugins"].mkdir(parents=True, exist_ok=True)
    warnings = []
    error = None
    active = plugins / GRIM_ACTIVE
    disabled = plugins / GRIM_DISABLED
    latest_info = None
    latest = None
    latest_available = None
    source = None
    changed = False
    if requested and str(options.get("server_type") or "PAPER").upper() == "VANILLA":
        requested = False
        warnings.append("Grim requires Paper or Purpur; Vanilla cannot load Grim")
    if not requested:
        if active.exists():
            if disabled.exists():
                backup_file(active, paths["plugin_backups"], keep=2)
                active.unlink()
            else:
                active.replace(disabled)
            changed = True
        for candidate in find_grim_jars(plugins):
            if candidate.name != GRIM_ACTIVE:
                move_to_disabled(candidate, paths)
                warnings.append(f"Disabled extra Grim jar {candidate.name}")
        return {
            "requested": False,
            "installed": disabled.exists() or active.exists(),
            "enabled_on_disk": False,
            "loaded": None,
            "compatible": None,
            "version": None,
            "latest_version": None,
            "latest_stable_version": None,
            "latest_available_version": None,
            "latest_available_channel": None,
            "download_source": None,
            "update_available": False,
            "auto_update": auto_update,
            "auto_compatible_beta": auto_compatible_beta,
            "update_channel": update_channel,
            "restart_required": changed,
            "warnings": warnings,
            "error": None,
        }
    for candidate in find_grim_jars(paths["disabled_plugins"]):
        if not active.exists():
            shutil.move(str(candidate), str(active))
            changed = True
            break
    for candidate in find_grim_jars(plugins):
        if candidate.name != GRIM_ACTIVE:
            move_to_disabled(candidate, paths)
            warnings.append(f"Disabled extra Grim jar {candidate.name}")
            changed = True
    try:
        latest_info = latest_modrinth_grim_info(server_minecraft_version(options, paths))
        if latest_info.get("game_version") and not latest_info.get("game_version_matched"):
            warnings.append(f"No Grim Modrinth release declares Minecraft {latest_info['game_version']}; showing unfiltered release data")
        latest = latest_info.get("stable")
        latest_available = latest_info.get("available")
        if update_channel == "beta" and latest_available:
            latest = latest_available
        elif auto_compatible_beta and latest_available and latest_available.get("channel") == "beta_or_snapshot":
            latest = latest_available
            warnings.append("Using compatible Grim beta/snapshot for this Minecraft version")
        source = latest["source"] if latest else None
    except Exception as exc:
        warnings.append(f"Could not check latest Grim release: {exc}")
    try:
        should_download = not active.exists()
        if latest and auto_update and active.exists():
            meta = read_state(paths).get("grim", {})
            should_download = meta.get("version") not in {"", latest.get("version")}
        if latest and should_download:
            tmp = plugins / GRIM_DOWNLOAD
            try:
                tmp.unlink()
            except OSError:
                pass
            download_file(latest["url"], tmp)
            validate_downloaded_grim(tmp)
            if active.exists():
                backup_file(active, paths["plugin_backups"], keep=2)
            tmp.replace(active)
            changed = True
    except Exception as exc:
        error = str(exc)
        warnings.append(error)
        try:
            (plugins / GRIM_DOWNLOAD).unlink()
        except OSError:
            pass
    installed = active.is_file()
    datastore_config = configure_grim_datastore_server_name(options, paths) if installed else {"changed": False, "server_name": active_server_label(options), "files": []}
    if datastore_config.get("changed"):
        warnings.append(f"Updated Grim datastore serverName to {datastore_config.get('server_name')}")
        changed = True
    state_meta = read_state(paths).get("grim", {})
    installed_version = state_meta.get("installed_version") or state_meta.get("version")
    if latest and changed:
        installed_version = latest.get("version")
    runtime = parse_grim_runtime_status(paths)
    if not installed_version and runtime.get("runtime_version"):
        installed_version = runtime["runtime_version"]
    log_update = runtime.get("grim_reported_update")
    latest_available_version = (latest_available or {}).get("version")
    latest_available_channel = (latest_available or {}).get("channel")
    if log_update and (
        not latest_available_version or version_newer(log_update["version"], latest_available_version)
    ):
        latest_available_version = log_update["version"]
        latest_available_channel = log_update["channel"]
        warnings.append("Grim reports a newer version than the add-on selected from the stable channel")
    update_available = bool(installed_version and latest_available_version and version_newer(latest_available_version, installed_version))
    return {
        "requested": True,
        "installed": installed,
        "enabled_on_disk": installed,
        "plugin_discovered": runtime.get("plugin_discovered"),
        "loaded": runtime.get("loaded"),
        "enabled": runtime.get("enabled"),
        "packet_manager_active": runtime.get("packet_manager_active"),
        "commands_registered": runtime.get("commands_registered"),
        "healthy": runtime.get("healthy"),
        "compatible": runtime.get("runtime_compatible"),
        "declared_compatible": None,
        "runtime_compatible": runtime.get("runtime_compatible"),
        "runtime_detection": runtime.get("runtime_detection"),
        "detected_server_version_by_grim": runtime.get("detected_server_version_by_grim"),
        "runtime_error": runtime.get("runtime_error"),
        "version": installed_version,
        "installed_version": installed_version,
        "latest_version": (latest or {}).get("version") if latest else None,
        "latest_stable_version": (latest_info.get("stable") or {}).get("version") if latest_info else None,
        "latest_available_version": latest_available_version,
        "latest_available_channel": latest_available_channel,
        "download_source": source,
        "update_available": update_available,
        "auto_update": auto_update,
        "auto_compatible_beta": auto_compatible_beta,
        "datastore_server_name": datastore_config.get("server_name"),
        "datastore_config_files": datastore_config.get("files", []),
        "update_channel": update_channel,
        "restart_required": changed,
        "warnings": warnings + runtime.get("runtime_warnings", []),
        "error": (runtime.get("runtime_error") or error) if (not installed or runtime.get("healthy") is False) else None,
    }


def read_state(paths):
    try:
        data = json.loads(paths["state_file"].read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(paths, payload):
    payload["updated_at"] = iso_now()
    atomic_write_json(paths["state_file"], payload)


def file_revision(path):
    if not path.is_file():
        return {"exists": False, "mtime": None, "size": 0, "sha256": None}
    stat = path.stat()
    return {"exists": True, "mtime": stat.st_mtime, "size": stat.st_size, "sha256": sha256_file(path)}


def validate_punishments_document(data):
    warnings = []
    errors = []
    if not isinstance(data, dict):
        errors.append("YAML root must be a mapping")
        return {"valid": False, "warnings": warnings, "errors": errors}
    punishments = data.get("Punishments")
    if punishments is None:
        warnings.append("Missing Punishments section")
    elif not isinstance(punishments, dict):
        errors.append("Punishments must be a mapping")
    else:
        for category, payload in punishments.items():
            if not isinstance(payload, dict):
                errors.append(f"{category}: category must be a mapping")
                continue
            commands = payload.get("commands", [])
            if commands is None:
                commands = []
            if not isinstance(commands, list):
                errors.append(f"{category}: commands must be a list")
                continue
            for command in commands:
                if not isinstance(command, str) or not re.match(r"^\d+:\d+\s+\S+", command):
                    warnings.append(f"{category}: custom or unknown command syntax: {command}")
    return {"valid": not errors, "warnings": warnings, "errors": errors}


def load_punishments(paths):
    path = paths["punishments"]
    raw = path.read_text(encoding="utf-8") if path.is_file() else ""
    data = safe_load_yaml(path) if path.is_file() else {"Punishments": {}}
    validation = validate_punishments_document(data)
    punishments = data.get("Punishments") if isinstance(data, dict) else {}
    categories = []
    if isinstance(punishments, dict):
        for name, payload in punishments.items():
            payload = payload if isinstance(payload, dict) else {}
            categories.append(
                {
                    "name": str(name),
                    "remove_violations_after": payload.get("remove-violations-after"),
                    "checks": payload.get("checks") if isinstance(payload.get("checks"), list) else [],
                    "commands": payload.get("commands") if isinstance(payload.get("commands"), list) else [],
                }
            )
    return {
        "path": str(path),
        "raw": raw,
        "revision": file_revision(path),
        "valid": validation["valid"],
        "warnings": validation["warnings"],
        "errors": validation["errors"],
        "categories": categories,
    }


def save_punishments(paths, raw_text, expected_sha=None, force=False):
    path = paths["punishments"]
    if len(raw_text.encode("utf-8")) > MAX_YAML_BYTES:
        raise RuntimeError("punishments.yml is too large")
    current = file_revision(path)
    if expected_sha and current["sha256"] != expected_sha and not force:
        raise RuntimeError("punishments.yml changed outside the Ingress editor")
    data = yaml.safe_load(raw_text) if yaml else None
    validation = validate_punishments_document(data)
    if not validation["valid"]:
        raise RuntimeError("; ".join(validation["errors"]))
    if path.is_file():
        backup_file(path, paths["backups"])
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(raw_text, encoding="utf-8")
    yaml.safe_load(tmp.read_text(encoding="utf-8"))
    tmp.replace(path)
    append_event(paths, {"action": "punishments_saved", "source": "ingress", "raw": None})
    return load_punishments(paths)


def append_event(paths, event):
    paths["state"].mkdir(parents=True, exist_ok=True)
    payload = {
        "id": str(event.get("id") or uuid.uuid4()),
        "timestamp": event.get("timestamp") or iso_now(),
        "player": event.get("player"),
        "check": event.get("check"),
        "category": event.get("category"),
        "violation_level": event.get("violation_level"),
        "verbose": event.get("verbose"),
        "action": event.get("action"),
        "source": event.get("source") or "addon",
        "raw": event.get("raw"),
    }
    with paths["events"].open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


XRAY_BLOCK_WEIGHTS = {
    "minecraft:diamond_ore": 4,
    "minecraft:deepslate_diamond_ore": 5,
    "minecraft:ancient_debris": 10,
    "minecraft:emerald_ore": 4,
    "minecraft:deepslate_emerald_ore": 5,
    "minecraft:gold_ore": 1,
    "minecraft:deepslate_gold_ore": 1,
    "minecraft:nether_gold_ore": 0,
}


DEFAULT_XRAY_RULES = {
    "window_minutes": 20,
    "alert_threshold": 120,
    "alert_cooldown_seconds": 300,
    "series_bonus": [
        {"hits": 12, "bonus": 10},
        {"hits": 20, "bonus": 25},
        {"hits": 30, "bonus": 50},
    ],
    "blocks": {
        block: {"enabled": weight > 0, "weight": weight}
        for block, weight in XRAY_BLOCK_WEIGHTS.items()
    },
}


def safe_player(value):
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z0-9_.]{1,32}", text) else ""


def normalized_block(value):
    block = str(value or "").strip().lower()
    if not block:
        return ""
    if ":" not in block:
        block = f"minecraft:{block}"
    return block if re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_./-]+", block) else ""


def read_xray_state(paths):
    try:
        data = json.loads(paths["xray_state"].read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_xray_state(paths, state):
    atomic_write_json(paths["xray_state"], state)


def xray_rule_blocks(rules):
    blocks = rules.get("blocks") if isinstance(rules, dict) else {}
    return blocks if isinstance(blocks, dict) else {}


def tracked_xray_blocks(rules):
    return {
        normalized_block(block): payload
        for block, payload in xray_rule_blocks(rules).items()
        if normalized_block(block) and isinstance(payload, dict) and bool(payload.get("enabled", True)) and int(payload.get("weight") or 0) > 0
    }


def sanitize_xray_rules(payload, options=None):
    options = options or {}
    defaults = copy.deepcopy(DEFAULT_XRAY_RULES)
    source = payload if isinstance(payload, dict) else {}
    defaults["window_minutes"] = int_option(source or options, "window_minutes", int_option(options, "xray_suspicion_window_minutes", 20, 5, 240), 5, 240)
    defaults["alert_threshold"] = int_option(source or options, "alert_threshold", int_option(options, "xray_suspicion_alert_threshold", 120, 10, 500), 10, 500)
    defaults["alert_cooldown_seconds"] = int_option(source or options, "alert_cooldown_seconds", int_option(options, "xray_suspicion_alert_cooldown_seconds", 300, 30, 3600), 30, 3600)
    blocks = source.get("blocks") if isinstance(source.get("blocks"), dict) else {}
    for block in list(DEFAULT_XRAY_RULES["blocks"]):
        incoming = blocks.get(block) if isinstance(blocks.get(block), dict) else {}
        defaults["blocks"][block] = {
            "enabled": bool(incoming.get("enabled", defaults["blocks"][block]["enabled"])),
            "weight": max(0, min(50, int(incoming.get("weight", defaults["blocks"][block]["weight"]) or 0))),
        }
    return defaults


def load_xray_rules(paths, options=None):
    try:
        data = json.loads(paths["xray_rules"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return sanitize_xray_rules(data, options or {})


def save_xray_rules(paths, payload, options=None):
    rules = sanitize_xray_rules(payload, options or {})
    atomic_write_json(paths["xray_rules"], rules)
    return rules


def xray_weight(block, visible=False, rules=None):
    rules = rules or DEFAULT_XRAY_RULES
    entry = tracked_xray_blocks(rules).get(normalized_block(block))
    if not entry:
        return 0
    base = int(entry.get("weight") or 0)
    if base <= 0:
        return 0
    if visible is None:
        return base
    return max(1, base // 2) if visible else base


def score_xray_observations(observations, rules, now_epoch=None):
    now_epoch = float(now_epoch or time.time())
    window_seconds = int(rules.get("window_minutes") or 20) * 60
    tracked = tracked_xray_blocks(rules)
    kept = [
        item
        for item in observations
        if (
            isinstance(item, dict)
            and normalized_block(item.get("block")) in tracked
            and xray_weight(item.get("block"), item.get("visible"), rules) > 0
            and now_epoch - float(item.get("timestamp_epoch", 0)) <= window_seconds
        )
    ]
    ore_hits = len(kept)
    hidden_hits = sum(1 for item in kept if item.get("visible") is False)
    unknown_visibility_hits = sum(1 for item in kept if item.get("visible") is None)
    score = sum(xray_weight(item.get("block"), item.get("visible"), rules) for item in kept)
    for bonus in rules.get("series_bonus") or []:
        if isinstance(bonus, dict) and ore_hits >= int(bonus.get("hits") or 0):
            score += int(bonus.get("bonus") or 0)
    return {
        "observations": kept[-200:],
        "score": score,
        "hidden_hits": hidden_hits,
        "unknown_visibility_hits": unknown_visibility_hits,
        "ore_hits": ore_hits,
    }


def build_xray_alert_command(player, score, block):
    safe_name = safe_player(player) or "unbekannter Spieler"
    safe_block = normalized_block(block).replace("minecraft:", "") or "unbekannter Fund"
    return f"say [Security] Cheatverdacht: {safe_name} hat Xray-Score {int(score)} erreicht ({safe_block}). Bitte pruefen."


def rcon_security_alert(options, command):
    if not bool_option(options, "enable_rcon", False):
        return {"sent": False, "reason": "RCON disabled"}
    password = option_text(options, "rcon_password", "")
    if not password:
        return {"sent": False, "reason": "RCON password missing"}
    port = int_option(options, "rcon_port", 25575, 1, 65535)
    try:
        result = subprocess.run(
            ["rcon-cli", "--host", "127.0.0.1", "--port", str(port), "--password", password, command],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return {"sent": False, "reason": str(exc)}
    if result.returncode != 0:
        return {"sent": False, "reason": (result.stdout or result.stderr or "RCON failed").strip()}
    return {"sent": True, "reason": (result.stdout or "").strip()}


def maybe_save_stats_before_scan(paths, options, cooldown=120):
    if not bool_option(options, "xray_suspicion_rcon_save_before_scan", True):
        return {"attempted": False, "reason": "disabled"}
    state = read_xray_state(paths)
    now = time.time()
    last_save = float(state.get("last_rcon_save_before_scan") or 0)
    if now - last_save < cooldown:
        return {"attempted": False, "reason": "cooldown"}
    result = rcon_security_alert(options, "save-all")
    state["last_rcon_save_before_scan"] = now
    state["last_rcon_save_before_scan_result"] = result
    write_xray_state(paths, state)
    return {"attempted": True, **result}


def record_xray_observation(paths, options, observation):
    if not bool_option(options, "xray_suspicion_enabled", True):
        return {"recorded": False, "reason": "xray suspicion disabled"}
    player = safe_player(observation.get("player"))
    block = normalized_block(observation.get("block"))
    rules = load_xray_rules(paths, options)
    if not player or block not in tracked_xray_blocks(rules):
        return {"recorded": False, "reason": "not a tracked ore observation"}
    visible = observation.get("visible")
    visible = visible if isinstance(visible, bool) else None
    timestamp = float(observation.get("timestamp_epoch") or time.time())
    window_seconds = int(rules.get("window_minutes") or 20) * 60
    threshold = int(rules.get("alert_threshold") or 120)
    cooldown = int(rules.get("alert_cooldown_seconds") or 300)
    state = read_xray_state(paths)
    players = state.setdefault("players", {})
    player_state = players.setdefault(player, {"observations": [], "last_alert": 0})
    observations = score_xray_observations(player_state.get("observations", []), rules, timestamp)["observations"]
    observations.append(
        {
            "timestamp": observation.get("timestamp") or iso_now(),
            "timestamp_epoch": timestamp,
            "block": block,
            "visible": visible,
            "weight": xray_weight(block, visible, rules),
            "location": observation.get("location"),
            "source": observation.get("source") or "api",
        }
    )
    scored = score_xray_observations(observations, rules, timestamp)
    player_state["observations"] = scored["observations"]
    player_state["score"] = scored["score"]
    player_state["last_seen"] = iso_now()
    player_state["last_block"] = block
    player_state["hidden_hits"] = scored["hidden_hits"]
    player_state["unknown_visibility_hits"] = scored["unknown_visibility_hits"]
    player_state["ore_hits"] = scored["ore_hits"]
    alert = None
    if scored["score"] >= threshold and timestamp - float(player_state.get("last_alert") or 0) >= cooldown:
        command = build_xray_alert_command(player, scored["score"], block)
        rcon = {"sent": False, "reason": "RCON alerts disabled"}
        if bool_option(options, "xray_suspicion_rcon_alerts", True):
            rcon = rcon_security_alert(options, command)
        alert = {"threshold": threshold, "command": command, "rcon": rcon}
        player_state["last_alert"] = timestamp
        append_event(
            paths,
            {
                "player": player,
                "check": "XraySuspicion",
                "category": "Mining",
                "violation_level": scored["score"],
                "verbose": f"Cheatverdacht: {scored['hidden_hits']} hidden ore hits, {scored['ore_hits']} tracked ore hits in {window_seconds // 60} minutes",
                "action": "xray_suspicion_alert",
                "source": observation.get("source") or "api",
                "raw": json.dumps({"block": block, "score": scored["score"], "alert": alert}, ensure_ascii=False),
            },
        )
    else:
        append_event(
            paths,
            {
                "player": player,
                "check": "XraySuspicion",
                "category": "Mining",
                "violation_level": scored["score"],
                "verbose": f"Tracked ore break: {block}; score {scored['score']}",
                "action": "xray_suspicion_observation",
                "source": observation.get("source") or "api",
                "raw": json.dumps({"block": block, "visible": visible, "score": scored["score"]}, ensure_ascii=False),
            },
        )
    write_xray_state(paths, state)
    return {
        "recorded": True,
        "player": player,
        "block": block,
        "score": scored["score"],
        "threshold": threshold,
        "hidden_hits": scored["hidden_hits"],
        "unknown_visibility_hits": scored["unknown_visibility_hits"],
        "ore_hits": scored["ore_hits"],
        "alert": alert,
    }


def xray_suspicion_status(options, paths):
    enabled = bool_option(options, "security_enabled", True) and bool_option(options, "xray_suspicion_enabled", True)
    rules = load_xray_rules(paths, options)
    threshold = int(rules.get("alert_threshold") or 120)
    tracked_blocks = tracked_xray_blocks(rules)
    state = read_xray_state(paths)
    active_world = active_world_name(options)
    stats_dir = paths["data"] / active_world / "stats"
    stats_dirs = xray_stats_dirs(paths, options)
    uuid_names = player_names_by_uuid(paths)
    stat_players = state.get("stat_players") if isinstance(state.get("stat_players"), dict) else {}
    baselines = []
    for key, snapshot in (state.get("stat_snapshots") or {}).items():
        if not isinstance(snapshot, dict):
            continue
        total = sum(int(snapshot.get(block) or 0) for block in tracked_blocks)
        if total <= 0:
            continue
        baselines.append(
            {
                "player": stat_players.get(key) or uuid_names.get(key) or uuid_names.get(Path(str(key)).stem) or uuid_names.get(Path(str(key)).stem.replace("-", "")) or Path(str(key)).stem.replace("-", "")[:12],
                "tracked_total": total,
                "diamond_ore": int(snapshot.get("minecraft:diamond_ore") or 0),
                "deepslate_diamond_ore": int(snapshot.get("minecraft:deepslate_diamond_ore") or 0),
                "ancient_debris": int(snapshot.get("minecraft:ancient_debris") or 0),
                "gold_ore": int(snapshot.get("minecraft:gold_ore") or 0) + int(snapshot.get("minecraft:deepslate_gold_ore") or 0) + int(snapshot.get("minecraft:nether_gold_ore") or 0),
            }
        )
    baselines.sort(key=lambda item: item["tracked_total"], reverse=True)
    players = []
    changed = False
    for name, payload in (state.get("players") or {}).items():
        if not isinstance(payload, dict):
            continue
        scored = score_xray_observations(payload.get("observations", []), rules)
        payload["observations"] = scored["observations"]
        payload["score"] = scored["score"]
        payload["hidden_hits"] = scored["hidden_hits"]
        payload["unknown_visibility_hits"] = scored["unknown_visibility_hits"]
        payload["ore_hits"] = scored["ore_hits"]
        changed = True
        if scored["ore_hits"] <= 0 and scored["score"] <= 0:
            continue
        players.append(
            {
                "player": name,
                "score": scored["score"],
                "hidden_hits": scored["hidden_hits"],
                "unknown_visibility_hits": scored["unknown_visibility_hits"],
                "ore_hits": scored["ore_hits"],
                "last_seen": payload.get("last_seen"),
                "last_block": payload.get("last_block"),
                "alerting": scored["score"] >= threshold,
            }
        )
    if changed:
        write_xray_state(paths, state)
    players.sort(key=lambda item: item["score"], reverse=True)
    return {
        "enabled": enabled,
        "threshold": threshold,
        "window_minutes": int(rules.get("window_minutes") or 20),
        "rcon_alerts": bool_option(options, "xray_suspicion_rcon_alerts", True),
        "alert_cooldown_seconds": int(rules.get("alert_cooldown_seconds") or 300),
        "rules": rules,
        "tracked_players": len(players),
        "top_players": players[:10],
        "active_world": active_world,
        "stats_dir": str(stats_dir),
        "stats_dirs": [str(item) for item in stats_dirs],
        "data_source": "minecraft_stats_api_or_bridge" if stats_dirs else "api_or_bridge",
        "stats_polling": bool(stats_dirs),
        "last_stats_scan": state.get("last_stats_scan"),
        "last_stats_files_scanned": int(state.get("last_stats_files_scanned") or 0),
        "last_stats_tracked_total": int(state.get("last_stats_tracked_total") or 0),
        "baseline_players": baselines[:10],
        "last_rcon_save_before_scan_result": state.get("last_rcon_save_before_scan_result"),
        "warning": None if players or stats_dirs else "No block-break observation source is active yet",
    }


def collect_xray_bridge_events(paths, options, max_new=200):
    text = read_latest_log(paths)
    if not text:
        return 0
    existing_hashes = {
        hashlib.sha256(str(event.get("raw") or "").encode("utf-8")).hexdigest()
        for event in get_security_events(paths, limit=1000)
        if event.get("raw")
    }
    added = 0
    for line in text.splitlines()[-3000:]:
        if "HAMinecraftBridge" not in line or "block_break" not in line:
            continue
        raw_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
        if raw_hash in existing_hashes:
            continue
        match = re.search(r"(\{.*\})", line)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "block_break":
            continue
        result = record_xray_observation(
            paths,
            options,
            {
                "player": payload.get("player"),
                "block": payload.get("block"),
                "visible": payload.get("visible", False),
                "location": payload.get("location"),
                "source": "bridge_log",
            },
        )
        if result.get("recorded"):
            existing_hashes.add(raw_hash)
            added += 1
        if added >= max_new:
            break
    return added


def player_names_by_uuid(paths):
    usercache = paths["data"] / "usercache.json"
    names = {}
    try:
        data = json.loads(usercache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return names
    if not isinstance(data, list):
        return names
    for entry in data:
        if not isinstance(entry, dict):
            continue
        uuid_value = str(entry.get("uuid") or "").lower()
        name = safe_player(entry.get("name"))
        if uuid_value and name:
            names[uuid_value] = name
            names[uuid_value.replace("-", "")] = name
    return names


def stat_file_player_name(path, uuid_names):
    key = path.stem.lower()
    compact = key.replace("-", "")
    return uuid_names.get(key) or uuid_names.get(compact) or compact[:12]


def xray_stats_dirs(paths, options):
    root = paths["data"]
    active_world = active_world_name(options)
    candidates = [
        root / active_world / "stats",
        root / active_world / "players" / "stats",
        root / "stats",
        root / "players" / "stats",
    ]
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


def stat_snapshot_key(paths, stat_file):
    try:
        return stat_file.relative_to(paths["data"]).as_posix().lower()
    except ValueError:
        return stat_file.name.lower()


def mined_stats_from_file(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    stats = data.get("stats") or {}
    mined = stats.get("minecraft:mined") or stats.get("minecraft:mine_block") or {}
    return mined if isinstance(mined, dict) else {}


def collect_xray_stats_events(paths, options, max_new=200):
    if not bool_option(options, "xray_suspicion_enabled", True):
        return 0
    stats_dirs = xray_stats_dirs(paths, options)
    if not stats_dirs:
        return 0
    maybe_save_stats_before_scan(paths, options)
    rules = load_xray_rules(paths, options)
    tracked_blocks = tracked_xray_blocks(rules)
    state = read_xray_state(paths)
    snapshots = state.setdefault("stat_snapshots", {})
    stat_players = state.setdefault("stat_players", {})
    uuid_names = player_names_by_uuid(paths)
    added = 0
    changed = False
    scanned_files = 0
    tracked_total = 0
    for stats_dir in stats_dirs:
        for stat_file in sorted(stats_dir.glob("*.json"), key=lambda item: item.name.lower()):
            scanned_files += 1
            current = mined_stats_from_file(stat_file)
            if not current:
                continue
            key = stat_snapshot_key(paths, stat_file)
            player = stat_file_player_name(stat_file, uuid_names)
            stat_players[key] = player
            previous = snapshots.get(key)
            tracked_current = {block: int(current.get(block) or 0) for block in tracked_blocks}
            tracked_total += sum(tracked_current.values())
            if not isinstance(previous, dict):
                snapshots[key] = tracked_current
                changed = True
                continue
            for block, count in tracked_current.items():
                delta = count - int(previous.get(block) or 0)
                if delta <= 0:
                    continue
                for _index in range(min(delta, 25)):
                    result = record_xray_observation(
                        paths,
                        options,
                        {
                            "player": player,
                            "block": block,
                            "visible": None,
                            "source": "minecraft_stats",
                        },
                    )
                    if result.get("recorded"):
                        added += 1
                    if added >= max_new:
                        break
                if added >= max_new:
                    break
            snapshots[key] = tracked_current
            changed = True
            if added >= max_new:
                break
        if added >= max_new:
            break
    latest_state = read_xray_state(paths)
    if changed:
        latest_state["stat_snapshots"] = snapshots
        latest_state["stat_players"] = stat_players
    latest_state["last_stats_scan"] = iso_now()
    latest_state["last_stats_files_scanned"] = scanned_files
    latest_state["last_stats_tracked_total"] = tracked_total
    write_xray_state(paths, latest_state)
    return added


def xray_runtime_summary(payload):
    if not isinstance(payload, dict):
        return None
    xray = payload.get("xray_suspicion") or {}
    if not isinstance(xray, dict) or not xray.get("enabled"):
        return None
    if not xray.get("stats_polling"):
        return (
            "Xray-Stats: keine Minecraft-Stats-Quelle gefunden "
            f"(Welt={xray.get('active_world') or 'unbekannt'}, Pfad={xray.get('stats_dir') or 'unbekannt'}); "
            "API/Bridge wartet auf Beobachtungen"
        )
    last_scan = xray.get("last_stats_scan") or "noch kein Scan"
    baseline_count = len(xray.get("baseline_players") or [])
    stats_dirs = xray.get("stats_dirs") or [xray.get("stats_dir")]
    stats_dir_text = ", ".join(str(item) for item in stats_dirs if item) or "unbekannt"
    return (
        "Xray-Stats: aktiv "
        f"(Welt={xray.get('active_world') or 'unbekannt'}, Scan={last_scan}, "
        f"Dateien={int(xray.get('last_stats_files_scanned') or 0)}, "
        f"Erzzaehler={int(xray.get('last_stats_tracked_total') or 0)}, "
        f"Verdachtsspieler={int(xray.get('tracked_players') or 0)}, "
        f"Baseline-Spieler={baseline_count}, Pfade={stats_dir_text})"
    )


def log_runtime_summary_if_changed(paths, payload):
    summary = xray_runtime_summary(payload)
    if not summary:
        return
    state = read_xray_state(paths)
    if state.get("last_runtime_summary") == summary:
        return
    state["last_runtime_summary"] = summary
    write_xray_state(paths, state)
    log(summary)


def import_current_xray_stats(paths, options, max_new=500):
    stats_dirs = xray_stats_dirs(paths, options)
    if not stats_dirs:
        return {"imported": 0, "reason": "stats directory not found"}
    maybe_save_stats_before_scan(paths, options, cooldown=0)
    rules = load_xray_rules(paths, options)
    tracked_blocks = tracked_xray_blocks(rules)
    uuid_names = player_names_by_uuid(paths)
    state = read_xray_state(paths)
    snapshots = state.setdefault("stat_snapshots", {})
    stat_players = state.setdefault("stat_players", {})
    imported = 0
    scanned_files = 0
    tracked_total = 0
    for stats_dir in stats_dirs:
        for stat_file in sorted(stats_dir.glob("*.json"), key=lambda item: item.name.lower()):
            scanned_files += 1
            current = mined_stats_from_file(stat_file)
            if not current:
                continue
            player = stat_file_player_name(stat_file, uuid_names)
            key = stat_snapshot_key(paths, stat_file)
            stat_players[key] = player
            tracked_current = {block: int(current.get(block) or 0) for block in tracked_blocks}
            tracked_total += sum(tracked_current.values())
            for block, count in tracked_current.items():
                if count <= 0:
                    continue
                for _index in range(min(count, 25)):
                    result = record_xray_observation(
                        paths,
                        options,
                        {
                            "player": player,
                            "block": block,
                            "visible": None,
                            "source": "minecraft_stats_import",
                        },
                    )
                    if result.get("recorded"):
                        imported += 1
                    if imported >= max_new:
                        break
                if imported >= max_new:
                    break
            snapshots[key] = tracked_current
            if imported >= max_new:
                break
        if imported >= max_new:
            break
    latest_state = read_xray_state(paths)
    latest_state["stat_snapshots"] = snapshots
    latest_state["stat_players"] = stat_players
    latest_state["last_stats_scan"] = iso_now()
    latest_state["last_stats_import"] = iso_now()
    latest_state["last_stats_files_scanned"] = scanned_files
    latest_state["last_stats_tracked_total"] = tracked_total
    write_xray_state(paths, latest_state)
    return {"imported": imported, "reason": None, "status": xray_suspicion_status(options, paths)}


def reset_xray_suspicion(paths):
    try:
        paths["xray_state"].unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError(f"Could not reset Xray suspicion state: {exc}") from exc
    return {"reset": True, "message": "Xray suspicion score reset. The next stats scan will create a fresh baseline."}


def reset_xray_player(paths, player):
    player = safe_player(player)
    if not player:
        raise RuntimeError("Invalid player name")
    state = read_xray_state(paths)
    players = state.get("players")
    if isinstance(players, dict):
        players.pop(player, None)
    write_xray_state(paths, state)
    return {"reset": True, "player": player}


def reset_player_max_score(paths, player):
    player = safe_player(player)
    if not player:
        raise RuntimeError("Invalid player name")
    state = read_xray_state(paths)
    resets = state.setdefault("max_score_resets", {})
    resets[player] = time.time()
    write_xray_state(paths, state)
    return {"reset": True, "player": player, "reset_epoch": resets[player]}


def get_security_events(paths, limit=100):
    if not paths["events"].is_file():
        return []
    rows = []
    with paths["events"].open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows[-max(1, int(limit)) :][::-1]


def grim_startup_noise(line):
    text = str(line or "")
    return bool(
        re.search(
            r"(Deprecated Grim command-execute|Failed to load PacketEventsInit|Failed to start PacketManager|Failed to register commands|"
            r"SLF4J\(W\)|Loading PacketEvents|Loaded packetevents|Registering packets|Registering tick schedulers|Grim Version:|"
            r"You are using the latest version of GrimAC|New GrimAC version found|Your server software is preventing us)",
            text,
            re.IGNORECASE,
        )
    )


def security_event_noise(event):
    if not isinstance(event, dict):
        return True
    if event.get("source") != "logparser" or event.get("action") != "detected":
        return False
    raw = str(event.get("raw") or event.get("verbose") or "")
    if grim_startup_noise(raw):
        return True
    if event.get("player") == "to" and not event.get("check"):
        return True
    return not (event.get("player") and event.get("check") and event.get("violation_level") is not None)


def parse_grim_violation_line(line):
    if "[GrimAC]" not in line and "[alert]" not in line.lower():
        return None
    if grim_startup_noise(line):
        return None
    patterns = [
        r"\[GrimAC\].*?\b(?P<player>[A-Za-z0-9_.]{1,32})\s+failed\s+(?P<check>[A-Za-z][A-Za-z0-9_.-]{1,48}).*?\bVL[:= ]+(?P<vl>\d+)",
        r"\[GrimAC\].*?\b(?P<player>[A-Za-z0-9_.]{1,32}).*?\b(?P<check>[A-Za-z][A-Za-z0-9_.-]{1,48}).*?\bVL[:= ]+(?P<vl>\d+)",
        r"\[alert\].*?\b(?P<player>[A-Za-z0-9_.]{1,32}).*?\b(?P<check>[A-Za-z][A-Za-z0-9_.-]{1,48}).*?\bVL[:= ]+(?P<vl>\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, line, re.IGNORECASE)
        if not match:
            continue
        player = safe_player(match.group("player"))
        check = str(match.group("check") or "").strip()
        if not player or player.lower() in {"failed", "defaulting", "server", "grimac"}:
            continue
        return {"player": player, "check": check, "violation_level": int(match.group("vl"))}
    return None


def collect_grim_log_events(paths, max_new=100):
    log_path = paths["data"] / "logs" / "latest.log"
    if not log_path.is_file():
        return 0
    existing_hashes = {
        hashlib.sha256(str(event.get("raw") or "").encode("utf-8")).hexdigest()
        for event in get_security_events(paths, limit=1000)
        if event.get("raw")
    }
    try:
        with log_path.open("rb") as handle:
            if log_path.stat().st_size > 500_000:
                handle.seek(-500_000, os.SEEK_END)
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return 0
    added = 0
    for line in text.splitlines()[-2000:]:
        parsed = parse_grim_violation_line(line)
        if not parsed:
            continue
        raw_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
        if raw_hash in existing_hashes:
            continue
        append_event(
            paths,
            {
                "player": parsed["player"],
                "check": parsed["check"],
                "violation_level": parsed["violation_level"],
                "verbose": line[-300:],
                "action": "detected",
                "source": "logparser",
                "raw": line,
            },
        )
        existing_hashes.add(raw_hash)
        added += 1
        if added >= max_new:
            break
    return added


def trim_events(paths, max_entries):
    events = [event for event in get_security_events(paths, limit=max_entries * 2) if not security_event_noise(event)]
    events = events[:max_entries]
    paths["events"].parent.mkdir(parents=True, exist_ok=True)
    with paths["events"].open("w", encoding="utf-8") as handle:
        for event in reversed(events):
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def events_status(options, paths):
    enabled = bool_option(options, "security_enabled", True) and bool_option(options, "grim_event_log_enabled", True)
    max_entries = int_option(options, "grim_event_log_max_entries", DEFAULT_EVENT_LIMIT, 50, 5000)
    if enabled:
        collect_grim_log_events(paths)
        collect_xray_bridge_events(paths, options)
        collect_xray_stats_events(paths, options)
        trim_events(paths, max_entries)
    events = get_security_events(paths, limit=max_entries)
    return {
        "enabled": enabled,
        "source": "logparser",
        "stored_entries": len(events),
        "max_entries": max_entries,
        "last_event": events[0]["timestamp"] if events else None,
        "error": None,
    }


def punishments_status(options, paths):
    managed = bool_option(options, "security_enabled", True) and bool_option(options, "grim_punishments_managed", True)
    if not paths["punishments"].is_file():
        return {
            "managed": managed,
            "initialized": False,
            "file_exists": False,
            "valid": None,
            "categories": 0,
            "commands": 0,
            "public_alerts_detected": False,
            "kick_actions_detected": False,
            "ban_actions_detected": False,
            "last_modified": None,
            "last_backup": newest_backup_iso(paths["backups"], "punishments-*.yml"),
            "reload_supported": False,
            "restart_required": False,
            "warnings": ["Grim has not created punishments.yml yet"],
            "error": None,
        }
    try:
        document = load_punishments(paths)
        commands = sum(len(category["commands"]) for category in document["categories"])
        text = document["raw"]
        return {
            "managed": managed,
            "initialized": True,
            "file_exists": paths["punishments"].is_file(),
            "valid": document["valid"],
            "categories": len(document["categories"]),
            "commands": commands,
            "public_alerts_detected": "tellraw @a" in text,
            "kick_actions_detected": bool(re.search(r"\bkick\s+%player%", text)),
            "ban_actions_detected": bool(re.search(r"\bban\s+%player%", text)),
            "last_modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(paths["punishments"].stat().st_mtime)) if paths["punishments"].is_file() else None,
            "last_backup": newest_backup_iso(paths["backups"], "punishments-*.yml"),
            "reload_supported": False,
            "restart_required": False,
            "warnings": document["warnings"],
            "error": "; ".join(document["errors"]) if document["errors"] else None,
        }
    except Exception as exc:
        return {"managed": managed, "initialized": paths["punishments"].is_file(), "file_exists": paths["punishments"].is_file(), "valid": False, "categories": 0, "commands": 0, "warnings": [], "error": str(exc)}


def newest_backup_iso(directory, pattern):
    try:
        newest = max(directory.glob(pattern), key=lambda item: item.stat().st_mtime)
    except ValueError:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(newest.stat().st_mtime))


def bedrock_compatibility(options, paths):
    bedrock_enabled = bool_option(options, "bedrock_enabled", False)
    plugins = paths["plugins"]
    geyser = any(path.name.lower().startswith("geyser") for path in plugins.glob("*.jar")) if plugins.is_dir() else False
    floodgate = any(path.name.lower().startswith("floodgate") for path in plugins.glob("*.jar")) if plugins.is_dir() else False
    warning = None
    if bedrock_enabled and bool_option(options, "grim_enabled", True) and bool_option(options, "security_enabled", True):
        warning = "Bedrock players are exempt from Grim checks."
    return {
        "bedrock_enabled": bedrock_enabled,
        "geyser_detected": geyser,
        "floodgate_detected": floodgate,
        "bedrock_players_checked_by_grim": False,
        "warning": warning,
    }


def viaversion_config_path(paths):
    for relative in (
        ("plugins", "ViaVersion", "config.yml"),
        ("plugins", "ViaVersion", "config.yaml"),
    ):
        path = paths["data"].joinpath(*relative)
        if path.is_file():
            return path
    return paths["data"] / "plugins" / "ViaVersion" / "config.yml"


def via_version_status(paths):
    config = viaversion_config_path(paths)
    log_text = read_latest_log(paths)
    detected = config.exists() or "ViaVersion" in log_text
    placement_value = None
    error = None
    if config.is_file():
        try:
            data = safe_load_yaml(config)
            placement_value = data.get("fix-1_21-placement-rotation")
        except Exception as exc:
            error = str(exc)
    log_conflict = "fix-1_21-placement-rotation" in log_text and "GrimAC has detected" in log_text
    enabled = bool(placement_value is True or log_conflict)
    return {
        "detected": detected,
        "config_file": str(config.relative_to(paths["data"])) if config.exists() else None,
        "placement_rotation_fix_enabled": enabled,
        "compatible_with_grim": not enabled,
        "restart_required": False,
        "warning": "ViaVersion fix-1_21-placement-rotation must be disabled for Grim." if enabled else None,
        "error": error,
    }


def fix_viaversion_placement_rotation(paths):
    config = viaversion_config_path(paths)
    if not config.is_file():
        raise RuntimeError("ViaVersion config.yml was not found")
    data = safe_load_yaml(config)
    if data.get("fix-1_21-placement-rotation") is False:
        return via_version_status(paths)
    backup_file(config, paths["backups"])
    data["fix-1_21-placement-rotation"] = False
    text = dump_yaml(data)
    yaml.safe_load(text)
    atomic_write_text(config, text)
    status = via_version_status(paths)
    status["placement_rotation_fix_enabled"] = False
    status["compatible_with_grim"] = True
    status["restart_required"] = True
    status["warning"] = None
    return status


def prepare_security(options, paths):
    paths["state"].mkdir(parents=True, exist_ok=True)
    if not bool_option(options, "security_enabled", True):
        grim = prepare_grim(options, paths)
        payload = {
            "enabled": False,
            "healthy": True,
            "restart_required": grim.get("restart_required", False),
            "warnings": ["Security disabled"],
            "anti_xray": {"requested": False, "configured": False, "engine_mode": int_option(options, "anti_xray_engine_mode", 2), "config_files": [], "warnings": [], "error": None},
            "grim": grim,
            "punishments": punishments_status(options, paths),
            "events": events_status(options, paths),
            "xray_suspicion": xray_suspicion_status(options, paths),
            "via_version": via_version_status(paths),
            "bedrock_compatibility": bedrock_compatibility(options, paths),
        }
        write_state(paths, {"security": payload, "grim": {"version": grim.get("version"), "installed_version": grim.get("installed_version")}})
        return payload
    anti = configure_anti_xray(options, paths)
    grim = prepare_grim(options, paths)
    punishments = punishments_status(options, paths)
    events = events_status(options, paths)
    via = via_version_status(paths)
    warnings = anti.get("warnings", []) + grim.get("warnings", []) + punishments.get("warnings", [])
    grim_required = bool(grim.get("requested"))
    if grim_required and grim.get("healthy") is None:
        warnings.append("Grim runtime status is not confirmed yet")
    if via.get("warning"):
        warnings.append(via["warning"])
    grim_runtime_ok = (not grim_required) or grim.get("healthy") is True
    payload = {
        "enabled": True,
        "healthy": grim_runtime_ok and not (anti.get("error") or grim.get("error") or punishments.get("error") or via.get("error") or via.get("warning")),
        "restart_required": bool(anti.get("restart_required") or grim.get("restart_required") or punishments.get("restart_required")),
        "warnings": warnings,
        "anti_xray": anti,
        "grim": grim,
        "punishments": punishments,
        "events": events,
        "xray_suspicion": xray_suspicion_status(options, paths),
        "via_version": via,
        "bedrock_compatibility": bedrock_compatibility(options, paths),
    }
    write_state(paths, {"security": payload, "grim": {"version": grim.get("version"), "installed_version": grim.get("installed_version")}})
    return payload


def get_security_status(options, paths):
    stored = read_state(paths).get("security")
    if isinstance(stored, dict):
        if isinstance(stored.get("grim"), dict):
            runtime = parse_grim_runtime_status(paths)
            for key in (
                "plugin_discovered",
                "loaded",
                "enabled",
                "packet_manager_active",
                "commands_registered",
                "healthy",
                "runtime_compatible",
                "runtime_detection",
                "detected_server_version_by_grim",
                "runtime_error",
                "grim_reported_update",
                "runtime_version",
            ):
                stored["grim"][key] = runtime.get(key)
            if runtime.get("runtime_error"):
                stored["grim"]["error"] = runtime["runtime_error"]
            if runtime.get("runtime_version") and not stored["grim"].get("installed_version"):
                stored["grim"]["installed_version"] = runtime["runtime_version"]
                stored["grim"]["version"] = runtime["runtime_version"]
            if runtime.get("runtime_warnings"):
                stored["grim"]["warnings"] = list(dict.fromkeys((stored["grim"].get("warnings") or []) + runtime["runtime_warnings"]))
            log_update = runtime.get("grim_reported_update")
            current_available = stored["grim"].get("latest_available_version")
            if log_update and (not current_available or version_newer(log_update["version"], current_available)):
                stored["grim"]["latest_available_version"] = log_update["version"]
                stored["grim"]["latest_available_channel"] = log_update["channel"]
                installed_version = stored["grim"].get("installed_version") or stored["grim"].get("version")
                stored["grim"]["update_available"] = bool(installed_version and version_newer(log_update["version"], installed_version))
        stored["punishments"] = punishments_status(options, paths)
        stored["events"] = events_status(options, paths)
        stored["xray_suspicion"] = xray_suspicion_status(options, paths)
        stored["via_version"] = via_version_status(paths)
        stored["bedrock_compatibility"] = bedrock_compatibility(options, paths)
        warnings = []
        for section in ("anti_xray", "grim", "punishments"):
            value = stored.get(section)
            if isinstance(value, dict):
                warnings.extend(value.get("warnings") or [])
        if stored["via_version"].get("warning"):
            warnings.append(stored["via_version"]["warning"])
        stored["warnings"] = list(dict.fromkeys(warnings))
        grim_required = isinstance(stored.get("grim"), dict) and stored["grim"].get("requested")
        if grim_required and stored["grim"].get("healthy") is None:
            stored["warnings"].append("Grim runtime status is not confirmed yet")
        grim_runtime_ok = (not grim_required) or stored["grim"].get("healthy") is True
        stored["healthy"] = not (
            not grim_runtime_ok
            or
            (isinstance(stored.get("grim"), dict) and stored["grim"].get("healthy") is False)
            or stored.get("via_version", {}).get("warning")
            or stored.get("punishments", {}).get("error")
            or stored.get("anti_xray", {}).get("error")
        )
        return stored
    return prepare_security(options, paths)


def read_options_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "status", "punishments", "save-punishments", "events", "clear-events"])
    parser.add_argument("--data-dir", default=os.environ.get("MC_DATA_DIR", "/media/minecraft-java"))
    parser.add_argument("--options-file", default=os.environ.get("OPTIONS_FILE", "/tmp/ha-minecraft-options.json"))
    parser.add_argument("--status-file", default=os.environ.get("MINECRAFT_SECURITY_STATUS_FILE", "/tmp/ha-minecraft-security-status.json"))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--runtime-summary", action="store_true")
    args = parser.parse_args()
    options = read_options_json(args.options_file)
    paths = data_paths(args.data_dir)
    try:
        if args.action == "prepare":
            payload = prepare_security(options, paths)
        elif args.action == "status":
            payload = get_security_status(options, paths)
        elif args.action == "punishments":
            payload = load_punishments(paths)
        elif args.action == "save-punishments":
            raw = sys.stdin.read()
            payload = save_punishments(paths, raw, args.expected_sha or None, args.force)
        elif args.action == "events":
            payload = {"events": get_security_events(paths, args.limit)}
        else:
            if not args.force:
                raise RuntimeError("clear-events requires --force")
            atomic_write_text(paths["events"], "")
            payload = {"events": []}
        atomic_write_json(Path(args.status_file), payload)
        if args.runtime_summary:
            log_runtime_summary_if_changed(paths, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        payload = {"enabled": bool(options.get("security_enabled", True)), "state": "error", "error": str(exc)}
        atomic_write_json(Path(args.status_file), payload)
        log(f"ERROR: {exc}")
        return 1 if args.action in {"save-punishments"} else 0


if __name__ == "__main__":
    sys.exit(main())
