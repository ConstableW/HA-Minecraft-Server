# Changelog

## 0.6.25

- Add Dashboard Bedrock controls for enabling, disabling, and applying Bedrock support changes with a Minecraft restart.
- Keep Bedrock control actions on the Dashboard and clarify that Geyser/Floodgate plugin changes take effect on the next Minecraft start.

## 0.6.24

- Keep Admin player actions such as give, quick give, gamemode, OP/deOP, broadcast, and moderation on the Admin tab after submission.
- Keep Admin login, logout, and password actions on the Admin tab instead of returning to the Dashboard.
- Add a temporary Admin-tab Auto-Refresh pause that disables Ingress meta-refresh for five minutes and clears automatically on the next server start, restart, or world switch.

## 0.6.23

- Add a dedicated Ingress Systemmessages tab for RCON-generated messages and save commands, including toggles for stop announcements, stop `save-all`, backup `save-all/save-off/save-on`, Xray stats save, and Xray suspicion alerts.
- Detect Bukkit/Paper plugin JARs with invalid internal `plugin.yml` names, show the internal name/status in the Plugins tab, and allow admins to move invalid plugin JARs into `plugins-disabled`.
- Align Grim datastore `serverName` style configuration keys with the active world/server label when existing Grim YAML config files expose such a key.

## 0.6.22

- Add a dedicated Ingress Backup tab with backup configuration, manual `Backup now`, restore actions, and a full backup list.
- Add persistent backup flags through `.persistent` sidecars so selected backups are excluded from retention and keep-last rotation until the flag is removed or the backup is deleted.
- Add Backup tab actions to set or remove persistent flags and delete backup archives with their sidecar metadata.
- Add controller commands for manual backups and full restore from existing `/media/minecraft-java/backups/minecraft-*.tar.gz` archives.
- Keep manual Backup-now runs outside the backup cooldown while scheduled, startup, shutdown, and world-switch backups still honor the configured minimum interval.

## 0.6.21

- Split the Ingress panel into clearer Dashboard, Spieler, Security, Plugins, Mods, and Admin tabs.
- Add a player intelligence view with known players, last seen data, online/offline/banned status, current and maximum Security score, related Security events, mined materials, mined blocks, and total play time where Minecraft stats provide it.
- Add sortable player columns, including sorting by current and maximum Security score.
- Add admin reset actions for a player's current Xray suspicion score and stored maximum Security score.
- Move Kick, Ban, Unban, and the visible ban list into the Security tab while keeping the Dashboard focused on live status and a read-only Security overview.
- Move plugin and mod file management into separate Ingress tabs.

## 0.6.20

- Make Xray suspicion scoring configurable from the Security Center, including rolling time window, alert threshold, alert cooldown, series bonuses, and per-material weights.
- Keep Xray suspicion scores rolling instead of only increasing forever by pruning observations outside the configured time window.
- Scan active-world Minecraft stats from both `stats/*.json` and `players/stats/*.json`, and ignore stats from inactive worlds.
- Add optional RCON `save-all` before Xray stats polling so in-memory Minecraft stats are more likely to be flushed before scoring.
- Add player-specific and global Xray score reset actions.

## 0.6.19

- Add runtime Xray suspicion scoring based on Minecraft mined-block stats and optional bridge observations.
- Emit cooldown-limited RCON `Cheatverdacht` messages when a player crosses the configured Xray suspicion threshold.
- Store Security events and Xray score state below `/media/minecraft-java/security`.
- Add Grim compatible-preview handling with the `grim_auto_compatible_beta` switch for newer Paper/Purpur server versions.
- Disable Bukkit connection throttling inside the managed server runtime to avoid repeated false `Connection throttled` reconnect failures behind the add-on/controller setup.

## 0.6.18

- Clean up the Ingress user-mode panel when Home Assistant does not forward user headers, falling back quietly to the admin-password flow instead of showing a persistent warning.

## 0.6.17

- Stop reporting Paper/Purpur migration as pending after legacy `<world>_nether` and `<world>_the_end` folders are gone.

## 0.6.16

- Stage legacy Paper/Purpur dimension folders as real runtime copies instead of symlinks so Paper can complete its one-time CraftBukkit dimension migration.
- Move migrated legacy dimension folders from media storage into `migration-conflicts` only after Paper removes the runtime copies, preventing repeated startup migrations.

## 0.6.15

- Keep honoring existing Paper/Purpur migration backup markers created by 0.6.14 while using a new marker for future safety backups.
- Detect migration-pending Nether and End data in both current `DIM-1`/`DIM1` folders and legacy `<world>_nether`/`<world>_the_end` folders.

## 0.6.14

- Set Paper/Purpur's official `paper.disableMigrationDelay` Java property automatically so world storage migration no longer adds the 30 second startup delay.
- Create a one-time `paper-migration` backup per existing world before the first Paper/Purpur storage migration is allowed to continue.

## 0.6.13

- Show a pending world slot immediately after an Ingress Start or Welt wechseln action so the panel no longer needs a manual browser refresh to display the selected target world.
- Keep the Ingress panel auto-refreshing while a world-slot change is still pending.

## 0.6.12

- Read the selected world slot from Ingress command form posts instead of only from the URL query.
- Keep Start, Stop, and Restart visibility aligned with the current server state.
- Restore a stable Ingress control layout for the world selector and server buttons.

## 0.6.11

- Make the cached server-JAR corruption guard fast and non-invasive by checking only ZIP/JAR structure before startup instead of scanning every archive entry.

## 0.6.10

- Make the Ingress Start button submit the selected world dropdown slot, so starting from Offline can launch a different configured world directly.

## 0.6.9

- Apply Ingress world-slot changes before starting Minecraft so selecting a world while the server is stopped starts the selected world instead of the previously active one.

## 0.6.8

- Add no-cache headers to Ingress HTML and redirects so User/Admin mode text updates immediately after add-on upgrades.

## 0.6.7

- Update the User mode description to include player moderation and playit stop.
- Replace the confusing `Erkannter HA-User: keiner` text with a clearer forwarded-header hint.

## 0.6.6

- Add `playit_public_address` so a playit.gg address can be shown even when the agent does not log it.
- Show the playit.gg status card in User mode when playit is configured.
- Allow User mode to stop playit.gg while keeping start and restart restricted to Admin mode.

## 0.6.5

- Show the simple online-player and ban-list moderation tables in User mode.
- Allow User-mode Kick, Ban, and Unban actions through RCON.
- Keep OP/deOP, gamemode, give, broadcast, and Kick all restricted to Admin mode.

## 0.6.4

- Replace the admin player tables with player cards in the Ingress panel.
- Add admin-only RCON actions for broadcast, OP/deOP, gamemode changes, quick give, and custom give.
- Show OP status badges from `ops.json` when available.

## 0.6.3

- Redirect after Ingress server commands so auto-refresh cannot re-trigger `/command?action=start`.
- Treat GET `/command` as a harmless redirect instead of queuing another server command.
- Log an active startup as "start is already in progress" instead of saying the Minecraft server is already running.

## 0.6.2

- Accept Ingress `/command` links sent as GET requests and redirect back to the panel instead of returning 404.
- Remove corrupt cached Paper, Purpur, or Vanilla server JAR files before startup so the base image downloads a clean copy.

## 0.6.1

- Add polished Home Assistant add-on icon and logo assets.
- Use the Minecraft panel icon for the Ingress sidebar entry.

## 0.6.0

- Add optional playit.gg agent support for exposing the Minecraft TCP server without router port forwarding.
- Add `playit_enabled` and `playit_secret` options.
- Add admin-only Ingress controls for playit.gg start, stop, restart, and runtime status.
- Add visible idle shutdown settings with `enable_autopause` and `idle_shutdown_minutes`.
- Let idle shutdown use the normal Minecraft status query, so it no longer requires RCON.
- Fix partial config restore after reinstall by treating merged YAML values as the active runtime config and adding missing per-world defaults.
- Pass the playit.gg secret to the standalone agent through the CLI `--secret` option instead of the Docker-only `SECRET_KEY` environment variable.
- Decode MQTT CONNACK errors so authentication failures show a readable reason.
- Make `/media/minecraft-java/addon-options.yaml` the single source of truth once it exists; Supervisor options are now only seeded from or mirrored back to that file.
- Remove unsupported playit.gg CLI flags for the standalone binary.
- Accept playit.gg Ingress actions through GET as well as POST to avoid Home Assistant Ingress link handling quirks.
- Capture public playit.gg tunnel addresses from agent logs and show the detected address in the Ingress admin panel.
- Stop autostarting the playit.gg agent; `playit_enabled` now only enables manual Ingress controls.
- Redirect after playit.gg Ingress actions so browser refresh cannot repeat start/stop/restart commands.

## 0.5.10

- Recognize additional forwarded Home Assistant/proxy user headers for `ingress_admin_users`.
- Show detected forwarded user values in the Ingress user-mode card to diagnose passwordless admin unlock.
- Log detected forwarded user values and the configured allowlist when admin password login fails.

## 0.5.9

- Avoid RCON player-list polling while Minecraft is offline or still starting.
- Show an admin-mode hint instead of logging repeated RCON connection-refused messages before the server is ready.

## 0.5.8

- Show `Start pending` or `Stop pending` in the Ingress status while the Minecraft server is starting or stopping.
- Auto-refresh the Ingress page while a start or stop is pending.

## 0.5.7

- Refresh the Ingress page automatically after admin login, logout, and password setup by using redirect-after-post.

## 0.5.6

- Add `ingress_admin_users` as a passwordless Ingress admin allowlist for forwarded Home Assistant usernames, user IDs, or email addresses.
- Show whether admin mode was unlocked by Home Assistant admin headers or the configured admin-user allowlist.

## 0.5.5

- Add admin-only Ingress player management through RCON.
- Add Kick all next to the server control buttons in admin mode.
- Add online player Kick/Ban actions and banned player Unban actions in admin mode.
- Add RCON enable/password fields to the Ingress admin configuration panel.

## 0.5.4

- Automatically enable Ingress admin mode for Home Assistant admin users when Home Assistant forwards an admin header.
- Keep password-based admin unlock for normal Home Assistant users.
- Require admin mode before setting or changing the Ingress admin password.

## 0.5.3

- Add an Ingress user/admin mode split.
- Keep start, stop, restart, world switching, and the JAR explorer available in user mode.
- Protect configuration, world-slot definitions, plugin automation, MQTT, and backup settings behind `ingress_admin_password`.

## 0.5.2

- Allow non-admin Home Assistant users to open the Minecraft Ingress panel.

## 0.5.1

- Add an Ingress configuration panel for common server, world-slot, plugin, MQTT, and backup settings.
- Save Ingress configuration changes to `/media/minecraft-java/addon-options.yaml`, the runtime add-on config copy, local options JSON, and Supervisor options when available.
- Fix the example dashboard Ingress link to open the actual local add-on panel path.

## 0.5.0

- Expand the Ingress page into a Minecraft server panel with status, player count, start/stop/restart controls, world switching, and the JAR explorer.

## 0.4.9

- Block MQTT restart and world-slot switch commands while players are online by default.
- Hide restart and world-switch controls in the example dashboard while players are online.

## 0.4.8

- Extend the Ingress JAR explorer to manage both `/media/minecraft-java/plugins` and `/media/minecraft-java/mods`.

## 0.4.7

- Add a Home Assistant Ingress plugin explorer for `/media/minecraft-java/plugins`.
- Add `plugin_browser_enabled` to enable or disable the plugin explorer.
- Add plugin explorer cards to the example dashboard.

## 0.4.6

- Override the inherited Minecraft server healthcheck with a controller readiness healthcheck so the add-on can be healthy while Minecraft is intentionally stopped.

## 0.4.5

- Align runtime shell fallbacks with safe defaults so missing options no longer enable autostart or backups.

## 0.4.4

- Change safety-sensitive defaults to `false`: `server_autostart`, `enable_auto_backup`, and `backup_on_shutdown`.
- Prevent fresh or reset Supervisor options from starting Minecraft or creating backups unless explicitly enabled.

## 0.4.3

- Preserve YAML values such as `backup_on_shutdown: false`, `enable_auto_backup: false`, and `server_autostart: false` when Supervisor only reports packaged defaults.
- Log the effective shutdown and automatic backup flags during controller startup.

## 0.4.2

- Preserve reinstall-safe YAML values for options that are missing from Supervisor's current options payload, including `server_autostart`.

## 0.4.1

- Ignore retained MQTT command messages so old `start` or world-switch commands cannot fire immediately after add-on startup.
- Clear stale local controller command files during controller startup.
- Log the effective `server_autostart` value during startup for easier diagnosis.

## 0.4.0

- Keep the add-on container running as a lightweight controller while the Minecraft server runs as a managed child process.
- Add MQTT start, stop, restart, and world-slot commands that no longer call Supervisor add-on services.
- Add `server_autostart` so Minecraft can either start with the add-on or wait idle for dashboard control.

## 0.1.9

- Add dashboard command support for switching `world_slot` through Home Assistant scripts.

## 0.1.8

- Add per-world-slot server names so each configured world can expose its own display name.

## 0.1.7

- Restore add-on options from dedicated `addon_config` YAML after reinstall when Supervisor options reset to defaults.
- Best-effort write restored options back to the Home Assistant add-on UI through the Supervisor API.

## 0.1.6

- Add per-world-slot owners so each configured world can add its own operator automatically.

## 0.1.5

- Move mirrored add-on options from Home Assistant's main `/config` mount to dedicated `addon_config` storage.
- Quarantine accidentally copied Home Assistant config files from Minecraft config storage.

## 0.1.4

- Mirror the Home Assistant add-on UI configuration to `/config/addons_config/minecraft_java/options.yaml` on startup.

## 0.1.3

- Match configured world names case-insensitively against existing media worlds.
- Add `allow_create_worlds` so world slots can intentionally create new worlds.

## 0.1.2

- Add configurable world slots for switching the active world from the Home Assistant add-on UI.

## 0.1.1

- Prevent accidental fresh-world creation when `level_name` does not match an existing media world.
- Repair stale `/data` symlinks so they point back to `/media/minecraft-java`.

## 0.1.0

- Initial Home Assistant OS add-on for Minecraft Java.
- Added Paper, Purpur, and Vanilla support through `itzg/minecraft-server:2026.7.0`.
- Added persistent `/data/minecraft` storage, validation, graceful shutdown, local backups, backup rotation, and optional autopause.
