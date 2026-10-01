#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - local Windows Python may not have PyYAML
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "minecraft-java" / "rootfs" / "usr" / "local" / "lib" / "ha-minecraft"


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, LIB / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


security = load_module("security")
options_config = load_module("options_config")


def base_options():
    options = options_config.PACKAGED_DEFAULTS.copy()
    options.update({"server_type": "PAPER", "level_name": "world"})
    return options


class SecurityTests(unittest.TestCase):
    def test_defaults_include_security_center(self):
        self.assertIs(options_config.PACKAGED_DEFAULTS["security_enabled"], True)
        self.assertIs(options_config.PACKAGED_DEFAULTS["anti_xray_enabled"], True)
        self.assertEqual(options_config.PACKAGED_DEFAULTS["anti_xray_engine_mode"], 2)
        self.assertIs(options_config.PACKAGED_DEFAULTS["grim_enabled"], True)
        self.assertEqual(options_config.PACKAGED_DEFAULTS["grim_event_log_max_entries"], 500)

    def test_configure_anti_xray_preserves_existing_lists(self):
        if yaml is None:
            self.skipTest("PyYAML is not installed in this local Python")
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            config = paths["data"] / "config" / "paper-world-defaults.yml"
            config.parent.mkdir(parents=True)
            config.write_text(
                "anticheat:\n  anti-xray:\n    enabled: false\n    engine-mode: 1\n    hidden-blocks:\n      - custom_ore\n",
                encoding="utf-8",
            )
            result = security.configure_anti_xray(base_options(), paths)
            data = yaml.safe_load(config.read_text(encoding="utf-8"))
            self.assertTrue(result["configured"])
            self.assertTrue(data["anticheat"]["anti-xray"]["enabled"])
            self.assertEqual(data["anticheat"]["anti-xray"]["engine-mode"], 2)
            self.assertEqual(data["anticheat"]["anti-xray"]["hidden-blocks"], ["custom_ore"])
            self.assertTrue(list(paths["backups"].glob("paper-world-defaults-*.yml")))

    def test_anti_xray_uses_active_world_slot_and_does_not_create_missing_nether_override(self):
        if yaml is None:
            self.skipTest("PyYAML is not installed in this local Python")
        with tempfile.TemporaryDirectory() as tmp:
            options = base_options()
            options.update({"world_slot": "WORLD_2", "world_2_name": "Locotopia", "level_name": "chaos"})
            paths = security.data_paths(tmp)
            result = security.configure_anti_xray(options, paths)
            self.assertEqual(result["active_world"], "Locotopia")
            self.assertIn("config/paper-world-defaults.yml", result["config_files"])
            self.assertNotIn("chaos/dimensions/minecraft/the_nether/paper-world.yml", result["config_files"])
            self.assertFalse((paths["data"] / "Locotopia" / "dimensions" / "minecraft" / "the_nether" / "paper-world.yml").exists())

    def test_invalid_anti_xray_mode_falls_back(self):
        if yaml is None:
            self.skipTest("PyYAML is not installed in this local Python")
        with tempfile.TemporaryDirectory() as tmp:
            options = base_options()
            options["anti_xray_engine_mode"] = 99
            result = security.configure_anti_xray(options, security.data_paths(tmp))
            self.assertEqual(result["engine_mode"], 2)
            self.assertTrue(result["warnings"])

    def test_grim_disabled_moves_active_jar_without_deleting_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            paths["plugins"].mkdir(parents=True)
            jar = paths["plugins"] / "GrimAC.jar"
            jar.write_text("existing", encoding="utf-8")
            config = paths["plugins"] / "GrimAC" / "punishments.yml"
            config.parent.mkdir()
            config.write_text("Punishments: {}\n", encoding="utf-8")
            options = base_options()
            options["security_enabled"] = False
            result = security.prepare_grim(options, paths)
            self.assertFalse((paths["plugins"] / "GrimAC.jar").exists())
            self.assertTrue((paths["plugins"] / "GrimAC.jar.disabled").is_file())
            self.assertTrue(config.is_file())
            self.assertFalse(result["enabled_on_disk"])

    def test_grim_runtime_errors_override_enabling_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            latest = paths["data"] / "logs" / "latest.log"
            latest.parent.mkdir(parents=True)
            latest.write_text(
                "\n".join(
                    [
                        "[14:25:06 INFO]: [PluginInitializerManager] Bukkit plugins (1):",
                        " - GrimAC (2.3.73)",
                        "[14:25:13 INFO]: [GrimAC] Loading server plugin GrimAC v2.3.73",
                        "[14:25:13 WARN]: [GrimAC] Your server software is preventing us from checking the Minecraft Server version. This is what we found: 26.2.build.2609-stable. We will assume the Server version is V_1_8_8...",
                        "[14:25:13 ERROR]: [GrimAC] Failed to load PacketEventsInit: boom",
                        "[14:25:15 INFO]: [GrimAC] Enabling GrimAC v2.3.73",
                        "[14:25:15 ERROR]: [GrimAC] Failed to start PacketManager: boom",
                        "[14:25:15 ERROR]: [GrimAC] Failed to register commands! Grim will run without command support.",
                        "[14:25:16 INFO]: New GrimAC version found! Version 2.3.74-6c699c2 is available to be downloaded here: https://modrinth.com/plugin/grimac",
                    ]
                ),
                encoding="utf-8",
            )
            runtime = security.parse_grim_runtime_status(paths)
            self.assertTrue(runtime["plugin_discovered"])
            self.assertFalse(runtime["loaded"])
            self.assertFalse(runtime["enabled"])
            self.assertFalse(runtime["packet_manager_active"])
            self.assertFalse(runtime["commands_registered"])
            self.assertFalse(runtime["healthy"])
            self.assertEqual(runtime["runtime_detection"], "failed")
            self.assertEqual(runtime["detected_server_version_by_grim"], "V_1_8_8 fallback")
            self.assertEqual(runtime["grim_reported_update"]["version"], "2.3.74-6c699c2")

    def test_grim_log_event_parser_ignores_startup_noise(self):
        self.assertIsNone(
            security.parse_grim_violation_line(
                "[16:10:36] [Server thread/WARN]: [GrimAC] Deprecated Grim command-execute string listener registered; use the Supplier<String> verbose handler"
            )
        )
        self.assertIsNone(
            security.parse_grim_violation_line(
                "[16:10:10] [Server thread/ERROR]: [GrimAC] Failed to register commands! Grim will run without command support."
            )
        )

    def test_grim_log_event_parser_accepts_violation_with_vl(self):
        parsed = security.parse_grim_violation_line(
            "[16:20:00] [Server thread/INFO]: [GrimAC] NachoLoco failed Timer VL: 8 details"
        )
        self.assertEqual(parsed["player"], "NachoLoco")
        self.assertEqual(parsed["check"], "Timer")
        self.assertEqual(parsed["violation_level"], 8)

    def test_trim_events_removes_old_startup_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            security.append_event(
                paths,
                {
                    "player": "to",
                    "action": "detected",
                    "source": "logparser",
                    "raw": "[GrimAC] Failed to start PacketManager",
                },
            )
            security.append_event(
                paths,
                {
                    "player": "NachoLoco",
                    "check": "Timer",
                    "violation_level": 8,
                    "action": "detected",
                    "source": "logparser",
                    "raw": "[GrimAC] NachoLoco failed Timer VL: 8",
                },
            )
            security.trim_events(paths, 10)
            events = security.get_security_events(paths, 10)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["player"], "NachoLoco")

    def test_punishments_save_detects_external_change(self):
        if yaml is None:
            self.skipTest("PyYAML is not installed in this local Python")
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            path = paths["punishments"]
            path.parent.mkdir(parents=True)
            path.write_text("Punishments:\n  Movement:\n    commands:\n      - \"1:1 [alert]\"\n", encoding="utf-8")
            loaded = security.load_punishments(paths)
            path.write_text("Punishments: {}\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                security.save_punishments(paths, loaded["raw"], loaded["revision"]["sha256"])

    def test_missing_punishments_is_not_valid_until_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = security.punishments_status(base_options(), security.data_paths(tmp))
            self.assertFalse(status["file_exists"])
            self.assertFalse(status["initialized"])
            self.assertIsNone(status["valid"])

    def test_viaversion_fix_only_changes_target_key(self):
        if yaml is None:
            self.skipTest("PyYAML is not installed in this local Python")
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            config = paths["data"] / "plugins" / "ViaVersion" / "config.yml"
            config.parent.mkdir(parents=True)
            config.write_text("fix-1_21-placement-rotation: true\nother-setting: keep\n", encoding="utf-8")
            status = security.via_version_status(paths)
            self.assertTrue(status["placement_rotation_fix_enabled"])
            fixed = security.fix_viaversion_placement_rotation(paths)
            data = yaml.safe_load(config.read_text(encoding="utf-8"))
            self.assertFalse(data["fix-1_21-placement-rotation"])
            self.assertEqual(data["other-setting"], "keep")
            self.assertTrue(fixed["restart_required"])

    def test_xray_suspicion_scores_hidden_ore_and_alerts_without_rcon(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            options = base_options()
            options.update(
                {
                    "xray_suspicion_enabled": True,
                    "xray_suspicion_alert_threshold": 10,
                    "xray_suspicion_rcon_alerts": False,
                }
            )
            result = security.record_xray_observation(
                paths,
                options,
                {"player": "NachoLoco", "block": "diamond_ore", "visible": False, "source": "test"},
            )
            self.assertTrue(result["recorded"])
            result = security.record_xray_observation(
                paths,
                options,
                {"player": "NachoLoco", "block": "ancient_debris", "visible": False, "source": "test"},
            )
            self.assertGreaterEqual(result["score"], 10)
            self.assertIsNotNone(result["alert"])
            self.assertIn("Cheatverdacht", result["alert"]["command"])
            status = security.xray_suspicion_status(options, paths)
            self.assertEqual(status["top_players"][0]["player"], "NachoLoco")
            self.assertTrue(status["top_players"][0]["alerting"])

    def test_xray_bridge_log_observations_are_collected(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            log = paths["data"] / "logs" / "latest.log"
            log.parent.mkdir(parents=True)
            log.write_text(
                '[HAMinecraftBridge] {"type":"block_break","player":"Miner","block":"minecraft:deepslate_diamond_ore","visible":false}\n',
                encoding="utf-8",
            )
            options = base_options()
            options["xray_suspicion_rcon_alerts"] = False
            added = security.collect_xray_bridge_events(paths, options)
            self.assertEqual(added, 1)
            status = security.xray_suspicion_status(options, paths)
            self.assertEqual(status["top_players"][0]["player"], "Miner")

    def test_xray_stats_polling_uses_baseline_then_scores_deltas(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            options = base_options()
            options.update(
                {
                    "world_slot": "WORLD_2",
                    "world_2_name": "Locotopia",
                    "xray_suspicion_alert_threshold": 10,
                    "xray_suspicion_rcon_alerts": False,
                }
            )
            stats_dir = paths["data"] / "Locotopia" / "stats"
            stats_dir.mkdir(parents=True)
            uuid = "472ff97b-2cd3-4e38-96f8-c736f7e31e7b"
            (paths["data"] / "usercache.json").write_text(
                '[{"uuid":"472ff97b-2cd3-4e38-96f8-c736f7e31e7b","name":"NachoLoco"}]',
                encoding="utf-8",
            )
            stat_file = stats_dir / f"{uuid}.json"
            stat_file.write_text(
                '{"stats":{"minecraft:mined":{"minecraft:diamond_ore":1,"minecraft:ancient_debris":0}}}',
                encoding="utf-8",
            )
            self.assertEqual(security.collect_xray_stats_events(paths, options), 0)
            stat_file.write_text(
                '{"stats":{"minecraft:mined":{"minecraft:diamond_ore":2,"minecraft:ancient_debris":1}}}',
                encoding="utf-8",
            )
            self.assertEqual(security.collect_xray_stats_events(paths, options), 2)
            status = security.xray_suspicion_status(options, paths)
            self.assertEqual(status["top_players"][0]["player"], "NachoLoco")
            self.assertGreaterEqual(status["top_players"][0]["score"], 10)
            self.assertTrue(status["stats_polling"])
            self.assertEqual(status["last_stats_files_scanned"], 1)
            self.assertEqual(status["last_stats_tracked_total"], 3)

    def test_xray_stats_polling_supports_mine_block_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            options = base_options()
            options["xray_suspicion_rcon_alerts"] = False
            stats_dir = paths["data"] / "world" / "stats"
            stats_dir.mkdir(parents=True)
            stat_file = stats_dir / "abc123.json"
            stat_file.write_text(
                '{"stats":{"minecraft:mine_block":{"minecraft:diamond_ore":0}}}',
                encoding="utf-8",
            )
            self.assertEqual(security.collect_xray_stats_events(paths, options), 0)
            stat_file.write_text(
                '{"stats":{"minecraft:mine_block":{"minecraft:diamond_ore":1}}}',
                encoding="utf-8",
            )
            self.assertEqual(security.collect_xray_stats_events(paths, options), 1)

    def test_xray_stats_polling_ignores_other_world_stats_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            options = base_options()
            options.update({"level_name": "Locotopia", "xray_suspicion_rcon_alerts": False})
            stats_dir = paths["data"] / "world" / "stats"
            stats_dir.mkdir(parents=True)
            stat_file = stats_dir / "abc123.json"
            stat_file.write_text(
                '{"stats":{"minecraft:mined":{"minecraft:diamond_ore":1}}}',
                encoding="utf-8",
            )
            self.assertEqual(security.collect_xray_stats_events(paths, options), 0)
            stat_file.write_text(
                '{"stats":{"minecraft:mined":{"minecraft:diamond_ore":2}}}',
                encoding="utf-8",
            )
            self.assertEqual(security.collect_xray_stats_events(paths, options), 0)
            status = security.xray_suspicion_status(options, paths)
            self.assertFalse(any(path.endswith("world\\stats") or path.endswith("world/stats") for path in status["stats_dirs"]))
            self.assertIsNone(status["last_stats_scan"])

    def test_xray_stats_polling_supports_players_stats_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            options = base_options()
            options.update({"level_name": "Locotopia", "xray_suspicion_rcon_alerts": False})
            stats_dir = paths["data"] / "Locotopia" / "players" / "stats"
            stats_dir.mkdir(parents=True)
            stat_file = stats_dir / "472ff97b-2cd3-4e38-96f8-c736f7e31e7b.json"
            stat_file.write_text(
                '{"stats":{"minecraft:mined":{"minecraft:diamond_ore":2,"minecraft:gold_ore":1}}}',
                encoding="utf-8",
            )
            self.assertEqual(security.collect_xray_stats_events(paths, options), 0)
            status = security.xray_suspicion_status(options, paths)
            self.assertEqual(status["last_stats_files_scanned"], 1)
            self.assertEqual(status["last_stats_tracked_total"], 3)
            self.assertEqual(status["baseline_players"][0]["tracked_total"], 3)
            stat_file.write_text(
                '{"stats":{"minecraft:mined":{"minecraft:diamond_ore":3,"minecraft:gold_ore":1}}}',
                encoding="utf-8",
            )
            self.assertEqual(security.collect_xray_stats_events(paths, options), 1)

    def test_xray_rules_can_disable_material_and_change_weight(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            options = base_options()
            security.save_xray_rules(
                paths,
                {
                    "window_minutes": 20,
                    "alert_threshold": 50,
                    "blocks": {
                        "minecraft:diamond_ore": {"enabled": True, "weight": 20},
                        "minecraft:gold_ore": {"enabled": False, "weight": 50},
                    },
                },
                options,
            )
            gold = security.record_xray_observation(paths, options, {"player": "Miner", "block": "gold_ore"})
            self.assertFalse(gold["recorded"])
            diamond = security.record_xray_observation(paths, options, {"player": "Miner", "block": "diamond_ore"})
            self.assertEqual(diamond["score"], 20)

    def test_xray_score_decays_on_status_poll(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            options = base_options()
            now = 1_000_000
            security.record_xray_observation(
                paths,
                options,
                {"player": "Miner", "block": "diamond_ore", "timestamp_epoch": now - 3600},
            )
            status = security.xray_suspicion_status(options, paths)
            self.assertEqual(status["tracked_players"], 0)

    def test_xray_player_reset_keeps_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            options = base_options()
            security.record_xray_observation(paths, options, {"player": "Miner", "block": "diamond_ore"})
            state = security.read_xray_state(paths)
            state["stat_snapshots"] = {"world/stats/abc.json": {"minecraft:diamond_ore": 2}}
            security.write_xray_state(paths, state)
            security.reset_xray_player(paths, "Miner")
            state = security.read_xray_state(paths)
            self.assertNotIn("Miner", state.get("players", {}))
            self.assertIn("world/stats/abc.json", state.get("stat_snapshots", {}))

    def test_xray_status_shows_baseline_when_no_observations_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            options = base_options()
            stats_dir = paths["data"] / "world" / "stats"
            stats_dir.mkdir(parents=True)
            stat_file = stats_dir / "abc123.json"
            stat_file.write_text(
                '{"stats":{"minecraft:mined":{"minecraft:diamond_ore":2,"minecraft:gold_ore":1}}}',
                encoding="utf-8",
            )
            self.assertEqual(security.collect_xray_stats_events(paths, options), 0)
            status = security.xray_suspicion_status(options, paths)
            self.assertEqual(status["tracked_players"], 0)
            self.assertEqual(status["baseline_players"][0]["diamond_ore"], 2)
            self.assertEqual(status["baseline_players"][0]["gold_ore"], 1)

    def test_xray_stats_polling_records_scan_even_without_tracked_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            options = base_options()
            stats_dir = paths["data"] / "world" / "stats"
            stats_dir.mkdir(parents=True)
            (stats_dir / "abc123.json").write_text('{"stats":{"minecraft:custom":{"x":1}}}', encoding="utf-8")
            self.assertEqual(security.collect_xray_stats_events(paths, options), 0)
            status = security.xray_suspicion_status(options, paths)
            self.assertTrue(status["stats_polling"])
            self.assertIsNotNone(status["last_stats_scan"])
            self.assertEqual(status["last_stats_files_scanned"], 1)
            self.assertEqual(status["last_stats_tracked_total"], 0)

    def test_xray_runtime_summary_changes_when_stats_polling_state_changes(self):
        payload = {
            "xray_suspicion": {
                "enabled": True,
                "stats_polling": True,
                "active_world": "world",
                "last_stats_scan": "2026-07-18T12:00:00Z",
                "last_stats_files_scanned": 1,
                "last_stats_tracked_total": 3,
                "tracked_players": 1,
                "baseline_players": [{"player": "Miner"}],
            }
        }
        summary = security.xray_runtime_summary(payload)
        self.assertIn("Xray-Stats: aktiv", summary)
        self.assertIn("Erzzaehler=3", summary)
        self.assertIn("Pfade=", summary)

    def test_import_current_xray_stats_scores_existing_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = security.data_paths(tmp)
            options = base_options()
            options.update({"xray_suspicion_alert_threshold": 30, "xray_suspicion_rcon_alerts": False})
            stats_dir = paths["data"] / "world" / "stats"
            stats_dir.mkdir(parents=True)
            stat_file = stats_dir / "abc123.json"
            stat_file.write_text(
                '{"stats":{"minecraft:mined":{"minecraft:diamond_ore":2,"minecraft:gold_ore":1}}}',
                encoding="utf-8",
            )
            result = security.import_current_xray_stats(paths, options)
            self.assertEqual(result["imported"], 3)
            status = security.xray_suspicion_status(options, paths)
            self.assertEqual(status["top_players"][0]["ore_hits"], 3)

    def test_xray_alert_command_sanitizes_player_and_block(self):
        command = security.build_xray_alert_command("Bad;Name", 80, "minecraft:diamond_ore")
        self.assertNotIn("Bad;Name", command)
        self.assertIn("Cheatverdacht", command)

    def test_valid_grim_jar_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            jar = Path(tmp) / "GrimAC.jar"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr("plugin.yml", "name: GrimAC\nversion: 1.0\n")
                archive.writestr("payload.bin", b"x" * security.MIN_GRIM_JAR_BYTES)
            self.assertTrue(security.is_grim_jar(jar))


if __name__ == "__main__":
    unittest.main()
