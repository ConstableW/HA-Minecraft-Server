#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
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


bedrock = load_module("bedrock")
player_model = load_module("player_model")
options_config = load_module("options_config")


class BedrockTests(unittest.TestCase):
    def test_defaults_include_bedrock_disabled(self):
        self.assertIs(options_config.PACKAGED_DEFAULTS["bedrock_enabled"], False)
        self.assertEqual(options_config.PACKAGED_DEFAULTS["bedrock_port"], 19132)

    def test_supervisor_bedrock_option_overrides_existing_persistent_value(self):
        supervisor = options_config.PACKAGED_DEFAULTS.copy()
        supervisor.update({"eula": True, "bedrock_enabled": True, "bedrock_port": 19133})
        persistent = options_config.PACKAGED_DEFAULTS.copy()
        persistent.update({"eula": True, "bedrock_enabled": False, "bedrock_port": 19132})
        effective = options_config.merge_supervisor_overrides(supervisor, persistent)
        self.assertIs(effective["bedrock_enabled"], True)
        self.assertEqual(effective["bedrock_port"], 19133)

    def test_udp_port_validation(self):
        self.assertTrue(bedrock.valid_udp_port(19132))
        self.assertFalse(bedrock.valid_udp_port(0))
        self.assertFalse(bedrock.valid_udp_port(70000))
        self.assertFalse(bedrock.valid_udp_port("bad"))

    def test_configure_geyser_current_java_format_preserves_unknowns(self):
        if yaml is None:
            self.skipTest("PyYAML is not installed in this local Python")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yml"
            path.write_text(
                "bedrock:\n  address: 127.0.0.1\n  port: 19133\njava:\n  auth-type: online\ncustom-value: keep-me\n",
                encoding="utf-8",
            )
            changed = bedrock.configure_geyser_config(path, 19132)
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertTrue(changed)
            self.assertEqual(data["bedrock"]["address"], "0.0.0.0")
            self.assertEqual(data["bedrock"]["port"], 19132)
            self.assertEqual(data["java"]["auth-type"], "floodgate")
            self.assertEqual(data["custom-value"], "keep-me")
            self.assertTrue(path.with_name("config.yml.bak").is_file())

    def test_configure_geyser_old_remote_format(self):
        if yaml is None:
            self.skipTest("PyYAML is not installed in this local Python")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yml"
            path.write_text("remote:\n  auth-type: online\n", encoding="utf-8")
            bedrock.configure_geyser_config(path, 19140)
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertNotIn("java", data)
            self.assertEqual(data["remote"]["auth-type"], "floodgate")
            self.assertEqual(data["bedrock"]["port"], 19140)

    def test_disable_moves_only_bedrock_jars(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            plugins = data / "plugins"
            plugins.mkdir()
            (plugins / "Geyser-Spigot.jar").write_text("geyser", encoding="utf-8")
            (plugins / "floodgate-spigot.jar").write_text("floodgate", encoding="utf-8")
            (plugins / "LuckPerms.jar").write_text("luckperms", encoding="utf-8")
            bedrock.disable(data)
            self.assertTrue((data / "plugins-disabled" / "Geyser-Spigot.jar").is_file())
            self.assertTrue((data / "plugins-disabled" / "floodgate-spigot.jar").is_file())
            self.assertTrue((plugins / "LuckPerms.jar").is_file())

    def test_bedrock_name_deduplication(self):
        log = "\n".join(
            [
                "[Geyser-Spigot] Player connected with username Constable W1949 (1001)",
                "[Geyser-Spigot] Constable W1949 (logged in as: .Constable_W1949) has connected to the Java server",
                "[floodgate] Floodgate player logged in as .Constable_W1949 joined",
            ]
        )
        mappings = player_model.bedrock_mappings_from_log(log)
        model = player_model.build_player_model(
            ["ConstableWatson", "FieryMonkey4174", ".Constable_W1949"],
            mappings,
        )
        self.assertEqual(model["players_online"], 3)
        self.assertEqual(model["players_java"], 2)
        self.assertEqual(model["players_bedrock"], 1)
        bedrock_player = [player for player in model["players"] if player["platform"] == "bedrock"][0]
        self.assertEqual(bedrock_player["display_name"], "Constable W1949")
        self.assertEqual(bedrock_player["server_name"], ".Constable_W1949")


if __name__ == "__main__":
    unittest.main()
