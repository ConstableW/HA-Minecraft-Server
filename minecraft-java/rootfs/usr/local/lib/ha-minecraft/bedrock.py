#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the add-on image installs python3-yaml
    yaml = None


GEYSER_URL = "https://download.geysermc.org/v2/projects/geyser/versions/latest/builds/latest/downloads/spigot"
FLOODGATE_URL = "https://download.geysermc.org/v2/projects/floodgate/versions/latest/builds/latest/downloads/spigot"
GEYSER_JAR = "Geyser-Spigot.jar"
FLOODGATE_JAR = "floodgate-spigot.jar"
GEYSER_PATTERNS = (r"^Geyser(?:-Spigot)?(?:[-_.].*)?\.jar$",)
FLOODGATE_PATTERNS = (r"^floodgate(?:-spigot)?(?:[-_.].*)?\.jar$",)


def log(message):
    print(f"[bedrock] {message}", flush=True)


def valid_udp_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        return False
    return 1 <= port <= 65535


def plugin_matches(path, patterns):
    return any(re.match(pattern, path.name, re.IGNORECASE) for pattern in patterns)


def find_plugin(directory, patterns):
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.jar"), key=lambda item: item.name.lower()):
        if plugin_matches(path, patterns):
            return path
    return None


def move_matching(source, target, patterns):
    moved = []
    if not source.is_dir():
        return moved
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob("*.jar"), key=lambda item: item.name.lower()):
        if plugin_matches(path, patterns):
            destination = target / path.name
            if destination.exists():
                destination.unlink()
            shutil.move(str(path), str(destination))
            moved.append(destination)
    return moved


def download_if_missing(target, url, label):
    if target.is_file() and target.stat().st_size > 0:
        log(f"{label} already installed as {target.name}")
        return target
    log(f"Downloading {label} from {url}")
    tmp = target.with_suffix(target.suffix + ".download")
    try:
        subprocess.run(["curl", "-fL", "--retry", "3", "--connect-timeout", "20", "-o", str(tmp), url], check=True)
        tmp.replace(target)
    except Exception as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise RuntimeError(f"Unable to download {label}: {exc}") from exc
    return target


def backup_once(path):
    backup = path.with_name(path.name + ".bak")
    if path.is_file() and not backup.exists():
        shutil.copy2(path, backup)


def load_yaml_map(path):
    if yaml is None:
        raise RuntimeError("PyYAML is required to configure Geyser")
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must contain a YAML mapping")
    return data


def write_yaml_map(path, data):
    if yaml is None:
        raise RuntimeError("PyYAML is required to configure Geyser")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, default_flow_style=False, allow_unicode=True, sort_keys=False)


def configure_geyser_config(path, port):
    backup_once(path)
    config = load_yaml_map(path)
    changed = False

    bedrock = config.setdefault("bedrock", {})
    if not isinstance(bedrock, dict):
        bedrock = {}
        config["bedrock"] = bedrock
        changed = True
    if bedrock.get("address") != "0.0.0.0":
        bedrock["address"] = "0.0.0.0"
        changed = True
    if bedrock.get("port") != int(port):
        bedrock["port"] = int(port)
        changed = True

    if isinstance(config.get("java"), dict):
        auth_section = config["java"]
    elif isinstance(config.get("remote"), dict):
        auth_section = config["remote"]
    else:
        auth_section = config.setdefault("java", {})
        changed = True
    if auth_section.get("auth-type") != "floodgate":
        auth_section["auth-type"] = "floodgate"
        changed = True

    if changed or not path.exists():
        write_yaml_map(path, config)
    return changed


def disable(data_dir):
    plugins = data_dir / "plugins"
    disabled = data_dir / "plugins-disabled"
    moved = move_matching(plugins, disabled, GEYSER_PATTERNS + FLOODGATE_PATTERNS)
    log("Bedrock support disabled")
    if moved:
        log(f"Moved {len(moved)} Bedrock plugin jar(s) to {disabled}")
    return {"enabled": False, "state": "disabled", "message": "Bedrock support disabled"}


def prepare(data_dir, port):
    if not valid_udp_port(port):
        raise RuntimeError("UDP port configuration is invalid")
    plugins = data_dir / "plugins"
    disabled = data_dir / "plugins-disabled"
    plugins.mkdir(parents=True, exist_ok=True)
    disabled.mkdir(parents=True, exist_ok=True)

    log("Bedrock support enabled")
    log("Checking Geyser-Spigot")
    move_matching(disabled, plugins, GEYSER_PATTERNS)
    geyser = find_plugin(plugins, GEYSER_PATTERNS) or download_if_missing(plugins / GEYSER_JAR, GEYSER_URL, "Geyser-Spigot")

    log("Checking Floodgate")
    move_matching(disabled, plugins, FLOODGATE_PATTERNS)
    floodgate = find_plugin(plugins, FLOODGATE_PATTERNS) or download_if_missing(plugins / FLOODGATE_JAR, FLOODGATE_URL, "Floodgate")

    log("Configuring Geyser")
    geyser_config = plugins / "Geyser-Spigot" / "config.yml"
    configure_geyser_config(geyser_config, int(port))
    log("Authentication: floodgate")
    log("UDP bind address: 0.0.0.0")
    log(f"UDP port: {int(port)}")
    return {
        "enabled": True,
        "state": "prepared",
        "geyser": "installed",
        "floodgate": "installed",
        "geyser_jar": geyser.name,
        "floodgate_jar": floodgate.name,
        "udp_port": int(port),
        "auth": "floodgate",
        "message": "Bedrock support prepared",
    }


def read_latest_log(data_dir, max_bytes=250_000):
    path = data_dir / "logs" / "latest.log"
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        if path.stat().st_size > max_bytes:
            handle.seek(-max_bytes, os.SEEK_END)
        return handle.read().decode("utf-8", errors="replace")


def check(data_dir, port):
    latest = read_latest_log(data_dir)
    geyser_loaded = bool(re.search(r"\[Geyser-Spigot\].*(Started Geyser|Done|Enabling)", latest, re.IGNORECASE))
    floodgate_loaded = bool(re.search(r"\[floodgate\].*(Enabling floodgate|enabled)", latest, re.IGNORECASE))
    udp_started = bool(re.search(rf"\[Geyser-Spigot\].*UDP port {int(port)}", latest, re.IGNORECASE))
    errors = []
    for line in latest.splitlines()[-300:]:
        if ("[Geyser-Spigot]" in line or "[floodgate]" in line) and re.search(r"\b(ERROR|SEVERE|Exception)\b", line, re.IGNORECASE):
            errors.append(line[-240:])
    state = "active" if geyser_loaded and floodgate_loaded and (udp_started or geyser_loaded) and not errors else "error"
    return {
        "enabled": True,
        "state": state,
        "geyser": "active" if geyser_loaded else "missing",
        "floodgate": "active" if floodgate_loaded else "missing",
        "udp_port": int(port),
        "auth": "floodgate",
        "message": errors[0] if errors else ("Bedrock support active" if state == "active" else "Geyser or Floodgate not confirmed in logs"),
        "errors": errors[:5],
    }


def write_status(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "disable", "check"])
    parser.add_argument("--data-dir", default=os.environ.get("MC_DATA_DIR", "/media/minecraft-java"))
    parser.add_argument("--port", default=os.environ.get("BEDROCK_PORT", "19132"))
    parser.add_argument("--status-file", default=os.environ.get("BEDROCK_STATUS_FILE", "/tmp/ha-minecraft-bedrock-status.json"))
    parser.add_argument("--delay", type=int, default=0)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    status_file = Path(args.status_file)
    if args.delay:
        time.sleep(args.delay)
    try:
        if args.action == "prepare":
            payload = prepare(data_dir, args.port)
        elif args.action == "disable":
            payload = disable(data_dir)
        else:
            payload = check(data_dir, args.port)
        write_status(status_file, payload)
    except Exception as exc:
        payload = {"enabled": args.action != "disable", "state": "error", "message": str(exc)}
        write_status(status_file, payload)
        log(f"ERROR: {exc}")
        if args.action == "prepare":
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
