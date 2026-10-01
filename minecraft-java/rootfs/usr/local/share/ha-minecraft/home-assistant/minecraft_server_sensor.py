#!/usr/bin/env python3
import json
import os
import socket
import struct
import sys
import time
from pathlib import Path


DATA_DIR = Path(os.environ.get("MINECRAFT_DATA_DIR", "/media/minecraft-java"))
LIB_DIR = Path(os.environ.get("HA_MINECRAFT_LIB_DIR", "/usr/local/lib/ha-minecraft"))
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))
try:
    import player_model
except ImportError:  # pragma: no cover - useful when this helper is copied to HA /config without the add-on libs
    player_model = None
BEDROCK_STATUS_FILE = Path(os.environ.get("MINECRAFT_BEDROCK_STATUS_FILE", "/tmp/ha-minecraft-bedrock-status.json"))
CONFIG_PATHS = [
    Path(os.environ["MINECRAFT_ADDON_CONFIG"]),
] if os.environ.get("MINECRAFT_ADDON_CONFIG") else [
    DATA_DIR / "addon-options.yaml",
    Path("/addon_configs/local_minecraft_java/options.yaml"),
    Path("/addon_configs/minecraft_java/options.yaml"),
]


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
    if not path.is_file():
        return data
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


def first_config():
    for path in CONFIG_PATHS:
        if path.is_file():
            return path, read_simple_yaml(path)
    return None, {}


def resolve_active_world(config):
    slot = str(config.get("world_slot") or "CUSTOM")
    if slot == "WORLD_1":
        return config.get("world_1_name") or config.get("level_name") or "world"
    if slot == "WORLD_2":
        return config.get("world_2_name") or config.get("level_name") or "world"
    if slot == "WORLD_3":
        return config.get("world_3_name") or config.get("level_name") or "world"
    return config.get("level_name") or "world"


def list_worlds():
    worlds = []
    if not DATA_DIR.is_dir():
        return worlds
    ignored = {
        "backups",
        "cache",
        "config",
        "crash-reports",
        "libraries",
        "logs",
        "migration-conflicts",
        "mods",
        "plugins",
        "versions",
    }
    for level_dat in DATA_DIR.glob("**/level.dat"):
        try:
            rel = level_dat.parent.relative_to(DATA_DIR)
        except ValueError:
            continue
        first = rel.parts[0] if rel.parts else ""
        if first in ignored:
            continue
        worlds.append(str(rel))
    return sorted(set(worlds), key=str.lower)


def directory_count(path, pattern="*"):
    if not path.is_dir():
        return 0
    return sum(1 for item in path.glob(pattern) if item.is_file())


def backup_stats():
    backup_dir = DATA_DIR / "backups"
    backups = sorted(backup_dir.glob("minecraft-*.tar.gz"), reverse=True)
    latest = backups[0] if backups else None
    total_bytes = sum(path.stat().st_size for path in backups if path.is_file())
    return {
        "backup_count": len(backups),
        "latest_backup": latest.name if latest else "",
        "latest_backup_path": str(latest) if latest else "",
        "latest_backup_mtime": int(latest.stat().st_mtime) if latest else 0,
        "backup_total_mb": round(total_bytes / 1024 / 1024, 1),
    }


def pack_varint(value):
    out = bytearray()
    value &= 0xFFFFFFFF
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def read_varint(sock):
    value = 0
    for position in range(5):
        raw = sock.recv(1)
        if not raw:
            raise OSError("connection closed")
        byte = raw[0]
        value |= (byte & 0x7F) << (7 * position)
        if not byte & 0x80:
            return value
    raise OSError("varint too long")


def read_exact(sock, length):
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise OSError("connection closed")
        data.extend(chunk)
    return bytes(data)


def minecraft_status(host, port, timeout=2.0):
    started = time.monotonic()
    host_bytes = host.encode("utf-8")
    handshake = (
        pack_varint(0)
        + pack_varint(-1)
        + pack_varint(len(host_bytes))
        + host_bytes
        + struct.pack(">H", port)
        + pack_varint(1)
    )
    request = pack_varint(0)

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(pack_varint(len(handshake)) + handshake)
        sock.sendall(pack_varint(len(request)) + request)
        packet_length = read_varint(sock)
        packet_data = read_exact(sock, packet_length)

    offset = 0
    packet_id, consumed = decode_varint(packet_data, offset)
    offset += consumed
    if packet_id != 0:
        raise OSError(f"unexpected packet id {packet_id}")
    payload_length, consumed = decode_varint(packet_data, offset)
    offset += consumed
    payload = packet_data[offset:offset + payload_length].decode("utf-8")
    status = json.loads(payload)
    latency_ms = round((time.monotonic() - started) * 1000)
    players = status.get("players") or {}
    version = status.get("version") or {}
    description = normalize_description(status.get("description"))
    return {
        "online": True,
        "latency_ms": latency_ms,
        "minecraft_version": version.get("name", ""),
        "protocol": version.get("protocol", ""),
        "players_online": players.get("online", 0),
        "players_max": players.get("max", 0),
        "player_sample": ", ".join(player.get("name", "") for player in players.get("sample", []) if player.get("name")),
        "motd": description,
    }


def read_bedrock_status(config):
    enabled = bool(config.get("bedrock_enabled", False))
    default = {
        "bedrock_enabled": enabled,
        "bedrock_state": "inactive" if not enabled else "unknown",
        "bedrock_port": int(config.get("bedrock_port") or 19132),
        "geyser_status": "inactive" if not enabled else "unknown",
        "floodgate_status": "inactive" if not enabled else "unknown",
        "bedrock_auth": "floodgate" if enabled else "",
        "bedrock_message": "Bedrock support disabled" if not enabled else "",
    }
    if not BEDROCK_STATUS_FILE.is_file():
        return default
    try:
        payload = json.loads(BEDROCK_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    default.update(
        {
            "bedrock_enabled": bool(payload.get("enabled", enabled)),
            "bedrock_state": payload.get("state", default["bedrock_state"]),
            "bedrock_port": int(payload.get("udp_port") or default["bedrock_port"]),
            "geyser_status": payload.get("geyser", default["geyser_status"]),
            "floodgate_status": payload.get("floodgate", default["floodgate_status"]),
            "bedrock_auth": payload.get("auth", default["bedrock_auth"]),
            "bedrock_message": payload.get("message", default["bedrock_message"]),
        }
    )
    return default


def decode_varint(data, offset=0):
    value = 0
    for position in range(5):
        if offset + position >= len(data):
            raise OSError("varint truncated")
        byte = data[offset + position]
        value |= (byte & 0x7F) << (7 * position)
        if not byte & 0x80:
            return value, position + 1
    raise OSError("varint too long")


def normalize_description(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("text", "")
        extra = value.get("extra") or []
        return text + "".join(normalize_description(part) for part in extra)
    if isinstance(value, list):
        return "".join(normalize_description(part) for part in value)
    return ""


def main():
    config_path, config = first_config()
    active_world = resolve_active_world(config)
    host = os.environ.get("MINECRAFT_HOST", "127.0.0.1")
    port = int(config.get("server_port") or os.environ.get("MINECRAFT_PORT", 25565))

    data = {
        "online": False,
        "host": host,
        "port": port,
        "data_dir": str(DATA_DIR),
        "config_path": str(config_path) if config_path else "not found",
        "active_world": active_world,
        "world_exists": (DATA_DIR / active_world / "level.dat").is_file(),
        "worlds": ", ".join(list_worlds()),
        "world_count": len(list_worlds()),
        "plugin_count": directory_count(DATA_DIR / "plugins", "*.jar"),
        "log_count": directory_count(DATA_DIR / "logs", "*.log*"),
        "server_type": config.get("server_type", ""),
        "configured_version": config.get("minecraft_version", ""),
    }
    data.update(backup_stats())

    try:
        data.update(minecraft_status(host, port))
    except Exception as exc:
        data["error"] = str(exc)
    data.update(read_bedrock_status(config))
    if player_model and data.get("online"):
        players = player_model.load_players(DATA_DIR, config, data.get("player_sample", ""))
        data.update(players)
    else:
        data.setdefault("players_java", int(data.get("players_online") or 0))
        data.setdefault("players_bedrock", 0)
        data.setdefault("players", [])

    print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
