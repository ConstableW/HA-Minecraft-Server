# Home Assistant Dashboard Sensors

Legacy manual YAML helpers. Prefer the add-on MQTT Discovery exporter when you do not want YAML-based helpers.

## Install

1. If you use the add-on auto installer, start the add-on once and check that these files exist in `/config`.
2. If you install manually, copy `minecraft_addon_config_sensor.py` to `/config/minecraft_addon_config_sensor.py`.
2. Copy `minecraft_server_sensor.py` to `/config/minecraft_server_sensor.py`.
3. Copy `minecraft_package.yaml` to `/config/packages/minecraft_package.yaml`.
4. Make sure packages are enabled in `/config/configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

5. Restart Home Assistant.
6. Open Developer Tools -> States and check:

```text
sensor.minecraft_addon_config
sensor.minecraft_server_status
binary_sensor.minecraft_server_online
sensor.minecraft_players_online
sensor.minecraft_latency
sensor.minecraft_backup_count
sensor.minecraft_active_world
sensor.minecraft_world_1_name
```

7. Replace the Minecraft dashboard raw YAML with `dashboards/minecraft-dashboard.yaml`.

The package also adds world switcher scripts:

```text
script.minecraft_switch_world_1
script.minecraft_switch_world_2
script.minecraft_switch_world_3
script.minecraft_switch_custom_world
```

Each script writes `/media/minecraft-java/dashboard-command.yaml` and restarts the add-on. On startup the add-on imports the command, updates `world_slot`, removes the command file, and tries to write the new option back to the Home Assistant add-on UI.

## Why this is needed

Lovelace dashboards cannot read Home Assistant add-on option files directly. The command line sensor reads the durable add-on YAML from `/media/minecraft-java/addon-options.yaml` first, then falls back to `/addon_configs/local_minecraft_java/options.yaml`, and exposes the values as normal Home Assistant entities.

`/addon_configs/local_minecraft_java/options.yaml` is still kept as a readable runtime copy, but `/media/minecraft-java/addon-options.yaml` is the reinstall-safe backup because it lives with the Minecraft data.

`minecraft_server_sensor.py` reads `/media/minecraft-java` for world, plugin, log, and backup counts. It also tries a Minecraft status ping to `127.0.0.1:25565`; when the server is running this fills player count, max players, latency, MOTD, and server version.

Useful generated entities:

- `binary_sensor.minecraft_server_online`
- `sensor.minecraft_players_online`
- `sensor.minecraft_players_max`
- `sensor.minecraft_latency`
- `sensor.minecraft_server_version`
- `sensor.minecraft_plugin_count`
- `sensor.minecraft_backup_count`
- `sensor.minecraft_backup_total`
- `sensor.minecraft_latest_backup`
- `sensor.minecraft_world_count`
- `sensor.minecraft_world_exists`
