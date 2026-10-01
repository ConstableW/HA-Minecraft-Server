#!/usr/bin/env python3
import importlib.util
import json
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "minecraft-java" / "rootfs" / "usr" / "local" / "lib" / "ha-minecraft"


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, LIB / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


updater = load_module("updater")

# Keep synthetic artifacts small; the real thresholds are exercised in the add-on image.
updater.MIN_SERVER_JAR_BYTES = 100
updater.MIN_PLUGIN_JAR_BYTES = 60


def make_jar(path, main_class="io.papermc.paperclip.Main", plugin_name="", payload=b"payload" * 16):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as jar:
        manifest = "Manifest-Version: 1.0\n"
        if main_class:
            manifest += f"Main-Class: {main_class}\n"
        jar.writestr("META-INF/MANIFEST.MF", manifest)
        if plugin_name:
            jar.writestr("plugin.yml", f"name: {plugin_name}\nmain: example.Main\nversion: '1.0'\n")
        jar.writestr("example/payload.bin", payload)
    return path


def purpur_fetcher(versions, builds, sha256="a" * 64):
    def fetcher(url, timeout=20):
        if url == "https://api.purpurmc.org/v2/purpur":
            return {"project": "purpur", "versions": versions}
        match = re.fullmatch(r"https://api\.purpurmc\.org/v2/purpur/([^/]+)", url)
        if match:
            version = match.group(1)
            available = [str(item) for item in builds.get(version, [])]
            return {"project": "purpur", "version": version, "builds": {"latest": available[-1] if available else None, "all": available}}
        match = re.fullmatch(r"https://api\.purpurmc\.org/v2/purpur/([^/]+)/(\d+)", url)
        if match:
            return {"project": "purpur", "version": match.group(1), "build": match.group(2), "md5": "d41d8cd98f00b204e9800998ecf8427e", "sha256": sha256}
        raise AssertionError(f"unexpected URL {url}")

    return fetcher


def paper_fetcher(version, builds):
    payload = []
    for build in builds:
        payload.append(
            {
                "id": int(build["build"]),
                "time": "2026-01-01T00:00:00.000Z",
                "channel": build["channel"],
                "downloads": {
                    "server:default": {
                        "name": f"paper-{version}-{build['build']}.jar",
                        "checksums": {"sha256": build["sha256"]},
                        "size": 50_000_000,
                        "url": f"https://fill-data.papermc.io/v1/objects/{build['sha256']}/paper-{version}-{build['build']}.jar",
                    }
                },
            }
        )

    def fetcher(url, timeout=20):
        if url == "https://fill.papermc.io/v3/projects/paper":
            return {"project": {"id": "paper"}, "versions": {version: [version]}}
        if url.endswith(f"/versions/{version}/builds"):
            return payload
        raise AssertionError(f"unexpected URL {url}")

    return fetcher


def geysermc_fetcher(shas):
    def fetcher(url, timeout=20):
        match = re.fullmatch(r"https://download\.geysermc\.org/v2/projects/([a-z]+)/versions/latest/builds/latest", url)
        if not match:
            raise AssertionError(f"unexpected URL {url}")
        project = match.group(1)
        return {
            "project_id": project,
            "version": shas[project]["version"],
            "build": shas[project]["build"],
            "channel": "default",
            "downloads": {"spigot": {"name": f"{project}-spigot.jar", "sha256": shas[project]["sha256"]}},
        }

    return fetcher


class VersionTests(unittest.TestCase):
    def test_version_comparison(self):
        self.assertTrue(updater.version_newer("232", "231"))
        self.assertTrue(updater.version_newer("26.3", "26.2"))
        self.assertTrue(updater.version_newer("1.21.4", "1.21.3"))
        self.assertTrue(updater.version_newer("26.3-rc-3", "26.3"))
        self.assertFalse(updater.version_newer("26.2", "26.3"))
        self.assertFalse(updater.version_newer("231", "231"))

    def test_server_jar_name_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_jar(Path(tmp) / "purpur-26.2-100.jar")
            info = updater.describe_server_jar(path)
            self.assertEqual(info["version"], "26.2")
            self.assertEqual(info["build"], "100")
            self.assertEqual(info["type"], "PURPUR")

    def test_newest_jar_wins_over_leftover_older_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp)
            make_jar(server / "purpur-26.2-100.jar")
            newest = make_jar(server / "purpur-26.2-120.jar")
            self.assertEqual(updater.find_server_jar(server, "PURPUR").name, newest.name)


class ValidationTests(unittest.TestCase):
    def test_valid_server_jar_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            updater.validate_server_jar(make_jar(Path(tmp) / "purpur-1.0-1.jar"))

    def test_plain_text_download_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "purpur-1.0-1.jar"
            path.write_text("<html>rate limited</html>" * 20, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                updater.validate_server_jar(path)

    def test_server_jar_without_main_class_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_jar(Path(tmp) / "purpur-1.0-1.jar", main_class="")
            with self.assertRaises(RuntimeError):
                updater.validate_server_jar(path)

    def test_truncated_jar_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_jar(Path(tmp) / "purpur-1.0-1.jar")
            data = path.read_bytes()
            path.write_bytes(data[: len(data) // 2])
            with self.assertRaises(RuntimeError):
                updater.validate_server_jar(path)

    def test_plugin_name_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = make_jar(Path(tmp) / "floodgate-spigot.jar", main_class="", plugin_name="floodgate")
            updater.validate_plugin_jar(good, ("floodgate",))
            wrong = make_jar(Path(tmp) / "floodgate-spigot.jar", main_class="", plugin_name="SomeOtherPlugin")
            with self.assertRaises(RuntimeError):
                updater.validate_plugin_jar(wrong, ("floodgate",))


class DownloadTests(unittest.TestCase):
    def test_sha256_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = make_jar(Path(tmp) / "source.jar")
            digest = updater.sha256_file(source)
            target = Path(tmp) / "target.jar"
            result = updater.download_verified(source.as_uri(), target, sha256=digest, min_bytes=1)
            self.assertTrue(target.is_file())
            self.assertEqual(result["sha256"], digest)
            self.assertTrue(result["verified"])

    def test_mismatch_leaves_no_file_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = make_jar(Path(tmp) / "source.jar")
            target = Path(tmp) / "target.jar"
            with self.assertRaises(RuntimeError):
                updater.download_verified(source.as_uri(), target, sha256="0" * 64, min_bytes=1)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_name(".target.jar.download").exists())

    def test_size_floor_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = make_jar(Path(tmp) / "source.jar")
            with self.assertRaises(RuntimeError):
                updater.download_verified(source.as_uri(), Path(tmp) / "target.jar", min_bytes=10_000_000)


class LogAnalysisTests(unittest.TestCase):
    def test_reported_floodgate_regression_is_detected(self):
        log = "\n".join(
            [
                '[19:51:19 WARN]: [io.netty.channel.DefaultChannelPipeline] An exceptionCaught() event was fired',
                "java.lang.IllegalStateException: The plugin classloader for floodgate has thrown a zip file error.",
                "\tat org.bukkit.plugin.java.PluginClassLoader.findClass(PluginClassLoader.java:228)",
                "Caused by: java.lang.IllegalStateException: zip file closed",
                "\tat java.base/java.util.zip.ZipFile.ensureOpen(Unknown Source)",
            ]
        )
        analysis = updater.analyze_log(log)
        self.assertFalse(analysis["started"])
        self.assertGreaterEqual(analysis["failure_count"], 1)
        self.assertEqual(analysis["first_failure"]["signature"], "plugin_classloader_zip_error")

    def test_controller_noise_is_not_health_evidence(self):
        log = "\n".join(
            [
                '[plugin-browser] 172.30.32.2 "GET /?tab=plugins HTTP/1.1" 200 -',
                "[plugin-browser] 172.30.32.2 could not render page: ERROR",
                "[backup] Error while rotating old backups",
                "Done (13.421s)! For help, type \"help\"",
            ]
        )
        analysis = updater.analyze_log(log)
        self.assertTrue(analysis["started"])
        self.assertEqual(analysis["failure_count"], 0)
        self.assertEqual(analysis["spam"], 0)

    def test_spam_pressure_is_counted(self):
        log = "\n".join(["java.lang.IllegalStateException: boom", "\tat example.Main(Main.java:12)"] * 50)
        analysis = updater.analyze_log(log)
        self.assertGreater(analysis["spam"], 50)


class PlanningTests(unittest.TestCase):
    def base_options(self, **overrides):
        options = {"server_type": "PURPUR", "minecraft_version": "26.2", "update_mode": "safe", "bedrock_enabled": True}
        options.update(overrides)
        return options

    def fixture(self, builds, versions=None, installed=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        data = root / "media"
        server = root / "data"
        (data / "plugins").mkdir(parents=True)
        server.mkdir(parents=True)
        if installed:
            make_jar(server / installed)
        return data, server, purpur_fetcher(versions or ["26.2"], builds)

    def test_newer_build_of_same_version_is_offered(self):
        data, server, fetcher = self.fixture({"26.2": ["100", "120"]}, installed="purpur-26.2-100.jar")
        paths = updater.update_paths(data, server)
        state = updater.read_state(paths)
        plan = updater.plan_server(self.base_options(), paths, state, fetcher=fetcher)
        self.assertTrue(plan["update_available"])
        self.assertIsNone(plan["blocked_reason"])
        self.assertEqual(plan["available"]["build"], "120")
        self.assertEqual(plan["pin"]["BUILD_VAR"], "PURPUR_BUILD")

    def test_current_build_is_not_reinstalled(self):
        data, server, fetcher = self.fixture({"26.2": ["100", "120"]}, installed="purpur-26.2-120.jar")
        paths = updater.update_paths(data, server)
        state = updater.read_state(paths)
        plan = updater.plan_server(self.base_options(), paths, state, fetcher=fetcher)
        self.assertFalse(plan["update_available"])

    def test_version_jump_is_blocked_by_default(self):
        data, server, fetcher = self.fixture({"26.3": ["2639"]}, versions=["26.3", "26.2"], installed="purpur-26.2-120.jar")
        paths = updater.update_paths(data, server)
        state = updater.read_state(paths)
        plan = updater.plan_server(self.base_options(minecraft_version="26.3"), paths, state, fetcher=fetcher)
        self.assertFalse(plan["update_available"])
        self.assertIn("version jump", plan["blocked_reason"])

    def test_version_jump_allowed_when_requested(self):
        data, server, fetcher = self.fixture({"26.3": ["2639"]}, versions=["26.3", "26.2"], installed="purpur-26.2-120.jar")
        paths = updater.update_paths(data, server)
        state = updater.read_state(paths)
        plan = updater.plan_server(
            self.base_options(minecraft_version="26.3", update_allow_version_jump=True), paths, state, fetcher=fetcher
        )
        self.assertTrue(plan["update_available"])

    def test_experimental_builds_are_not_selected_by_default(self):
        builds = [
            {"build": "2639", "channel": "EXPERIMENTAL", "sha256": "b" * 64},
            {"build": "2638", "channel": "STABLE", "sha256": "c" * 64},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "media"
            server = Path(tmp) / "data"
            (data / "plugins").mkdir(parents=True)
            server.mkdir(parents=True)
            make_jar(server / "paper-26.2-2630.jar")
            paths = updater.update_paths(data, server)
            state = updater.read_state(paths)
            fetcher = paper_fetcher("26.2", builds)
            options = {"server_type": "PAPER", "minecraft_version": "26.2", "update_mode": "safe"}
            plan = updater.plan_server(options, paths, state, fetcher=fetcher)
            self.assertEqual(plan["available"]["build"], "2638")
            options["update_allow_experimental"] = True
            plan = updater.plan_server(options, paths, state, fetcher=fetcher)
            self.assertEqual(plan["available"]["build"], "2639")
            self.assertEqual(plan["pin"]["BUILD_VAR"], "PAPER_BUILD")

    def test_implicit_jump_is_reported_for_latest(self):
        data, server, fetcher = self.fixture({"26.3": ["2639"]}, versions=["26.3", "26.2"], installed="purpur-26.2-120.jar")
        paths = updater.update_paths(data, server)
        state = updater.read_state(paths)
        finding = updater.detect_implicit_jump(self.base_options(minecraft_version="LATEST", update_allow_experimental=True), paths, state, fetcher=fetcher)
        self.assertIsNotNone(finding)
        self.assertEqual(finding["kind"], "minecraft_version")
        self.assertEqual(finding["installed_version"], "26.2")
        self.assertEqual(finding["target_version"], "26.3")

    def test_quarantined_plugin_build_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "media"
            server = Path(tmp) / "data"
            plugins = data / "plugins"
            plugins.mkdir(parents=True)
            server.mkdir(parents=True)
            make_jar(plugins / "floodgate-spigot.jar", main_class="", plugin_name="floodgate")
            paths = updater.update_paths(data, server)
            state = updater.read_state(paths)
            state["quarantine"] = {"floodgate": [{"sha256": "f" * 64, "reason": "previous failure"}]}
            fetcher = geysermc_fetcher({"floodgate": {"version": "2.2.5", "build": "141", "sha256": "f" * 64}})
            plans = updater.plan_plugins({"server_type": "PURPUR", "bedrock_enabled": True}, paths, state, fetcher=fetcher)
            self.assertFalse(plans["floodgate"]["update_available"])
            self.assertIn("quarantined", plans["floodgate"]["blocked_reason"])


class ApplyRollbackTests(unittest.TestCase):
    def fake_downloader(self, plugin_name=""):
        def downloader(url, target, sha256="", md5="", min_bytes=0, label="", timeout=120):
            path = make_jar(target, main_class="" if plugin_name else "io.papermc.paperclip.Main", plugin_name=plugin_name)
            return {"path": path, "sha256": updater.sha256_file(path), "size": path.stat().st_size, "verified": bool(sha256)}

        return downloader

    def server_plan(self, build="120", sha256="a" * 64):
        return {
            "artifact": "server",
            "type": "PURPUR",
            "available": {
                "source": "purpur",
                "version": "26.2",
                "build": build,
                "channel": "STABLE",
                "name": f"purpur-26.2-{build}.jar",
                "url": "https://api.purpurmc.org/v2/purpur/26.2/120/download",
                "sha256": sha256,
                "md5": "",
            },
            "update_available": True,
            "blocked_reason": None,
        }

    def test_server_update_archives_previous_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "media"
            server = Path(tmp) / "data"
            (data / "plugins").mkdir(parents=True)
            server.mkdir(parents=True)
            old = make_jar(server / "purpur-26.2-100.jar")
            old_digest = updater.sha256_file(old)
            paths = updater.update_paths(data, server)
            state = updater.read_state(paths)
            options = {"server_type": "PURPUR", "minecraft_version": "26.2", "update_mode": "safe"}

            result = updater.apply_server_update(options, paths, state, self.server_plan(), downloader=self.fake_downloader())
            self.assertTrue(result["changed"])
            new_jar = server / "purpur-26.2-120.jar"
            self.assertTrue(new_jar.is_file())
            self.assertFalse(old.exists())
            record = updater.artifact_state(state, "server")
            self.assertIsNotNone(record["previous"])
            self.assertTrue(Path(record["previous"]["archived_path"]).is_file())
            self.assertFalse(record["known_good"])
            self.assertEqual(record["build"], "120")

            updater.set_pending_update(state, ["server"], "apply")
            updater.write_state(paths, state)
            rollback = updater.rollback(paths, state, reason="post_update_health_check")
            self.assertTrue(rollback["changed"])
            self.assertTrue(old.is_file())
            self.assertFalse(new_jar.exists())
            self.assertEqual(updater.sha256_file(old), old_digest)
            self.assertEqual(state["state"], "rolled_back")
            self.assertIn("post_update_health_check", state["blocked"]["reason"])
            quarantined = updater.quarantined_hashes(state, "server")
            self.assertIn(updater.sha256_file(old), {old_digest} | quarantined)
            self.assertTrue(any(item["reason"] == "post_update_health_check" for item in state["quarantine"]["server"]))

    def test_quarantined_server_candidate_is_not_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "media"
            server = Path(tmp) / "data"
            (data / "plugins").mkdir(parents=True)
            server.mkdir(parents=True)
            make_jar(server / "purpur-26.2-100.jar")
            paths = updater.update_paths(data, server)
            state = updater.read_state(paths)
            state["quarantine"] = {"server": [{"sha256": "a" * 64, "reason": "bad build"}]}
            options = {"server_type": "PURPUR", "minecraft_version": "26.2", "update_mode": "safe"}
            result = updater.apply_server_update(options, paths, state, self.server_plan(), downloader=self.fake_downloader())
            self.assertFalse(result["changed"])
            self.assertIn("quarantined", result["reason"])
            self.assertFalse((server / "purpur-26.2-120.jar").exists())

    def test_plugin_update_normalises_name_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "media"
            server = Path(tmp) / "data"
            plugins = data / "plugins"
            plugins.mkdir(parents=True)
            server.mkdir(parents=True)
            old = make_jar(plugins / "floodgate-spigot-2.2.4.jar", main_class="", plugin_name="floodgate")
            old_digest = updater.sha256_file(old)
            paths = updater.update_paths(data, server)
            state = updater.read_state(paths)
            plans = {
                "floodgate": {
                    "artifact": "floodgate",
                    "requested": True,
                    "update_available": True,
                    "blocked_reason": None,
                    "available": {
                        "source": "geysermc",
                        "version": "2.2.5",
                        "build": "141",
                        "channel": "default",
                        "name": "floodgate-spigot.jar",
                        "url": "https://download.geysermc.org/v2/projects/floodgate/versions/latest/builds/latest/downloads/spigot",
                        "sha256": "e" * 64,
                    },
                }
            }
            options = {"server_type": "PURPUR", "bedrock_enabled": True, "update_mode": "safe"}
            results = updater.apply_plugin_updates(options, paths, state, plans, downloader=self.fake_downloader(plugin_name="floodgate"))
            self.assertTrue(results[0]["changed"])
            canonical = plugins / "floodgate-spigot.jar"
            self.assertTrue(canonical.is_file())
            self.assertFalse(old.exists())
            updater.set_pending_update(state, ["floodgate"], "apply")
            updater.write_state(paths, state)
            updater.rollback(paths, state, reason="failed start")
            restored = plugins / "floodgate-spigot-2.2.4.jar"
            self.assertTrue(restored.is_file())
            self.assertEqual(updater.sha256_file(restored), old_digest)
            self.assertFalse(canonical.exists())

    def test_notify_mode_never_touches_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "media"
            server = Path(tmp) / "data"
            (data / "plugins").mkdir(parents=True)
            server.mkdir(parents=True)
            old = make_jar(server / "purpur-26.2-100.jar")
            before = old.read_bytes()
            paths = updater.update_paths(data, server)
            state = updater.read_state(paths)
            options = {"server_type": "PURPUR", "minecraft_version": "26.2", "update_mode": "notify"}
            result = updater.apply_updates(options, paths, state, fetcher=purpur_fetcher(["26.2"], {"26.2": ["100", "120"]}), downloader=self.fake_downloader())
            self.assertEqual(result["mode"], "notify")
            self.assertEqual(result["applied"], [])
            self.assertEqual(old.read_bytes(), before)
            self.assertEqual(sorted(item.name for item in server.glob("*.jar")), ["purpur-26.2-100.jar"])
            self.assertIsNone(updater.pending_update(state))


class WatchdogTests(unittest.TestCase):
    def setup_paths(self, tmp, installed="purpur-26.2-100.jar"):
        data = Path(tmp) / "media"
        server = Path(tmp) / "data"
        (data / "plugins").mkdir(parents=True)
        (data / "logs").mkdir(parents=True)
        server.mkdir(parents=True)
        make_jar(server / installed)
        return updater.update_paths(data, server)

    def test_healthy_start_promotes_and_clears_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.setup_paths(tmp)
            state = updater.read_state(paths)
            updater.artifact_state(state, "server").update({"path": str(paths["server_dir"] / "purpur-26.2-120.jar"), "build": "120"})
            updater.set_pending_update(state, ["server"], "apply")
            updater.write_state(paths, state)
            log = paths["logs"] / "latest.log"
            log.write_text('Done (13.421s)! For help, type "help"\n', encoding="utf-8")
            pid_file = Path(tmp) / "server.pid"
            pid_file.write_text(str(os.getpid()), encoding="utf-8")
            verdict = updater.watchdog(
                paths, state, log, pid_file, Path(tmp) / "commands", seconds=1, spam_limit=1000, poll=0.05
            )
            self.assertEqual(verdict["state"], "healthy")
            self.assertIsNone(updater.pending_update(state))
            self.assertTrue(updater.artifact_state(state, "server")["known_good"])

    def test_broken_update_requests_abort_without_promoting(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.setup_paths(tmp)
            state = updater.read_state(paths)
            updater.set_pending_update(state, ["server"], "apply")
            updater.write_state(paths, state)
            log = paths["logs"] / "latest.log"
            log.write_text(
                "java.lang.IllegalStateException: The plugin classloader for floodgate has thrown a zip file error.\n"
                "Caused by: java.lang.IllegalStateException: zip file closed\n",
                encoding="utf-8",
            )
            pid_file = Path(tmp) / "server.pid"
            pid_file.write_text(str(os.getpid()), encoding="utf-8")
            command_dir = Path(tmp) / "commands"
            verdict = updater.watchdog(paths, state, log, pid_file, command_dir, seconds=1, spam_limit=1000, poll=0.05)
            self.assertEqual(verdict["state"], "failed")
            self.assertEqual(verdict["reason"], "update_failure_signature")
            self.assertIsNotNone(updater.pending_update(state))
            commands = list(command_dir.glob("*.json"))
            self.assertEqual(len(commands), 1)
            payload = json.loads(commands[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["action"], "abort_update")
            self.assertEqual(payload["reason"], "update_failure_signature")

    def test_spam_storm_aborts_even_after_a_successful_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.setup_paths(tmp)
            state = updater.read_state(paths)
            updater.set_pending_update(state, ["server"], "apply")
            updater.write_state(paths, state)
            log = paths["logs"] / "latest.log"
            lines = ['Done (13.421s)! For help, type "help"']
            lines.extend(["java.lang.IllegalStateException: every packet explodes"] * 20)
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            pid_file = Path(tmp) / "server.pid"
            pid_file.write_text(str(os.getpid()), encoding="utf-8")
            command_dir = Path(tmp) / "commands"
            verdict = updater.watchdog(paths, state, log, pid_file, command_dir, seconds=1, spam_limit=5, poll=0.05)
            self.assertEqual(verdict["state"], "failed")
            self.assertEqual(verdict["reason"], "error_spam_storm")


class EnvironmentTests(unittest.TestCase):
    def test_safe_mode_pins_version_and_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "media"
            server = Path(tmp) / "data"
            (data / "plugins").mkdir(parents=True)
            server.mkdir(parents=True)
            make_jar(server / "purpur-26.2-120.jar")
            paths = updater.update_paths(data, server)
            state = updater.read_state(paths)
            effective = updater.server_environment({"server_type": "PURPUR", "minecraft_version": "LATEST", "update_mode": "safe"}, paths, state)
            self.assertTrue(effective["pinned"])
            self.assertEqual(effective["env"]["VERSION"], "26.2")
            self.assertEqual(effective["env"]["PURPUR_BUILD"], "120")

    def test_notify_mode_pins_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "media"
            server = Path(tmp) / "data"
            (data / "plugins").mkdir(parents=True)
            server.mkdir(parents=True)
            make_jar(server / "purpur-26.2-120.jar")
            paths = updater.update_paths(data, server)
            state = updater.read_state(paths)
            effective = updater.server_environment({"server_type": "PURPUR", "minecraft_version": "LATEST", "update_mode": "notify"}, paths, state)
            self.assertFalse(effective["pinned"])
            self.assertEqual(effective["env"], {})

    def test_status_payload_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "media"
            server = Path(tmp) / "data"
            (data / "plugins").mkdir(parents=True)
            server.mkdir(parents=True)
            paths = updater.update_paths(data, server)
            state = updater.read_state(paths)
            payload = updater.status_payload({"server_type": "PURPUR", "update_mode": "safe"}, paths, state)
            for key in ("enabled", "mode", "state", "healthy", "pending_update", "blocked", "server", "plugins", "quarantine", "warnings"):
                self.assertIn(key, payload)
            self.assertEqual(payload["mode"], "safe")


if __name__ == "__main__":
    unittest.main()
