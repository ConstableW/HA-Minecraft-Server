# Minecraft Java Server Add-on Documentation

## Options

The add-on validates options before starting Minecraft. It rejects missing EULA acceptance, invalid memory strings, `memory_initial` greater than `memory_max`, and an empty RCON password when RCON is enabled. Persistent Minecraft data lives in `/media/minecraft-java`.

The default Home Assistant configuration view intentionally shows only the everyday options. Less common tuning and recovery options stay in the schema as optional fields and can be added manually when needed.

At startup the add-on loads `/media/minecraft-java/addon-options.yaml` as the persistent source of truth once the file exists. If it does not exist yet, the file is seeded from `/data/options.json`. The add-on also writes `/ha-addon-config/options.yaml` as a readable runtime copy in Home Assistant's `addon_config` storage and best-effort mirrors the media YAML back through the Supervisor API.

Everyday options:

- `eula`: Must be `true` before startup. You are responsible for accepting Mojang's EULA.
- `server_autostart`: Starts Minecraft automatically when the add-on controller starts. Defaults to `false`.
- `server_type`: `PAPER`, `PURPUR`, or `VANILLA`.
- `minecraft_version`: `LATEST` or a specific Minecraft version. Pin this if Paper/Purpur lags behind the newest Vanilla client release.
- `server_owner`: Optional Minecraft player name for the main owner account. It is added to `OPS` automatically.
- `world_slot`: Selects the active world profile. `CUSTOM` uses `level_name`; `WORLD_1`, `WORLD_2`, and `WORLD_3` use the matching world name fields.
- `world_1_name`, `world_2_name`, `world_3_name`, `level_name`: World folder names below `/media/minecraft-java`.
- `world_1_server_name`, `world_2_server_name`, `world_3_server_name`: Optional per-slot server names. The active slot name overrides `server_name`.
- `world_1_owner`, `world_2_owner`, `world_3_owner`: Optional per-slot owner accounts. The active slot owner overrides `server_owner` and is added to `OPS`.
- `allow_create_worlds`: Allows a selected missing world to be created. Existing media worlds are matched case-insensitively first.
- `block_disruptive_actions_when_players_online`: Blocks MQTT restart and world-slot switches while players are online. Defaults to `true`.
- `enable_autopause` and `idle_shutdown_minutes`: Automatically stop only the Minecraft server process after the configured number of minutes with no online players.
- `memory_initial` and `memory_max`: Java heap limits such as `2G` and `6G`.
- `enable_rcon` and `rcon_password`: Required for consistent backups and player management.
- `enable_auto_backup`: Runs periodic local backups.
- `backup_on_shutdown`: Creates a final backup during controlled shutdown.
- `backup_on_world_switch`: Allows shutdown backups during MQTT/world-slot switches. Defaults to `false` to avoid backup storms.
- `backup_min_interval_minutes`: Minimum time between successful backups. Defaults to `30`.
- `backup_keep_last`: Keeps only the newest X backups in addition to age-based retention. `0` disables the count limit.
- `plugins_enabled`, `plugin_preset`, `modrinth_plugins`, `plugin_urls`: Optional Paper/Purpur plugin installation. Manual `.jar` files in `/media/minecraft-java/plugins` work independently and are the recommended baseline.
- `bedrock_enabled` and `bedrock_port`: Optional Geyser-Spigot plus Floodgate support for Bedrock clients. Defaults to disabled and UDP `19132`.
- `security_enabled`: Enables the Security Center. It can manage Paper Anti-Xray, Grim AntiCheat, Grim `punishments.yml`, Security status and a bounded event log.
- `anti_xray_enabled` and `anti_xray_engine_mode`: Manage Paper/Purpur Anti-Xray in the active Paper world configuration. Mode `2` is the recommended strong default.
- `grim_enabled` and `grim_auto_update`: Install, enable, disable and update Grim AntiCheat from official stable sources. Existing Grim config is not deleted when disabled.
- `grim_update_channel`: `stable` by default. `beta` allows an admin-triggered install of the newest official Modrinth pre-release when a stable build is not compatible with the running server.
- `grim_punishments_managed`: Exposes Grim `punishments.yml` in Ingress. The real file remains authoritative and is backed up before writes.
- `xray_suspicion_enabled`: Enables the rolling Xray suspicion score. It needs block-break observations from the API or a bridge/log source and does not claim to prove a client mod.
- `xray_suspicion_rcon_alerts`: Sends a cooldown-limited RCON `say` message with `Cheatverdacht` when the score threshold is reached.
- `plugin_browser_enabled`: Starts the Home Assistant Ingress admin panel with status, start/stop/restart, guarded world switching, common configuration fields, and a JAR explorer for `/media/minecraft-java/plugins` and `/media/minecraft-java/mods`.
- `ingress_admin_password`: Lets normal Home Assistant users unlock Ingress admin mode. Home Assistant admin users are accepted automatically when Home Assistant forwards an admin header. Without admin mode, users can still start, stop, restart, switch worlds, and use the JAR explorer.
- `ingress_admin_users`: Optional comma-separated allowlist of Home Assistant usernames, user IDs, or email addresses that should enter Ingress admin mode without the password when Home Assistant forwards a matching user header.
- `mqtt_enabled`, `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password`: Optional MQTT Discovery exporter for Home Assistant entities without YAML helpers.
- `mqtt_discovery_prefix`, `mqtt_topic_prefix`, `mqtt_status_interval`: MQTT topic and refresh tuning.
- `playit_enabled` and `playit_secret`: Optional manual playit.gg agent controls for Minecraft Java. Configure the playit tunnel target as `127.0.0.1:25565`.
- `playit_public_address`: Optional display fallback for the public playit.gg address when the local agent does not log it.

Advanced optional options:

- `view_distance`, `simulation_distance`, `spawn_protection`, `allow_flight`, `allow_nether`.
- `backup_interval_hours`, `backup_retention_days`, `backup_min_interval_minutes`, `backup_keep_last`, `backup_on_start`.
- `autopause_timeout_seconds`: Legacy idle shutdown timeout. `idle_shutdown_minutes` is preferred when both are set.
- `java_tool_options`, `extra_server_options`, `stop_grace_period_seconds`.
- `remove_old_plugins`: Removes plugins managed by the automatic plugin list when entries are removed. Use carefully.

## Bedrock / Geyser / Floodgate

`bedrock_enabled: true` enables Bedrock support as one integrated feature. The add-on prepares both Geyser-Spigot and Floodgate before starting Minecraft, because this setup expects them to work together. It requires `PAPER` or `PURPUR`; Vanilla cannot load these plugins.

The add-on definition always publishes UDP `19132` in addition to Java TCP `25565`. Home Assistant add-on port mappings are static, so disabling Bedrock only disables the plugins, not the published port entry.

Geyser config is updated with a YAML parser and only changes the required values: Bedrock bind address, Bedrock UDP port, and Floodgate authentication in either the current `java.auth-type` format or the older `remote.auth-type` format. Unknown custom settings are kept. The first existing config is preserved as `config.yml.bak`.

Disabling Bedrock moves matching Geyser/Floodgate jar files into `/media/minecraft-java/plugins-disabled`. It does not delete `/media/minecraft-java/plugins/Geyser-Spigot`, `/media/minecraft-java/plugins/floodgate`, `key.pem`, caches, resource packs, mappings, or manually installed unrelated plugins.

The player model uses the live server player list as canonical and enriches it with Geyser/Floodgate log mappings. Bedrock display names such as `Constable W1949`, Geyser names such as `Constable_W1949`, and Floodgate server names such as `.Constable_W1949` are correlated into one active session. Autopause, dashboard status, MQTT `players_online`, and the Home Assistant sensor all use the deduplicated total.

Console clients need one extra network note: Xbox, PlayStation, and Nintendo Switch do not allow typing arbitrary Bedrock server addresses. Use a local DNS override only if you need console joining, for example `mco.cubecraft.net -> 192.168.32.14`. The domain should be discovered from DNS query logs and is not managed by this add-on.

## Startup Flow

1. Read `/data/options.json` only as the first-start seed.
2. Load `/media/minecraft-java/addon-options.yaml` as the active configuration, creating it from Supervisor options if missing.
3. Copy the active media YAML to `/ha-addon-config/options.yaml` and mirror it back to Supervisor options when possible.
4. Validate values.
5. Start the optional MQTT Discovery exporter when enabled.
6. Keep the optional playit.gg agent stopped until it is manually started from Ingress.
7. If `server_autostart` is enabled, require explicit EULA acceptance.
8. Create `/media/minecraft-java`.
9. Copy legacy data from `/data/minecraft` or `/hassio_data` only if the new directory is empty.
10. Link Minecraft storage entries from `/data` into `/media/minecraft-java` while preserving `/data/options.json`.
11. Prepare or disable Bedrock plugins according to `bedrock_enabled`.
12. Prepare Security Center features before Minecraft starts: Paper Anti-Xray YAML, Grim plugin jar state, Grim status, punishments metadata and event retention.

Security status is re-evaluated from `logs/latest.log` after startup. A Grim jar or Bukkit plugin listing is not considered healthy by itself; PacketEvents, packet registration, the PacketManager and Grim command registration must not have emitted critical startup failures. If ViaVersion has `fix-1_21-placement-rotation` enabled, Ingress can back up the config and set only that key to `false`.

Xray/Yray detection is treated as statistical suspicion. The add-on keeps `/media/minecraft-java/security/xray-suspicion.json`, polls the active world's `stats/*.json` and `players/stats/*.json` files every runtime Security poll, scores new valuable ore finds in a rolling time window, and records alerts in the Security event log. The first scan seeds a baseline. Tunable scoring rules are stored in `/media/minecraft-java/security/xray-rules.json`. RCON alerts are Minecraft console commands only; no user-provided value is passed to the operating-system shell.
12. Export supported `itzg/minecraft-server` environment variables.
13. Start `/start` as the managed Minecraft child process.
14. Keep the add-on controller running for MQTT start, stop, restart, world-slot commands, and playit.gg controls.

## Shutdown Flow

On SIGTERM or SIGINT, the controller saves the world over RCON when enabled, optionally creates a shutdown backup, sends `stop` over RCON, waits up to `stop_grace_period_seconds`, and only then kills the Minecraft process. Shutdown backups are skipped during world-slot switches unless `backup_on_world_switch` is enabled, and all backup reasons respect `backup_min_interval_minutes`.

MQTT start, stop, restart, and world-slot commands control the Minecraft child process without stopping the add-on container. This keeps MQTT Discovery alive and lets non-admin dashboard users control the family server without Supervisor add-on permissions.

The Ingress panel is available to logged-in Home Assistant users, not only administrators. User mode can start, stop, restart, switch worlds, and use the JAR explorer. Home Assistant admin users enter admin mode automatically only when Home Assistant forwards an admin header. If user headers are forwarded, `ingress_admin_users` can allow matching forwarded usernames, user IDs, or email addresses into admin mode without a password. If Home Assistant does not forward any user data to the add-on, use `ingress_admin_password` for admin mode. Admin mode can edit common server options directly, writes changes to `/media/minecraft-java/addon-options.yaml`, mirrors them to `/ha-addon-config/options.yaml`, updates the controller's effective JSON copy, and tries to sync the same values back to Supervisor options when the Supervisor API is available.

Admin mode also exposes player management when RCON is enabled and `rcon_password` is set. It can kick all online players, kick or ban individual online players, and unban entries from `banned-players.json`.

Admin mode can start and restart playit.gg when `playit_enabled` is true and `playit_secret` is set. User mode can see the configured playit address/status and stop the tunnel. The agent is a separate process from Minecraft and starts only through the Ingress playit buttons, so restarting playit.gg does not restart the Minecraft server.

Idle shutdown uses the same local Minecraft status query as the sensors. If the query cannot confirm that the player count is zero, it does not stop the server.

## Manual Restore

Stop the add-on, unpack a backup from `/media/minecraft-java/backups` into `/media/minecraft-java`, check ownership and permissions if needed, then start the add-on. Do not unpack one backup over a running server.

If conflicting files are found during local migration, the media folder wins. The old `/data` copy is moved into `/media/minecraft-java/migration-conflicts` for manual inspection.

Empty or invalid list files such as `ops.json`, `whitelist.json`, `banned-players.json`, and `banned-ips.json` are repaired to valid empty JSON arrays. Invalid originals are renamed with an `.invalid-...` suffix.

Vanilla uses real runtime world directories below `/data` because Mojang's file-layout upgrader rejects symlinked world folders. The add-on syncs Vanilla worlds from media on startup and back to media before backups and after shutdown.

Before Minecraft starts, the wrapper checks whether the configured `level_name` already exists in `/media/minecraft-java/<level_name>/level.dat`. If that exact world is missing but other worlds are present in the media folder, startup fails and lists the detected candidates. This prevents accidental fresh-world creation after changing `level_name` or switching between Vanilla, Paper, and Purpur.

Home Assistant add-on forms cannot populate a live dropdown from folders in `/media`, so the add-on exposes three configurable world slots. Set the slot names once, then switch `world_slot` between `WORLD_1`, `WORLD_2`, `WORLD_3`, or `CUSTOM`.

If a configured world differs only by uppercase/lowercase from an existing folder, the existing folder name wins. For example, `Loszwergtown` starts `/media/minecraft-java/loszwergtown`. If no matching world exists and `allow_create_worlds` is enabled, Minecraft creates the selected world.

## Switching Server Types

Paper and Purpur worlds are not guaranteed to start as Vanilla worlds. If Vanilla reports missing Paper data packs or world generation settings, start Vanilla with a separate `level_name`, restore a pre-Paper backup, or temporarily use `extra_server_options: "--safeMode"` for recovery.

## Plugin Presets

Home Assistant add-on configuration cannot create custom tabs, so plugin settings are shown as a compact block in the main configuration. `plugins_enabled: false` with `plugin_preset: MANUAL` means the add-on expects you to manage `.jar` files in `/media/minecraft-java/plugins` yourself. Presets cover common family-server needs such as TPA, permissions, Bedrock joining, voice chat, and maps.

`modrinth_plugins` accepts Modrinth project slugs, one per line. The wrapper resolves them to plugin jar URLs and passes the final list to the upstream `itzg/minecraft-server` `PLUGINS` mechanism. Custom plugin URLs can be added through `plugin_urls`, one URL per line.

Plugins are ignored for `VANILLA`, because Vanilla Minecraft does not load Bukkit/Paper plugins.

## Manual Validation

Build on amd64 with the Home Assistant builder, install the add-on, set `eula: true`, start Paper, join the server, place a block, restart the add-on, and verify the world state remains. Then trigger a backup, restore it, and verify startup.
