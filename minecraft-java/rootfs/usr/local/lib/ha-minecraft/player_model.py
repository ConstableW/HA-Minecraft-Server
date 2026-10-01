#!/usr/bin/env python3
import json
import os
import re
import subprocess
from pathlib import Path


SAFE_SERVER_NAME = re.compile(r"^[A-Za-z0-9_.]{1,32}$")


def normalize_name(value):
    name = str(value or "").strip()
    if name.startswith("."):
        name = name[1:]
    name = name.replace(" ", "_")
    return name.lower()


def safe_server_name(name):
    name = str(name or "").strip()
    return name if SAFE_SERVER_NAME.match(name) else ""


def parse_rcon_list(output):
    if ":" not in str(output):
        return []
    names = str(output).split(":", 1)[1].strip()
    if not names:
        return []
    return [name for name in (safe_server_name(part.strip()) for part in names.split(",")) if name]


def rcon_list(config, timeout=5):
    if not config.get("enable_rcon") or not config.get("rcon_password"):
        return []
    port = int(config.get("rcon_port") or 25575)
    result = subprocess.run(
        ["rcon-cli", "--host", "127.0.0.1", "--port", str(port), "--password", str(config.get("rcon_password")), "list"],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return []
    return parse_rcon_list(result.stdout or result.stderr)


def parse_sample(sample):
    if isinstance(sample, str):
        return [safe_server_name(part.strip()) for part in sample.split(",") if safe_server_name(part.strip())]
    if isinstance(sample, list):
        names = []
        for item in sample:
            if isinstance(item, dict):
                name = safe_server_name(item.get("name", ""))
            else:
                name = safe_server_name(item)
            if name:
                names.append(name)
        return names
    return []


def read_latest_log(data_dir, max_bytes=500_000):
    path = Path(data_dir) / "logs" / "latest.log"
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        if path.stat().st_size > max_bytes:
            handle.seek(-max_bytes, os.SEEK_END)
        return handle.read().decode("utf-8", errors="replace")


def bedrock_mappings_from_log(text):
    mappings = {}
    pending_display_by_norm = {}
    floodgate_prefix = "."
    patterns = [
        re.compile(r"\[Geyser-Spigot\] Player connected with username (?P<display>.+?)(?: \(\d+\))?$"),
        re.compile(r"\[Geyser-Spigot\] (?P<display>.+?) \(logged in as: (?P<server>[A-Za-z0-9_.]+)\) has connected to the Java server"),
        re.compile(r"\[floodgate\] Floodgate player logged in as (?P<server>[A-Za-z0-9_.]+) joined"),
    ]
    for raw_line in text.splitlines():
        line = raw_line.strip()
        first = patterns[0].search(line)
        if first:
            display = first.group("display").strip()
            pending_display_by_norm[normalize_name(display)] = display
            continue
        geyser = patterns[1].search(line)
        if geyser:
            display = geyser.group("display").strip()
            server = geyser.group("server").strip()
            mappings[normalize_name(server)] = {
                "display_name": display,
                "server_name": server,
                "geyser_name": server.lstrip(floodgate_prefix),
                "platform": "bedrock",
            }
            continue
        floodgate = patterns[2].search(line)
        if floodgate:
            server = floodgate.group("server").strip()
            norm = normalize_name(server)
            display = pending_display_by_norm.get(norm) or pending_display_by_norm.get(normalize_name(server.lstrip(floodgate_prefix))) or server.lstrip(floodgate_prefix).replace("_", " ")
            mappings[norm] = {
                "display_name": display,
                "server_name": server,
                "geyser_name": server.lstrip(floodgate_prefix),
                "platform": "bedrock",
            }
    return mappings


def build_player_model(server_names, bedrock_mappings):
    players = []
    seen = set()
    for server_name in server_names:
        safe = safe_server_name(server_name)
        if not safe:
            continue
        norm = normalize_name(safe)
        if norm in seen:
            continue
        seen.add(norm)
        mapped = bedrock_mappings.get(norm) or bedrock_mappings.get(normalize_name(safe.lstrip(".")))
        if mapped:
            players.append(
                {
                    "id": norm,
                    "name": mapped.get("display_name") or safe,
                    "display_name": mapped.get("display_name") or safe,
                    "server_name": safe,
                    "geyser_name": mapped.get("geyser_name", ""),
                    "platform": "bedrock",
                    "connected": True,
                }
            )
        else:
            players.append(
                {
                    "id": norm,
                    "name": safe,
                    "display_name": safe,
                    "server_name": safe,
                    "platform": "java",
                    "connected": True,
                }
            )
    java_count = sum(1 for player in players if player["platform"] == "java")
    bedrock_count = sum(1 for player in players if player["platform"] == "bedrock")
    return {
        "players": players,
        "players_online": java_count + bedrock_count,
        "players_java": java_count,
        "players_bedrock": bedrock_count,
        "player_sample": ", ".join(player["display_name"] for player in players),
    }


def load_players(data_dir, config, status_players=None):
    names = rcon_list(config)
    if not names:
        names = parse_sample(status_players or "")
    mappings = bedrock_mappings_from_log(read_latest_log(data_dir))
    return build_player_model(names, mappings)


def main():
    payload = build_player_model(
        [".Constable_W1949", "ConstableWatson", "FieryMonkey4174"],
        bedrock_mappings_from_log(
            "[Geyser-Spigot] Constable W1949 (logged in as: .Constable_W1949) has connected to the Java server\n"
        ),
    )
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
