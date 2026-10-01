#!/usr/bin/env python3
"""Safe update engine for the Minecraft server jar and Geyser/Floodgate.

The engine never changes files implicitly:

* ``notify`` mode only reports what a restart would do.
* ``safe`` mode applies a verified update before the next start, keeps the
  replaced file for rollback, quarantines versions that failed, and asks the
  controller to abort a start whose watchdog detected a broken update.

Every download is verified against the sha256/md5 published by the vendor API
and against the jar structure before it is allowed to replace a working file.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

USER_AGENT = "ha-minecraft-addon/1.0.0 (https://github.com/local/minecraftserver-hassos)"
FILL_PROJECT = "https://fill.papermc.io/v3/projects/{project}"
FILL_BUILDS = "https://fill.papermc.io/v3/projects/{project}/versions/{version}/builds"
PURPUR_VERSION = "https://api.purpurmc.org/v2/purpur/{version}"
PURPUR_BUILD = "https://api.purpurmc.org/v2/purpur/{version}/{build}"
PURPUR_DOWNLOAD = "https://api.purpurmc.org/v2/purpur/{version}/{build}/download"
GEYSERMC_BUILD = "https://download.geysermc.org/v2/projects/{project}/versions/latest/builds/latest"
GEYSERMC_DOWNLOAD = "https://download.geysermc.org/v2/projects/{project}/versions/latest/builds/latest/downloads/{platform}"

SERVER_TYPES = ("PAPER", "PURPUR", "VANILLA")
PLUGIN_SERVER_TYPES = ("PAPER", "PURPUR")
UPDATE_MODES = ("off", "notify", "safe")

SERVER_JAR_NAMES = {
    "PAPER": r"^paper-(?P<version>.+)-(?P<build>\d+)\.jar$",
    "PURPUR": r"^purpur-(?P<version>.+)-(?P<build>\d+)\.jar$",
    "VANILLA": r"^(?:minecraft_server.*|server)\.jar$",
}
SERVER_JAR_GLOBS = {
    "PAPER": ("paper-*.jar", "server.jar"),
    "PURPUR": ("purpur-*.jar",),
    "VANILLA": ("minecraft_server*.jar", "server.jar"),
}

SERVER_ARTIFACT = "server"
PLUGIN_ARTIFACTS = {
    "geyser": {"label": "Geyser-Spigot", "project": "geyser", "filename": "Geyser-Spigot.jar", "names": ("geyser",)},
    "floodgate": {"label": "Floodgate", "project": "floodgate", "filename": "floodgate-spigot.jar", "names": ("floodgate",)},
}
PLUGIN_PATTERNS = {
    "geyser": (r"^Geyser(?:-Spigot)?(?:[-_.].*)?\.jar$",),
    "floodgate": (r"^floodgate(?:-spigot)?(?:[-_.].*)?\.jar$",),
}

MIN_SERVER_JAR_BYTES = 5_000_000
MIN_PLUGIN_JAR_BYTES = 50_000
MAX_ARTIFACT_BYTES = 600 * 1024 * 1024
DEFAULT_KEEP_ROLLBACKS = 3
DEFAULT_SPAM_LIMIT = 2000
DEFAULT_WATCHDOG_SECONDS = 300
WATCHDOG_POLL_SECONDS = 5
LOG_TAIL_BYTES = 600_000

START_DONE = re.compile(r"Done \([0-9.]+s\)! For help, type", re.IGNORECASE)
SERVER_JAR_PATTERN = re.compile(r"^(?P<kind>paper|purpur)-(?P<version>.+)-(?P<build>\d+)\.jar$", re.IGNORECASE)

# Controller and sidecar log lines are not server-health evidence.
NOISE_PREFIXES = (
    "[plugin-browser]",
    "[mqtt",
    "[backup]",
    "[autopause]",
    "[playit]",
    "[security]",
    "[bedrock]",
    "[updates]",
    "[update]",
    "[control]",
    "[init]",
    "[shutdown]",
    "[sync]",
    "[rcon]",
    "[error]",
)

# Failure evidence that must abort an update. The first two entries are the
# signatures observed when a server jar update broke Floodgate's classloader.
FAILURE_SIGNATURES = (
    ("plugin_classloader_zip_error", re.compile(r"plugin classloader for \S+ has thrown a zip file error", re.IGNORECASE)),
    ("zip_file_closed", re.compile(r"zip file closed", re.IGNORECASE)),
    ("plugin_classloader_error", re.compile(r"plugin classloader for \S+ has thrown", re.IGNORECASE)),
    ("unsupported_class_version", re.compile(r"UnsupportedClassVersionError")),
    ("incompatible_class_change", re.compile(r"IncompatibleClassChangeError")),
    ("no_class_def_found", re.compile(r"NoClassDefFoundError")),
    ("no_such_method", re.compile(r"NoSuchMethodError")),
    ("abstract_method", re.compile(r"AbstractMethodError")),
    ("plugin_load_failure", re.compile(r"Could not load ['\"]?plugins[/\\]|Failed to load plugin|Error occurred while enabling", re.IGNORECASE)),
    ("plugin_disable_failure", re.compile(r"Error occurred while disabling", re.IGNORECASE)),
    ("jvm_crash", re.compile(r"A fatal error has been detected by the Java Runtime Environment")),
    ("out_of_memory", re.compile(r"java\.lang\.OutOfMemoryError")),
    ("main_exception", re.compile(r'Exception in thread "main"')),
    ("startup_failed", re.compile(r"Failed to start the minecraft server|Failed to start Minecraft", re.IGNORECASE)),
)

SPAM_LINE = re.compile(r"\b(?:ERROR|SEVERE|FATAL)\b|Exception|Error:|\.java:\d+\)")


def log(message):
    print(f"[updates] {message}", flush=True)


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_stamp():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


# --------------------------------------------------------------------------
# options and paths
# --------------------------------------------------------------------------


def read_options_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def bool_option(options, key, default=False):
    value = options.get(key, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def int_option(options, key, default, minimum=None, maximum=None):
    try:
        value = int(options.get(key, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def option_text(options, key, default=""):
    value = options.get(key, default)
    return str(value if value is not None else default).strip()


def update_mode(options):
    mode = option_text(options, "update_mode", "notify").lower()
    return mode if mode in UPDATE_MODES else "notify"


def server_type(options):
    value = option_text(options, "server_type", "PAPER").upper()
    return value if value in SERVER_TYPES else "PAPER"


def update_paths(data_dir, server_dir="/data"):
    root = Path(data_dir).resolve()
    updates = root / "updates"
    server_root = Path(server_dir).resolve()
    server_workspace = server_root / ".ha-minecraft-updates"
    return {
        "data": root,
        "plugins": root / "plugins",
        "server_dir": server_root,
        "updates": updates,
        "state_file": updates / "update-state.json",
        "staging": updates / "staging",
        "archive": updates / "archive",
        "quarantine": updates / "quarantine",
        "server_workspace": server_workspace,
        "server_staging": server_workspace / "staging",
        "server_archive": server_workspace / "archive",
        "logs": root / "logs",
    }


def artifact_workspace(paths, artifact_id):
    """Staging and archive must share a filesystem with the target file."""
    if artifact_id == SERVER_ARTIFACT:
        return paths["server_staging"], paths["server_archive"]
    return paths["staging"], paths["archive"]


def ensure_workspace(paths, artifact_id):
    staging, archive = artifact_workspace(paths, artifact_id)
    for directory in (staging, archive, paths["updates"], paths["quarantine"]):
        directory.mkdir(parents=True, exist_ok=True)
    return staging, archive


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_state(paths):
    try:
        data = json.loads(paths["state_file"].read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(paths, state):
    state["updated_at"] = iso_now()
    atomic_write_json(paths["state_file"], state)
    return state


def artifact_state(state, artifact_id):
    artifacts = state.setdefault("artifacts", {})
    entry = artifacts.get(artifact_id)
    if not isinstance(entry, dict):
        entry = {}
        artifacts[artifact_id] = entry
    return entry


def quarantined_hashes(state, artifact_id):
    entries = (state.get("quarantine") or {}).get(artifact_id) or []
    return {str(item.get("sha256") or "").lower() for item in entries if item.get("sha256")}


def add_quarantine(paths, state, artifact_id, record):
    store = state.setdefault("quarantine", {}).setdefault(artifact_id, [])
    digest = str(record.get("sha256") or "").lower()
    entry = {
        "sha256": digest,
        "version": record.get("version"),
        "build": record.get("build"),
        "channel": record.get("channel"),
        "reason": record.get("reason"),
        "at": iso_now(),
    }
    store[:] = [item for item in store if str(item.get("sha256") or "").lower() != digest]
    store.insert(0, entry)
    del store[10:]
    paths["quarantine"].mkdir(parents=True, exist_ok=True)
    label = record.get("version") or "unknown"
    note = paths["quarantine"] / f"{artifact_id}-{label}-{digest[:12] or 'nohash'}.json"
    atomic_write_json(note, entry)
    return entry


def clear_quarantine(paths, state, artifact_id=None):
    if artifact_id:
        (state.get("quarantine") or {}).pop(artifact_id, None)
    else:
        state["quarantine"] = {}
    write_state(paths, state)
    return state.get("quarantine") or {}


# --------------------------------------------------------------------------
# hashing, downloads and jar validation
# --------------------------------------------------------------------------


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def md5_file(path):
    digest = hashlib.md5()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def http_json(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", None)
        if status is not None and not 200 <= status < 300:
            raise RuntimeError(f"{url} returned HTTP {status}")
        return json.loads(response.read().decode("utf-8"))


def validate_zip_archive(path):
    """Reject truncated or structurally broken jars before they replace a file."""
    if not zipfile.is_zipfile(path):
        raise RuntimeError("downloaded file is not a valid jar archive")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names:
            raise RuntimeError("downloaded jar archive is empty")
        broken = archive.testzip()
        if broken:
            raise RuntimeError(f"downloaded jar archive is corrupt at {broken}")
        return names


def jar_manifest(path):
    try:
        with zipfile.ZipFile(path) as archive:
            if "META-INF/MANIFEST.MF" not in archive.namelist():
                return ""
            return archive.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile, KeyError):
        return ""


def jar_plugin_name(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            for candidate in ("plugin.yml", "paper-plugin.yml"):
                if candidate in names:
                    text = archive.read(candidate).decode("utf-8", errors="replace")
                    match = re.search(r"(?im)^name:\s*['\"]?([^'\"\r\n#]+)", text)
                    if match:
                        return match.group(1).strip()
    except (OSError, zipfile.BadZipFile):
        return ""
    return ""


def archive_stale_server_jars(paths, state, kind, keep_name="", keep=DEFAULT_KEEP_ROLLBACKS):
    """Leave a single server jar behind so the next start is deterministic."""
    archived = []
    for path in list_server_jars(paths["server_dir"], kind):
        if keep_name and path.name == keep_name:
            continue
        metadata = archive_current(paths, SERVER_ARTIFACT, path, keep=keep, reason="stale server jar")
        if metadata:
            archived.append(metadata)
    return archived


def validate_server_jar(path):
    size = Path(path).stat().st_size
    if size < MIN_SERVER_JAR_BYTES:
        raise RuntimeError(f"server jar is suspiciously small ({size} bytes)")
    if size > MAX_ARTIFACT_BYTES:
        raise RuntimeError(f"server jar is suspiciously large ({size} bytes)")
    validate_zip_archive(path)
    manifest = jar_manifest(path)
    if not re.search(r"(?im)^Main-Class:", manifest):
        raise RuntimeError("server jar has no Main-Class in its manifest")


def validate_plugin_jar(path, expected_names):
    size = Path(path).stat().st_size
    if size < MIN_PLUGIN_JAR_BYTES:
        raise RuntimeError(f"plugin jar is suspiciously small ({size} bytes)")
    validate_zip_archive(path)
    name = jar_plugin_name(path).lower()
    if not name:
        raise RuntimeError("plugin jar has no plugin.yml name")
    wanted = tuple(item.lower() for item in expected_names)
    if not any(item in name for item in wanted):
        raise RuntimeError(f"plugin jar identifies as {name!r}, expected {', '.join(wanted)}")


def download_verified(url, target, sha256="", md5="", min_bytes=0, max_bytes=MAX_ARTIFACT_BYTES, timeout=120, label=""):
    """Stream a download, verify it, and only then expose it at ``target``."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.download")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    legacy = hashlib.md5()
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is not None and not 200 <= status < 300:
                raise RuntimeError(f"download returned HTTP {status}")
            with tmp.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise RuntimeError(f"download exceeded {max_bytes} bytes")
                    digest.update(chunk)
                    legacy.update(chunk)
                    handle.write(chunk)
            handle_size = written
        if handle_size < min_bytes:
            raise RuntimeError(f"download is suspiciously small ({handle_size} bytes)")
        actual = digest.hexdigest()
        if sha256 and actual.lower() != str(sha256).lower():
            raise RuntimeError(f"sha256 mismatch for {label or target.name}: expected {sha256}, got {actual}")
        if md5 and legacy.hexdigest().lower() != str(md5).lower():
            raise RuntimeError(f"md5 mismatch for {label or target.name}")
        tmp.replace(target)
        return {"path": target, "sha256": actual, "size": handle_size, "verified": bool(sha256 or md5)}
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# vendor APIs
# --------------------------------------------------------------------------


def paper_builds(project, version, timeout=20, fetcher=http_json):
    """Builds from the PaperMC Fill API, newest first."""
    payload = fetcher(FILL_BUILDS.format(project=project.lower(), version=version), timeout=timeout)
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise RuntimeError(str(payload.get("message") or "PaperMC API returned an error"))
    builds = []
    for item in payload if isinstance(payload, list) else []:
        downloads = (item.get("downloads") or {}).get("server:default") or {}
        url = downloads.get("url")
        if not url:
            continue
        builds.append(
            {
                "source": "papermc-fill",
                "version": version,
                "build": str(item.get("id")),
                "channel": str(item.get("channel") or "STABLE").upper(),
                "name": downloads.get("name") or f"{project.lower()}-{version}-{item.get('id')}.jar",
                "url": url,
                "sha256": ((downloads.get("checksums") or {}).get("sha256") or "").lower(),
                "size": downloads.get("size"),
                "time": item.get("time"),
            }
        )
    if not builds:
        raise RuntimeError(f"no builds found for {project} {version}")
    return builds


def purpur_builds(version, timeout=20, fetcher=http_json):
    """Purpur builds, newest first, with the per-build md5 from its own API."""
    payload = fetcher(PURPUR_VERSION.format(version=version), timeout=timeout)
    builds_field = payload.get("builds") or {}
    if isinstance(builds_field, dict):
        raw = list(builds_field.get("all") or [])
        latest = builds_field.get("latest")
        if not raw and latest:
            raw = [str(latest)]
    else:
        raw = list(builds_field)
    if not raw:
        raise RuntimeError(f"no Purpur builds found for {version}")
    builds = []
    ordered = sorted((str(item) for item in raw), key=lambda item: (len(item), item), reverse=True)
    for build in ordered:
        detail = {}
        try:
            detail = fetcher(PURPUR_BUILD.format(version=version, build=build), timeout=timeout)
        except Exception:
            detail = {}
        builds.append(
            {
                "source": "purpur",
                "version": version,
                "build": str(build),
                "channel": "STABLE",
                "name": f"purpur-{version}-{build}.jar",
                "url": PURPUR_DOWNLOAD.format(version=version, build=build),
                "sha256": str(detail.get("sha256") or "").lower(),
                "md5": str(detail.get("md5") or "").lower(),
                "size": None,
                "time": None,
            }
        )
    return builds


def purpur_versions(timeout=20, fetcher=http_json):
    payload = fetcher("https://api.purpurmc.org/v2/purpur", timeout=timeout)
    versions = payload.get("versions") if isinstance(payload, dict) else None
    return [str(item) for item in versions] if isinstance(versions, list) else []


def latest_paper_version(project="paper", timeout=20, fetcher=http_json):
    payload = fetcher(FILL_PROJECT.format(project=project), timeout=timeout)
    versions = payload.get("versions") if isinstance(payload, dict) else {}
    if not isinstance(versions, dict):
        return ""
    for group in versions.values():
        if isinstance(group, list) and group:
            return str(group[0])
    return ""


def geysermc_build(project, platform="spigot", timeout=20, fetcher=http_json):
    payload = fetcher(GEYSERMC_BUILD.format(project=project), timeout=timeout)
    downloads = payload.get("downloads") or {}
    platform_data = downloads.get(platform) or {}
    if not platform_data:
        raise RuntimeError(f"no {platform} download for {project}")
    return {
        "source": "geysermc",
        "version": str(payload.get("version") or ""),
        "build": str(payload.get("build") or ""),
        "channel": str(payload.get("channel") or "default"),
        "name": platform_data.get("name") or PLUGIN_ARTIFACTS[project]["filename"],
        "url": GEYSERMC_DOWNLOAD.format(project=project, platform=platform),
        "sha256": str(platform_data.get("sha256") or "").lower(),
        "size": None,
        "time": payload.get("time"),
    }


# --------------------------------------------------------------------------
# installed artifacts
# --------------------------------------------------------------------------


def list_server_jars(server_dir, kind):
    """All server jars the base image could pick up, in the directory."""
    directory = Path(server_dir)
    if not directory.is_dir():
        return []
    found = []
    seen = set()
    for pattern in SERVER_JAR_GLOBS.get(kind, ("server.jar",)):
        for path in sorted(directory.glob(pattern), key=lambda item: item.name.lower()):
            if path.name in seen or not path.is_file():
                continue
            seen.add(path.name)
            found.append(path)
    return found


def server_jar_rank(path):
    match = SERVER_JAR_PATTERN.match(Path(path).name)
    if not match:
        # server.jar / minecraft_server*.jar carry no build information
        return ((), (), 0)
    return (version_key(match.group("version")), version_key(match.group("build")), 1)


def find_server_jar(server_dir, kind):
    """Locate the server jar the base image would run.

    Newest version/build wins so a leftover older build cannot mask the jar
    that is actually in use.
    """
    jars = list_server_jars(server_dir, kind)
    if not jars:
        return None
    return max(jars, key=server_jar_rank)


def describe_server_jar(path):
    if not path or not Path(path).is_file():
        return None
    info = {
        "name": Path(path).name,
        "path": str(path),
        "size": Path(path).stat().st_size,
        "version": "",
        "build": "",
        "type": "",
    }
    match = SERVER_JAR_PATTERN.match(info["name"])
    if match:
        info["type"] = match.group("kind").upper()
        info["version"] = match.group("version")
        info["build"] = match.group("build")
    return info


def find_plugin_jar(plugins, artifact_id):
    directory = Path(plugins)
    if not directory.is_dir():
        return None
    patterns = PLUGIN_PATTERNS[artifact_id]
    for path in sorted(directory.glob("*.jar"), key=lambda item: item.name.lower()):
        if any(re.match(pattern, path.name, re.IGNORECASE) for pattern in patterns):
            return path
    return None


def plugin_artifact_status(plugins, artifact_id, available=None, installed_sha=""):
    path = find_plugin_jar(plugins, artifact_id)
    installed = None
    if path:
        installed = {
            "name": path.name,
            "path": str(path),
            "size": path.stat().st_size,
            "sha256": installed_sha or sha256_file(path),
        }
    return installed


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------


def newest_build(builds, allow_experimental=False):
    usable = [item for item in builds if allow_experimental or str(item.get("channel", "")).upper() in {"STABLE", "DEFAULT"}]
    if not usable:
        return None
    return max(usable, key=lambda item: version_key(item.get("build")))


def version_key(value):
    pieces = re.split(r"[^0-9A-Za-z]+", str(value or ""))
    key = []
    for piece in pieces:
        if not piece:
            continue
        key.append((0, int(piece)) if piece.isdigit() else (1, piece.lower()))
    return key


def version_newer(left, right):
    return version_key(left) > version_key(right)


def plan_server(options, paths, state, timeout=20, fetcher=http_json):
    """Decide whether the server jar should change. Never downloads here."""
    kind = server_type(options)
    installed = describe_server_jar(find_server_jar(paths["server_dir"], kind))
    plan = {
        "artifact": SERVER_ARTIFACT,
        "type": kind,
        "installed": installed,
        "available": None,
        "update_available": False,
        "blocked_reason": None,
        "warnings": [],
        "error": None,
        "implicit_jump": None,
        "pin": {},
    }
    if kind == "VANILLA":
        plan["warnings"].append("Vanilla server jars are not auto-updated; only Paper and Purpur are managed")
        return plan
    configured = option_text(options, "minecraft_version", "LATEST")
    target_version = configured if configured and configured.upper() != "LATEST" else ""
    if not target_version:
        if not installed or not installed.get("version"):
            plan["warnings"].append("Minecraft version is not pinned yet; no automatic server update until a version is known")
            return plan
        target_version = installed["version"]
    plan["target_version"] = target_version
    try:
        if kind == "PAPER":
            builds = paper_builds("paper", target_version, timeout=timeout, fetcher=fetcher)
        else:
            builds = purpur_builds(target_version, timeout=timeout, fetcher=fetcher)
    except Exception as exc:
        plan["error"] = str(exc)
        plan["warnings"].append(f"Could not check {kind} {target_version} builds: {exc}")
        return plan
    allow_experimental = bool_option(options, "update_allow_experimental", False)
    pinned = option_text(options, "update_server_build", "")
    candidate = None
    if pinned:
        candidate = next((item for item in builds if str(item["build"]) == pinned), None)
        if candidate is None:
            plan["warnings"].append(f"pinned {kind} build {pinned} is not available for {target_version}")
    if candidate is None:
        candidate = newest_build(builds, allow_experimental=allow_experimental)
    if candidate is None:
        plan["warnings"].append(f"no build eligible for the configured update channel on {target_version}")
        return plan
    plan["available"] = candidate
    plan["pin"] = {
        "VERSION": target_version,
        "BUILD_VAR": "PAPER_BUILD" if kind == "PAPER" else "PURPUR_BUILD",
        "BUILD": candidate["build"],
    }
    if not installed:
        plan["update_available"] = True
        plan["reason"] = "server jar missing"
        return plan
    installed_build = installed.get("build") or ""
    if installed.get("version") and version_newer(installed["version"], target_version):
        plan["warnings"].append(f"installed Minecraft {installed['version']} is newer than the configured {target_version}")
        return plan
    if version_newer(target_version, installed.get("version") or ""):
        if not bool_option(options, "update_allow_version_jump", False):
            plan["blocked_reason"] = (
                f"Minecraft {target_version} is a version jump from {installed.get('version')}; "
                "set update_allow_version_jump to allow it"
            )
            return plan
    elif installed_build and not version_newer(candidate["build"], installed_build):
        return plan
    plan["update_available"] = True
    return plan


def detect_implicit_jump(options, paths, state, timeout=20, fetcher=http_json):
    """Report the silent rebuild/upgrade itzg would perform on the next start.

    Without a pinned build the base image resolves the newest build for the
    configured version on every start, so a restart can change the server jar
    with no consent at all.
    """
    kind = server_type(options)
    if kind == "VANILLA":
        return None
    installed = describe_server_jar(find_server_jar(paths["server_dir"], kind))
    if not installed or not installed.get("version"):
        return None
    configured = option_text(options, "minecraft_version", "LATEST")
    finding = {"installed_version": installed["version"], "installed_build": installed.get("build"), "target_version": None, "target_build": None, "kind": None}
    try:
        if configured and configured.upper() != "LATEST":
            target_version = configured
        else:
            if kind == "PAPER":
                target_version = latest_paper_version("paper", timeout=timeout, fetcher=fetcher)
            else:
                versions = purpur_versions(timeout=timeout, fetcher=fetcher)
                target_version = versions[0] if versions else ""
        if not target_version:
            return None
        finding["target_version"] = target_version
        if kind == "PAPER":
            builds = paper_builds("paper", target_version, timeout=timeout, fetcher=fetcher)
        else:
            builds = purpur_builds(target_version, timeout=timeout, fetcher=fetcher)
        candidate = newest_build(builds, allow_experimental=bool_option(options, "update_allow_experimental", False))
        if candidate is None:
            candidate = newest_build(builds, allow_experimental=True)
        if candidate:
            finding["target_build"] = candidate["build"]
            finding["target_channel"] = candidate.get("channel")
    except Exception:
        return None
    if version_newer(finding["target_version"], installed["version"]):
        finding["kind"] = "minecraft_version"
    elif finding["target_build"] and installed.get("build") and version_newer(finding["target_build"], installed["build"]):
        finding["kind"] = "build"
    else:
        return None
    return finding


def plan_plugins(options, paths, state, timeout=20, fetcher=http_json):
    """Plan Geyser and Floodgate together; they must stay a matched pair."""
    plans = {}
    if not bool_option(options, "bedrock_enabled", False):
        for artifact_id in PLUGIN_ARTIFACTS:
            plans[artifact_id] = {
                "artifact": artifact_id,
                "label": PLUGIN_ARTIFACTS[artifact_id]["label"],
                "requested": False,
                "installed": None,
                "available": None,
                "update_available": False,
                "warnings": ["Bedrock support is disabled"],
                "error": None,
                "blocked_reason": None,
            }
        return plans
    if server_type(options) not in PLUGIN_SERVER_TYPES:
        for artifact_id in PLUGIN_ARTIFACTS:
            plans[artifact_id] = {
                "artifact": artifact_id,
                "label": PLUGIN_ARTIFACTS[artifact_id]["label"],
                "requested": False,
                "installed": None,
                "available": None,
                "update_available": False,
                "warnings": [f"{PLUGIN_ARTIFACTS[artifact_id]['label']} requires Paper or Purpur"],
                "error": None,
                "blocked_reason": None,
            }
        return plans
    quarantined = {artifact_id: quarantined_hashes(state, artifact_id) for artifact_id in PLUGIN_ARTIFACTS}
    for artifact_id, spec in PLUGIN_ARTIFACTS.items():
        entry = artifact_state(state, artifact_id)
        installed_path = find_plugin_jar(paths["plugins"], artifact_id)
        installed = None
        if installed_path:
            installed = {
                "name": installed_path.name,
                "path": str(installed_path),
                "size": installed_path.stat().st_size,
                "sha256": sha256_file(installed_path),
                "version": entry.get("version"),
                "build": entry.get("build"),
            }
        plan = {
            "artifact": artifact_id,
            "label": spec["label"],
            "requested": True,
            "installed": installed,
            "available": None,
            "update_available": False,
            "warnings": [],
            "error": None,
            "blocked_reason": None,
        }
        try:
            candidate = geysermc_build(spec["project"], timeout=timeout, fetcher=fetcher)
        except Exception as exc:
            plan["error"] = str(exc)
            plan["warnings"].append(f"Could not check {spec['label']}: {exc}")
            plans[artifact_id] = plan
            continue
        plan["available"] = candidate
        if candidate["sha256"] and candidate["sha256"].lower() in quarantined[artifact_id]:
            plan["blocked_reason"] = "this build failed before and is quarantined"
            plans[artifact_id] = plan
            continue
        if not installed:
            plan["update_available"] = True
            plan["reason"] = "plugin jar missing"
        elif candidate["sha256"] and installed["sha256"].lower() != candidate["sha256"].lower():
            plan["update_available"] = True
            plan["reason"] = "newer vendor build"
        plans[artifact_id] = plan
    return plans


# --------------------------------------------------------------------------
# install, rollback, promotion
# --------------------------------------------------------------------------


def safe_replace(source, target):
    """Atomic replace with a copy fallback for cross-filesystem workspaces."""
    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        source.replace(target)
        return target
    except OSError:
        shutil.copy2(source, target)
        try:
            source.unlink()
        except OSError:
            pass
        return target


def archive_current(paths, artifact_id, target, keep=DEFAULT_KEEP_ROLLBACKS, reason="update"):
    """Keep the file that is about to be replaced so it can be restored."""
    target = Path(target)
    if not target.is_file():
        return None
    _, archive = ensure_workspace(paths, artifact_id)
    stamp = now_stamp()
    directory = archive / artifact_id / stamp
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / target.name
    shutil.move(str(target), str(destination))
    metadata = {
        "artifact": artifact_id,
        "name": target.name,
        "original_path": str(target),
        "archived_path": str(destination),
        "sha256": sha256_file(destination),
        "size": destination.stat().st_size,
        "archived_at": iso_now(),
        "reason": reason,
    }
    atomic_write_json(directory / "artifact.json", metadata)
    prune_archive(archive / artifact_id, keep)
    return metadata


def prune_archive(artifact_root, keep):
    if keep <= 0 or not artifact_root.is_dir():
        return []
    entries = sorted((item for item in artifact_root.iterdir() if item.is_dir()), key=lambda item: item.name, reverse=True)
    removed = []
    for old in entries[keep:]:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old.name)
    return removed


def install_verified(paths, state, artifact_id, staged, target, metadata, previous=None, keep=DEFAULT_KEEP_ROLLBACKS):
    """Move a verified staged file into place and remember its rollback source."""
    target = Path(target)
    safe_replace(staged, target)
    record = artifact_state(state, artifact_id)
    record.update(
        {
            "name": target.name,
            "path": str(target),
            "version": metadata.get("version"),
            "build": metadata.get("build"),
            "channel": metadata.get("channel"),
            "source": metadata.get("source"),
            "url": metadata.get("url"),
            "sha256": sha256_file(target),
            "size": target.stat().st_size,
            "installed_at": iso_now(),
            "known_good": False,
            "previous": previous,
        }
    )
    return record


def pending_update(state):
    pending = state.get("pending_update")
    return pending if isinstance(pending, dict) and pending.get("artifacts") else None


def set_pending_update(state, artifacts, mode, reason=""):
    state["pending_update"] = {
        "artifacts": artifacts,
        "mode": mode,
        "reason": reason,
        "applied_at": iso_now(),
        "watchdog_pid": os.getpid() if mode == "watchdog" else None,
    }
    return state["pending_update"]


def promote_known_good(paths, state):
    """Called only after a start was observed healthy."""
    pending = pending_update(state)
    promoted = []
    for artifact_id in (pending or {}).get("artifacts", []):
        record = artifact_state(state, artifact_id)
        if record.get("path"):
            record["known_good"] = True
            record["known_good_at"] = iso_now()
            promoted.append(artifact_id)
    state["pending_update"] = None
    state["state"] = "idle"
    state["last_result"] = {
        "action": "promote",
        "artifacts": promoted,
        "reason": "start verified",
        "at": iso_now(),
    }
    write_state(paths, state)
    return {"promoted": promoted}


def rollback(paths, state, reason="failed start", artifact_id=None):
    """Restore the archived file(s) that the pending update replaced."""
    pending = pending_update(state)
    targets = [artifact_id] if artifact_id else list((pending or {}).get("artifacts", []))
    if not targets:
        return {"rolled_back": [], "reason": "no pending update", "changed": False}
    results = []
    for target_id in targets:
        record = artifact_state(state, target_id)
        previous = record.get("previous") or {}
        archived = previous.get("archived_path")
        failed = {
            "sha256": record.get("sha256"),
            "version": record.get("version"),
            "build": record.get("build"),
            "channel": record.get("channel"),
            "reason": reason,
        }
        if archived and Path(archived).is_file():
            current = Path(record.get("path") or "")
            if current.is_file():
                quarantine_dir = paths["quarantine"]
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(current), str(quarantine_dir / f"{target_id}-failed-{now_stamp()}-{current.name}"))
                except OSError:
                    pass
            safe_replace(archived, previous.get("original_path") or record.get("path"))
            restored_path = previous.get("original_path") or record.get("path")
            restored = describe_server_jar(restored_path) if target_id == SERVER_ARTIFACT else None
            record.update(
                {
                    "name": Path(restored_path or "unknown").name,
                    "path": restored_path,
                    "version": (restored or {}).get("version") or None,
                    "build": (restored or {}).get("build") or previous.get("build"),
                    "sha256": previous.get("sha256"),
                    "known_good": True,
                    "rolled_back_at": iso_now(),
                    "previous": None,
                }
            )
            add_quarantine(paths, state, target_id, failed)
            results.append({"artifact": target_id, "restored": record.get("path"), "quarantined": failed})
        else:
            results.append({"artifact": target_id, "restored": None, "quarantined": failed, "error": "no archived copy available"})
    state["pending_update"] = None
    state["state"] = "rolled_back"
    state["blocked"] = {"reason": reason, "at": iso_now(), "artifacts": targets}
    state["last_result"] = {"action": "rollback", "reason": reason, "results": results, "at": iso_now()}
    write_state(paths, state)
    return {"rolled_back": results, "reason": reason, "changed": True}


def clear_block(paths, state):
    state["blocked"] = None
    write_state(paths, state)
    return state


# --------------------------------------------------------------------------
# log analysis and the start watchdog
# --------------------------------------------------------------------------


def read_log_tail(path, max_bytes=LOG_TAIL_BYTES):
    path = Path(path)
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        size = path.stat().st_size
        if size > max_bytes:
            handle.seek(-max_bytes, os.SEEK_END)
        return handle.read().decode("utf-8", errors="replace")


def is_noise(line):
    stripped = line.lstrip()
    return any(stripped.startswith(prefix) for prefix in NOISE_PREFIXES)


def analyze_log(text, bedrock_enabled=False):
    """Classify a server log: started, failure evidence, and spam pressure."""
    done = False
    failures = []
    errors = []
    spam = 0
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or is_noise(line):
            continue
        if START_DONE.search(line):
            done = True
        for name, pattern in FAILURE_SIGNATURES:
            if pattern.search(line):
                failures.append({"signature": name, "line": line[-240:]})
                break
        if SPAM_LINE.search(line):
            spam += 1
            if len(errors) < 5:
                errors.append(line[-240:])
    return {
        "started": done,
        "failures": failures[:10],
        "failure_count": len(failures),
        "spam": spam,
        "errors": errors,
        "first_failure": failures[0] if failures else None,
    }


def process_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def read_pid(pid_file):
    try:
        return int(Path(pid_file).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def server_reports_done(log_path, bedrock_enabled=False):
    return analyze_log(read_log_tail(log_path), bedrock_enabled=bedrock_enabled)


def watchdog(paths, state, log_path, pid_file, command_dir, seconds=DEFAULT_WATCHDOG_SECONDS, spam_limit=DEFAULT_SPAM_LIMIT, bedrock_enabled=False, poll=WATCHDOG_POLL_SECONDS):
    """Watch one start; promote on health, otherwise request an abort.

    The watchdog also keeps watching for a settling window after the server
    reported "Done", because the observed Floodgate regression only produced
    classloader errors at runtime and never crashed the process.
    """
    pending = pending_update(state)
    deadline = time.time() + max(1, int(seconds))
    verdict = {"state": "watching", "started": False, "reason": None, "evidence": None, "spam": 0}
    while time.time() < deadline:
        time.sleep(poll)
        analysis = analyze_log(read_log_tail(log_path), bedrock_enabled=bedrock_enabled)
        verdict["spam"] = analysis["spam"]
        verdict["started"] = analysis["started"]
        alive = process_alive(read_pid(pid_file))
        if analysis["failure_count"] and pending:
            verdict.update({"state": "failed", "reason": "update_failure_signature", "evidence": analysis["first_failure"]})
            break
        if analysis["spam"] > spam_limit:
            verdict.update(
                {
                    "state": "failed",
                    "reason": "error_spam_storm",
                    "evidence": {"spam": analysis["spam"], "limit": spam_limit, "sample": analysis["errors"]},
                }
            )
            break
        if not alive and not analysis["started"]:
            verdict.update({"state": "failed", "reason": "server_exited_before_start", "evidence": None})
            break
        if not pending and not alive:
            verdict.update({"state": "stopped", "reason": "server is not running"})
            break
    if verdict["state"] == "watching":
        if verdict["started"]:
            verdict.update({"state": "healthy", "reason": "server reported Done without failure evidence"})
        elif not pending:
            verdict.update({"state": "stopped", "reason": "server is not running"})
        else:
            verdict.update({"state": "failed", "reason": "start_timeout", "evidence": {"seconds": int(seconds)}})
    if verdict["state"] == "healthy":
        promote_known_good(paths, state)
    elif verdict["state"] == "failed":
        state["state"] = "update_failed"
        state["last_result"] = {"action": "watchdog", "reason": verdict["reason"], "evidence": verdict.get("evidence"), "at": iso_now()}
        write_state(paths, state)
        request_abort(command_dir, verdict["reason"], verdict.get("evidence"))
    return verdict


def request_abort(command_dir, reason, evidence=None):
    """Ask the controller to stop the server and roll the update back."""
    directory = Path(command_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "action": "abort_update",
            "reason": str(reason),
            "evidence": json.dumps(evidence or {}, ensure_ascii=False)[:2000],
        }
        target = directory / f"command-{int(time.time())}-{os.getpid()}-updates.json"
        atomic_write_json(target, payload)
        return target
    except OSError as exc:
        log(f"could not request update abort: {exc}")
        return None


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------


def apply_server_update(options, paths, state, plan, timeout=20, keep=DEFAULT_KEEP_ROLLBACKS, downloader=download_verified):
    candidate = plan.get("available")
    if not candidate or not plan.get("update_available"):
        return {"artifact": SERVER_ARTIFACT, "changed": False, "reason": "nothing to do"}
    if plan.get("blocked_reason"):
        return {"artifact": SERVER_ARTIFACT, "changed": False, "reason": plan["blocked_reason"]}
    if candidate.get("sha256") and candidate["sha256"].lower() in quarantined_hashes(state, SERVER_ARTIFACT):
        return {"artifact": SERVER_ARTIFACT, "changed": False, "reason": "candidate is quarantined"}
    staging, _ = ensure_workspace(paths, SERVER_ARTIFACT)
    staged = staging / candidate["name"]
    downloader(
        candidate["url"],
        staged,
        sha256=candidate.get("sha256") or "",
        md5=candidate.get("md5") or "",
        min_bytes=MIN_SERVER_JAR_BYTES,
        label=candidate["name"],
        timeout=max(timeout, 120),
    )
    validate_server_jar(staged)
    kind = plan.get("type") or server_type(options)
    target = paths["server_dir"] / candidate["name"]
    # The jar being replaced carries a different build in its name, so archive
    # the in-use jar explicitly and keep it as the rollback source.
    in_use = find_server_jar(paths["server_dir"], kind)
    previous = archive_current(paths, SERVER_ARTIFACT, in_use, keep=keep, reason="update") if in_use else None
    record = install_verified(paths, state, SERVER_ARTIFACT, staged, target, dict(candidate), previous=previous, keep=keep)
    stale = archive_stale_server_jars(paths, state, kind, keep_name=target.name, keep=keep)
    write_state(paths, state)
    return {"artifact": SERVER_ARTIFACT, "changed": True, "record": record, "candidate": candidate, "archived_stale": stale}


def apply_plugin_updates(options, paths, state, plans, timeout=20, keep=DEFAULT_KEEP_ROLLBACKS, downloader=download_verified):
    results = []
    for artifact_id, plan in plans.items():
        if not plan.get("requested") or not plan.get("update_available") or plan.get("blocked_reason"):
            continue
        candidate = plan.get("available") or {}
        if not candidate.get("url"):
            continue
        if candidate.get("sha256") and candidate["sha256"].lower() in quarantined_hashes(state, artifact_id):
            results.append({"artifact": artifact_id, "changed": False, "reason": "candidate is quarantined"})
            continue
        staging, _ = ensure_workspace(paths, artifact_id)
        staged = staging / candidate["name"]
        try:
            downloader(
                candidate["url"],
                staged,
                sha256=candidate.get("sha256") or "",
                md5=candidate.get("md5") or "",
                min_bytes=MIN_PLUGIN_JAR_BYTES,
                label=candidate["name"],
                timeout=max(timeout, 120),
            )
            validate_plugin_jar(staged, PLUGIN_ARTIFACTS[artifact_id]["names"])
        except Exception as exc:
            results.append({"artifact": artifact_id, "changed": False, "error": str(exc)})
            continue
        target = paths["plugins"] / PLUGIN_ARTIFACTS[artifact_id]["filename"]
        existing = find_plugin_jar(paths["plugins"], artifact_id)
        if existing and existing.name != target.name:
            # Normalize the filename first so the archive step below records the
            # jar that is actually being replaced.
            safe_replace(existing, target)
        previous = archive_current(paths, artifact_id, target, keep=keep, reason="update")
        record = install_verified(paths, state, artifact_id, staged, target, dict(candidate), previous=previous, keep=keep)
        results.append({"artifact": artifact_id, "changed": True, "record": record, "candidate": candidate})
    write_state(paths, state)
    return results


def apply_updates(options, paths, state, timeout=20, fetcher=http_json, downloader=download_verified, keep=DEFAULT_KEEP_ROLLBACKS):
    """Plan and, in safe mode, apply server and plugin updates."""
    mode = update_mode(options)
    result = {"mode": mode, "applied": [], "skipped": [], "plans": {}, "warnings": [], "error": None}
    if mode == "off":
        result["skipped"].append("update_mode is off")
        return result
    if state.get("blocked") and mode == "safe":
        result["skipped"].append(f"updates are blocked: {(state.get('blocked') or {}).get('reason')}")
        return result
    server_plan = plan_server(options, paths, state, timeout=timeout, fetcher=fetcher)
    result["plans"]["server"] = server_plan
    plugin_plans = plan_plugins(options, paths, state, timeout=timeout, fetcher=fetcher)
    result["plans"]["plugins"] = plugin_plans
    jump = detect_implicit_jump(options, paths, state, timeout=timeout, fetcher=fetcher)
    server_plan["implicit_jump"] = jump
    if jump:
        result["warnings"].append(
            f"a restart without a pinned build would move the server from {jump['installed_version']} to {jump['target_version']}"
        )
    for plan in [server_plan] + list(plugin_plans.values()):
        result["warnings"].extend(plan.get("warnings") or [])
        if plan.get("error"):
            result["warnings"].append(plan["error"])
    if mode != "safe":
        result["state"] = "update_available" if (server_plan.get("update_available") or any(item.get("update_available") for item in plugin_plans.values())) else "idle"
        state["state"] = result["state"]
        write_state(paths, state)
        return result
    changed = []
    server_result = apply_server_update(options, paths, state, server_plan, timeout=timeout, keep=keep, downloader=downloader)
    if server_result.get("changed"):
        changed.append(SERVER_ARTIFACT)
        result["applied"].append(server_result)
    elif server_result.get("reason"):
        result["skipped"].append(f"server: {server_result['reason']}")
    plugin_results = apply_plugin_updates(options, paths, state, plugin_plans, timeout=timeout, keep=keep, downloader=downloader)
    for item in plugin_results:
        if item.get("changed"):
            changed.append(item["artifact"])
            result["applied"].append(item)
        elif item.get("error"):
            result["warnings"].append(f"{item['artifact']}: {item['error']}")
    if changed:
        set_pending_update(state, changed, "apply", reason="pending start verification")
        state["state"] = "pending_restart"
    else:
        state["state"] = "idle"
    write_state(paths, state)
    return result


def server_environment(options, paths, state, timeout=20, fetcher=http_json):
    """Environment the base image must receive to stop implicit jar changes."""
    kind = server_type(options)
    result = {"mode": update_mode(options), "type": kind, "env": {}, "warnings": []}
    if kind not in PLUGIN_SERVER_TYPES:
        return result
    installed = describe_server_jar(find_server_jar(paths["server_dir"], kind))
    configured = option_text(options, "minecraft_version", "LATEST")
    record = artifact_state(state, SERVER_ARTIFACT)
    known_good = record.get("known_good") and record.get("name")
    target_version = ""
    target_build = ""
    if known_good:
        target_version = record.get("version") or ""
        target_build = record.get("build") or ""
    elif installed:
        target_version = installed.get("version") or ""
        target_build = installed.get("build") or ""
    if configured and configured.upper() != "LATEST" and not known_good:
        target_version = configured
    if not target_version:
        result["warnings"].append("no pinned Minecraft version available yet")
    if result["mode"] == "safe":
        if target_version:
            result["env"]["VERSION"] = target_version
        if target_build:
            result["env"]["PAPER_BUILD" if kind == "PAPER" else "PURPUR_BUILD"] = target_build
        result["pinned"] = True
    else:
        result["pinned"] = False
    return result


def status_payload(options, paths, state, timeout=20, refresh=False, fetcher=http_json):
    payload = {
        "enabled": update_mode(options) != "off",
        "mode": update_mode(options),
        "state": state.get("state") or "idle",
        "healthy": state.get("state") not in {"update_failed"},
        "pending_update": pending_update(state),
        "blocked": state.get("blocked"),
        "last_result": state.get("last_result"),
        "server": artifact_state(state, SERVER_ARTIFACT),
        "plugins": {artifact_id: artifact_state(state, artifact_id) for artifact_id in PLUGIN_ARTIFACTS},
        "quarantine": state.get("quarantine") or {},
        "warnings": [],
        "error": None,
        "checked_at": state.get("updated_at"),
    }
    if refresh:
        mode = update_mode(options)
        if mode == "off":
            payload["warnings"].append("update_mode is off")
            return payload
        plan = plan_server(options, paths, state, timeout=timeout, fetcher=fetcher)
        plugins = plan_plugins(options, paths, state, timeout=timeout, fetcher=fetcher)
        payload["server_plan"] = plan
        payload["plugin_plans"] = plugins
        payload["update_available"] = bool(plan.get("update_available")) or any(item.get("update_available") for item in plugins.values())
        payload["implicit_jump"] = detect_implicit_jump(options, paths, state, timeout=timeout, fetcher=fetcher)
        payload["warnings"] = list(plan.get("warnings") or [])
        for item in plugins.values():
            payload["warnings"].extend(item.get("warnings") or [])
        if plan.get("blocked_reason"):
            payload["warnings"].append(plan["blocked_reason"])
        payload["error"] = plan.get("error")
    return payload


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def add_common_arguments(parser):
    parser.add_argument("--data-dir", default=os.environ.get("MC_DATA_DIR", "/media/minecraft-java"))
    parser.add_argument("--server-dir", default=os.environ.get("MINECRAFT_SERVER_DIR", "/data"))
    parser.add_argument("--options-file", default=os.environ.get("OPTIONS_FILE", "/tmp/ha-minecraft-options.json"))
    parser.add_argument("--status-file", default=os.environ.get("MINECRAFT_UPDATE_STATUS_FILE", "/tmp/ha-minecraft-update-status.json"))
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--control-state-file", default=os.environ.get("MINECRAFT_CONTROL_STATE_FILE", "/tmp/ha-minecraft-control-state"))


def controller_busy(control_state_file):
    try:
        return Path(control_state_file).read_text(encoding="utf-8").strip().lower() in {"running", "starting", "stopping"}
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description="Safe update engine for the Minecraft server jar and Geyser/Floodgate")
    subparsers = parser.add_subparsers(dest="action", required=True)

    for name in ("status", "check", "plan", "apply", "server-env", "rollback", "promote", "block-state", "verify-log"):
        add_common_arguments(subparsers.add_parser(name))

    watchdog_parser = subparsers.add_parser("watchdog")
    add_common_arguments(watchdog_parser)
    watchdog_parser.add_argument("--log-file", default="")
    watchdog_parser.add_argument("--pid-file", default="")
    watchdog_parser.add_argument("--command-dir", default=os.environ.get("MINECRAFT_COMMAND_DIR", "/tmp/ha-minecraft-commands"))
    watchdog_parser.add_argument("--seconds", type=int, default=0)
    watchdog_parser.add_argument("--spam-limit", type=int, default=0)

    args = parser.parse_args()
    options = read_options_json(args.options_file)
    paths = update_paths(args.data_dir, args.server_dir)
    state = read_state(paths)
    mode = update_mode(options)

    try:
        if args.action == "status":
            payload = status_payload(options, paths, state, timeout=args.timeout)
        elif args.action == "check":
            payload = status_payload(options, paths, state, timeout=args.timeout, refresh=True)
        elif args.action == "plan":
            payload = {
                "server": plan_server(options, paths, state, timeout=args.timeout),
                "plugins": plan_plugins(options, paths, state, timeout=args.timeout),
                "implicit_jump": detect_implicit_jump(options, paths, state, timeout=args.timeout),
            }
        elif args.action == "apply":
            if controller_busy(args.control_state_file):
                payload = {"state": "skipped", "reason": "Minecraft is running; updates are only applied while stopped"}
            else:
                payload = apply_updates(options, paths, state, timeout=args.timeout)
        elif args.action == "server-env":
            payload = server_environment(options, paths, state, timeout=args.timeout)
            for key, value in (payload.get("env") or {}).items():
                print(f"{key}={value}")
            if payload.get("warnings"):
                for warning in payload["warnings"]:
                    log(warning)
            if args.status_file:
                atomic_write_json(Path(args.status_file), status_payload(options, paths, state))
            return 0
        elif args.action == "rollback":
            payload = rollback(paths, state, reason="manual rollback")
        elif args.action == "promote":
            payload = promote_known_good(paths, state)
        elif args.action == "block-state":
            payload = {"blocked": state.get("blocked"), "mode": mode}
        elif args.action == "verify-log":
            log_file = Path(options.get("log_file") or paths["logs"] / "latest.log")
            payload = analyze_log(read_log_tail(log_file), bedrock_enabled=bool_option(options, "bedrock_enabled", False))
        else:
            log_file = args.log_file or str(paths["logs"] / "latest.log")
            if mode == "off":
                payload = {"state": "off", "reason": "update_mode is off"}
            else:
                payload = watchdog(
                    paths,
                    state,
                    log_file,
                    args.pid_file,
                    args.command_dir,
                    seconds=args.seconds or int_option(options, "update_watchdog_seconds", DEFAULT_WATCHDOG_SECONDS, 30, 3600),
                    spam_limit=args.spam_limit or int_option(options, "update_error_spam_limit", DEFAULT_SPAM_LIMIT, 50, 200000),
                    bedrock_enabled=bool_option(options, "bedrock_enabled", False),
                )
        if args.status_file:
            atomic_write_json(Path(args.status_file), status_payload(options, paths, state))
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        payload = {"enabled": mode != "off", "mode": mode, "state": "error", "message": str(exc), "error": str(exc)}
        if getattr(args, "status_file", ""):
            try:
                atomic_write_json(Path(args.status_file), payload)
            except OSError:
                pass
        log(f"ERROR: {exc}")
        return 1 if args.action in {"apply", "rollback"} else 0


if __name__ == "__main__":
    sys.exit(main())
