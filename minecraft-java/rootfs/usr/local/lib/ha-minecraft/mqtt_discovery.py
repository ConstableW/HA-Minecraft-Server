#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import socket
import threading
import time
from pathlib import Path


SENSOR_MODULE = Path(os.environ.get("MINECRAFT_SENSOR_MODULE", "/usr/local/share/ha-minecraft/home-assistant/minecraft_server_sensor.py"))
COMMAND_DIR = Path(os.environ.get("MINECRAFT_COMMAND_DIR", "/tmp/ha-minecraft-commands"))
VALID_WORLD_SLOTS = {"CUSTOM", "WORLD_1", "WORLD_2", "WORLD_3"}
MQTT_CONNACK_CODES = {
    1: "unacceptable protocol version",
    2: "identifier rejected",
    3: "server unavailable",
    4: "bad username or password",
    5: "not authorized",
}


def encode_varint(value):
    encoded = bytearray()
    while True:
        byte = value % 128
        value //= 128
        if value > 0:
            byte |= 128
        encoded.append(byte)
        if value == 0:
            return bytes(encoded)


def mqtt_string(value):
    data = str(value).encode("utf-8")
    return len(data).to_bytes(2, "big") + data


def mqtt_packet(packet_type, payload):
    return bytes([packet_type]) + encode_varint(len(payload)) + payload


class MqttClient:
    def __init__(self, host, port, username="", password="", client_id="ha-minecraft-addon"):
        self.host = host
        self.port = int(port)
        self.username = username or ""
        self.password = password or ""
        self.client_id = client_id
        self.sock = None

    def connect(self):
        flags = 0x02
        payload = mqtt_string(self.client_id)
        if self.username:
            flags |= 0x80
            payload += mqtt_string(self.username)
        if self.password:
            flags |= 0x40
            payload += mqtt_string(self.password)

        variable_header = mqtt_string("MQTT") + bytes([4, flags]) + int(60).to_bytes(2, "big")
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        self.sock.settimeout(10)
        self.sock.sendall(mqtt_packet(0x10, variable_header + payload))
        response = self.sock.recv(4)
        if len(response) < 4 or response[0] != 0x20 or response[3] != 0:
            code = response[3] if len(response) >= 4 and response[0] == 0x20 else None
            reason = MQTT_CONNACK_CODES.get(code, "unknown") if code is not None else "invalid response"
            raise OSError(f"MQTT connect failed: {reason} ({response!r})")

    def publish(self, topic, payload, retain=False):
        if not isinstance(payload, bytes):
            payload = str(payload).encode("utf-8")
        header = 0x31 if retain else 0x30
        self.sock.sendall(mqtt_packet(header, mqtt_string(topic) + payload))

    def subscribe(self, topic, packet_id=1):
        payload = packet_id.to_bytes(2, "big") + mqtt_string(topic) + bytes([0])
        self.sock.sendall(mqtt_packet(0x82, payload))
        packet_type, body = self.read_packet()
        if packet_type != 0x90:
            raise OSError(f"unexpected MQTT subscribe response: {packet_type!r}")

    def ping(self):
        self.sock.sendall(mqtt_packet(0xC0, b""))

    def read_packet(self):
        fixed = self.sock.recv(1)
        if not fixed:
            raise OSError("connection closed")
        multiplier = 1
        remaining = 0
        while True:
            raw = self.sock.recv(1)
            if not raw:
                raise OSError("connection closed")
            byte = raw[0]
            remaining += (byte & 127) * multiplier
            if byte & 128 == 0:
                break
            multiplier *= 128
        body = read_exact(self.sock, remaining)
        return fixed[0], body

    def close(self):
        if self.sock:
            try:
                self.sock.sendall(mqtt_packet(0xE0, b""))
            finally:
                self.sock.close()
                self.sock = None


def load_sensor_module():
    spec = importlib.util.spec_from_file_location("minecraft_server_sensor", SENSOR_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_exact(sock, length):
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise OSError("connection closed")
        data.extend(chunk)
    return bytes(data)


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


def state_payload(module):
    config_path, config = module.first_config()
    active_slot = str(config.get("world_slot") or "CUSTOM")
    active_world = module.resolve_active_world(config)
    active_server_name = resolve_slot_value(config, active_slot, "server_name", "server_name")
    active_owner = resolve_slot_value(config, active_slot, "owner", "server_owner")
    data = {
        "online": False,
        "active_slot": active_slot,
        "active_world": active_world,
        "active_server_name": active_server_name,
        "active_owner": active_owner,
        "world_1_name": config.get("world_1_name", ""),
        "world_1_server_name": config.get("world_1_server_name", ""),
        "world_1_owner": config.get("world_1_owner", ""),
        "world_2_name": config.get("world_2_name", ""),
        "world_2_server_name": config.get("world_2_server_name", ""),
        "world_2_owner": config.get("world_2_owner", ""),
        "world_3_name": config.get("world_3_name", ""),
        "world_3_server_name": config.get("world_3_server_name", ""),
        "world_3_owner": config.get("world_3_owner", ""),
        "world_exists": (module.DATA_DIR / active_world / "level.dat").is_file(),
        "worlds": ", ".join(module.list_worlds()),
        "world_count": len(module.list_worlds()),
        "plugin_count": module.directory_count(module.DATA_DIR / "plugins", "*.jar"),
        "log_count": module.directory_count(module.DATA_DIR / "logs", "*.log*"),
        "server_type": config.get("server_type", ""),
        "configured_version": config.get("minecraft_version", ""),
        "config_path": str(config_path) if config_path else "not found",
    }
    data.update(module.backup_stats())
    try:
        port = int(config.get("server_port") or os.environ.get("MINECRAFT_PORT", 25565))
        data.update(module.minecraft_status("127.0.0.1", port))
    except Exception as exc:
        data["error"] = str(exc)
    if hasattr(module, "read_bedrock_status"):
        data.update(module.read_bedrock_status(config))
    if data.get("online") and getattr(module, "player_model", None):
        data.update(module.player_model.load_players(module.DATA_DIR, config, data.get("player_sample", "")))
    return data


def device():
    return {
        "identifiers": ["minecraft_java_addon"],
        "name": "Minecraft Java Server",
        "manufacturer": "Home Assistant Add-on",
        "model": "Minecraft Java",
    }


def discovery_entities(discovery_prefix, topic_prefix):
    state_topic = f"{topic_prefix}/state"
    availability_topic = f"{topic_prefix}/availability"
    base = {
        "state_topic": state_topic,
        "availability_topic": availability_topic,
        "device": device(),
    }
    sensors = [
        ("sensor", "players_online", "Minecraft Players Online", "{{ value_json.players_online | default(0) }}", "mdi:account-group", "players"),
        ("sensor", "players_java", "Minecraft Java Players", "{{ value_json.players_java | default(0) }}", "mdi:microsoft-xbox-controller", "players"),
        ("sensor", "players_bedrock", "Minecraft Bedrock Players", "{{ value_json.players_bedrock | default(0) }}", "mdi:cellphone", "players"),
        ("sensor", "players_max", "Minecraft Players Max", "{{ value_json.players_max | default(0) }}", "mdi:account-multiple-plus", "players"),
        ("sensor", "latency", "Minecraft Latency", "{{ value_json.latency_ms | default(0) }}", "mdi:speedometer", "ms"),
        ("sensor", "server_version", "Minecraft Server Version", "{{ value_json.minecraft_version | default(value_json.configured_version, true) }}", "mdi:tag", None),
        ("sensor", "active_slot", "Minecraft Active Slot", "{{ value_json.active_slot | default('CUSTOM') }}", "mdi:map-marker", None),
        ("sensor", "active_world", "Minecraft Active World", "{{ value_json.active_world | default('unknown') }}", "mdi:earth", None),
        ("sensor", "active_server_name", "Minecraft Active Server Name", "{{ value_json.active_server_name | default('unknown') }}", "mdi:server", None),
        ("sensor", "active_owner", "Minecraft Active Owner", "{{ value_json.active_owner | default('unknown') }}", "mdi:account-key", None),
        ("sensor", "world_count", "Minecraft World Count", "{{ value_json.world_count | default(0) }}", "mdi:earth-box", None),
        ("sensor", "world_1_name", "Minecraft WORLD_1 Name", "{{ value_json.world_1_name | default('not set') }}", "mdi:cube-outline", None),
        ("sensor", "world_1_server_name", "Minecraft WORLD_1 Server Name", "{{ value_json.world_1_server_name | default('not set') }}", "mdi:server", None),
        ("sensor", "world_1_owner", "Minecraft WORLD_1 Owner", "{{ value_json.world_1_owner | default('not set') }}", "mdi:account-key", None),
        ("sensor", "world_2_name", "Minecraft WORLD_2 Name", "{{ value_json.world_2_name | default('not set') }}", "mdi:cube-outline", None),
        ("sensor", "world_2_server_name", "Minecraft WORLD_2 Server Name", "{{ value_json.world_2_server_name | default('not set') }}", "mdi:server", None),
        ("sensor", "world_2_owner", "Minecraft WORLD_2 Owner", "{{ value_json.world_2_owner | default('not set') }}", "mdi:account-key", None),
        ("sensor", "world_3_name", "Minecraft WORLD_3 Name", "{{ value_json.world_3_name | default('not set') }}", "mdi:cube-outline", None),
        ("sensor", "world_3_server_name", "Minecraft WORLD_3 Server Name", "{{ value_json.world_3_server_name | default('not set') }}", "mdi:server", None),
        ("sensor", "world_3_owner", "Minecraft WORLD_3 Owner", "{{ value_json.world_3_owner | default('not set') }}", "mdi:account-key", None),
        ("sensor", "plugin_count", "Minecraft Plugin Count", "{{ value_json.plugin_count | default(0) }}", "mdi:puzzle", None),
        ("sensor", "backup_count", "Minecraft Backup Count", "{{ value_json.backup_count | default(0) }}", "mdi:backup-restore", None),
        ("sensor", "backup_total", "Minecraft Backup Total", "{{ value_json.backup_total_mb | default(0) }}", "mdi:database", "MB"),
        ("sensor", "latest_backup", "Minecraft Latest Backup", "{{ value_json.latest_backup | default('none') }}", "mdi:archive-clock", None),
        ("sensor", "server_type", "Minecraft Server Type", "{{ value_json.server_type | default('unknown') }}", "mdi:server", None),
    ]
    for platform, key, name, template, icon, unit in sensors:
        payload = dict(base)
        payload.update(
            {
                "name": name,
                "unique_id": f"minecraft_java_{key}",
                "object_id": f"minecraft_{key}",
                "value_template": template,
                "icon": icon,
            }
        )
        if unit:
            payload["unit_of_measurement"] = unit
        yield f"{discovery_prefix}/{platform}/minecraft_java/{key}/config", payload

    binary_payload = dict(base)
    binary_payload.update(
        {
            "name": "Minecraft Server Online",
            "unique_id": "minecraft_java_server_online",
            "object_id": "minecraft_server_online",
            "device_class": "connectivity",
            "value_template": "{{ 'ON' if value_json.online else 'OFF' }}",
            "payload_on": "ON",
            "payload_off": "OFF",
        }
    )
    yield f"{discovery_prefix}/binary_sensor/minecraft_java/server_online/config", binary_payload

    world_payload = dict(base)
    world_payload.update(
        {
            "name": "Minecraft World Exists",
            "unique_id": "minecraft_java_world_exists",
            "object_id": "minecraft_world_exists",
            "value_template": "{{ 'ON' if value_json.world_exists else 'OFF' }}",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:map-check",
        }
    )
    yield f"{discovery_prefix}/binary_sensor/minecraft_java/world_exists/config", world_payload

    select_payload = dict(base)
    select_payload.update(
        {
            "name": "Minecraft World Slot",
            "unique_id": "minecraft_java_world_slot",
            "object_id": "minecraft_world_slot",
            "command_topic": f"{topic_prefix}/command/world_slot",
            "value_template": "{{ value_json.active_slot | default('CUSTOM') }}",
            "options": ["CUSTOM", "WORLD_1", "WORLD_2", "WORLD_3"],
            "icon": "mdi:swap-horizontal",
        }
    )
    yield f"{discovery_prefix}/select/minecraft_java/world_slot/config", select_payload

    buttons = [
        ("start", "Minecraft Start", "mdi:play"),
        ("stop", "Minecraft Stop", "mdi:stop"),
        ("restart", "Minecraft Restart", "mdi:restart"),
    ]
    for key, name, icon in buttons:
        button_payload = dict(base)
        button_payload.update(
            {
                "name": name,
                "unique_id": f"minecraft_java_{key}",
                "object_id": f"minecraft_{key}",
                "command_topic": f"{topic_prefix}/command/{key}",
                "payload_press": key.upper(),
                "icon": icon,
            }
        )
        yield f"{discovery_prefix}/button/minecraft_java/{key}/config", button_payload


def publish_all(args, module):
    client = MqttClient(args.host, args.port, args.username, args.password, args.client_id)
    client.connect()
    try:
        for topic, payload in discovery_entities(args.discovery_prefix, args.topic_prefix):
            client.publish(topic, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), retain=True)
        client.publish(f"{args.topic_prefix}/availability", "online", retain=True)
        state = state_payload(module)
        client.publish(
            f"{args.topic_prefix}/state",
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            retain=True,
        )
        client.publish(f"{args.topic_prefix}/players/online", str(state.get("players_online", 0)), retain=True)
        client.publish(f"{args.topic_prefix}/players/java", str(state.get("players_java", 0)), retain=True)
        client.publish(f"{args.topic_prefix}/players/bedrock", str(state.get("players_bedrock", 0)), retain=True)
        client.publish(f"{args.topic_prefix}/players/list", json.dumps(state.get("players", []), ensure_ascii=False, separators=(",", ":")), retain=True)
    finally:
        client.close()


def enqueue_command(action, **payload):
    COMMAND_DIR.mkdir(parents=True, exist_ok=True)
    command = {"action": action, **payload, "created_at": int(time.time() * 1000)}
    name = f"command-{command['created_at']}-{os.getpid()}-{threading.get_ident()}.json"
    tmp_path = COMMAND_DIR / f".{name}.tmp"
    final_path = COMMAND_DIR / name
    tmp_path.write_text(json.dumps(command, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(final_path)
    print(f"[mqtt] queued controller command {action}", flush=True)


def write_world_slot_command(slot):
    slot = slot.strip().upper()
    if slot not in VALID_WORLD_SLOTS:
        print(f"[mqtt] ignored invalid world_slot command: {slot}", flush=True)
        return
    enqueue_command("world_slot", slot=slot)


def write_server_command(action):
    if action not in {"start", "stop", "restart"}:
        print(f"[mqtt] ignored invalid server action: {action}", flush=True)
        return
    enqueue_command(action)


def parse_publish(packet_type, body):
    if not body or packet_type >> 4 != 3:
        return None, None, False
    retain = bool(packet_type & 0x01)
    topic_length = int.from_bytes(body[:2], "big")
    topic = body[2:2 + topic_length].decode("utf-8")
    offset = 2 + topic_length
    qos = (packet_type >> 1) & 0x03
    if qos:
        offset += 2
    return topic, body[offset:].decode("utf-8", errors="replace"), retain


def command_listener(args):
    topic = f"{args.topic_prefix}/command/#"
    while True:
        client = MqttClient(args.host, args.port, args.username, args.password, f"{args.client_id}-commands")
        try:
            client.connect()
            client.subscribe(topic)
            client.sock.settimeout(max(min(args.interval, 30), 10))
            print(f"[mqtt] listening for commands on {topic}", flush=True)
            while True:
                try:
                    packet_type, body = client.read_packet()
                except socket.timeout:
                    client.ping()
                    continue
                if packet_type == 0xD0:
                    continue
                publish_topic, payload, retained = parse_publish(packet_type, body)
                if retained:
                    print(f"[mqtt] ignored retained command on {publish_topic}", flush=True)
                    continue
                if publish_topic == f"{args.topic_prefix}/command/world_slot" and payload:
                    write_world_slot_command(payload)
                elif publish_topic == f"{args.topic_prefix}/command/start" and payload:
                    write_server_command("start")
                elif publish_topic == f"{args.topic_prefix}/command/stop" and payload:
                    write_server_command("stop")
                elif publish_topic == f"{args.topic_prefix}/command/restart" and payload:
                    write_server_command("restart")
        except Exception as exc:
            print(f"[mqtt] command listener failed: {exc}", flush=True)
            time.sleep(max(args.interval, 5))
        finally:
            client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--discovery-prefix", default="homeassistant")
    parser.add_argument("--topic-prefix", default="minecraft_java")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--client-id", default="ha-minecraft-addon")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    module = load_sensor_module()
    if not args.once:
        threading.Thread(target=command_listener, args=(args,), daemon=True).start()
    while True:
        try:
            publish_all(args, module)
        except Exception as exc:
            print(f"[mqtt] publish failed: {exc}", flush=True)
        if args.once:
            return
        time.sleep(max(args.interval, 5))


if __name__ == "__main__":
    main()
