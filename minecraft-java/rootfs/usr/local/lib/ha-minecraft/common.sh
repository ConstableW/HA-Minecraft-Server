#!/usr/bin/env bash
set -Eeuo pipefail

OPTIONS_FILE="${OPTIONS_FILE:-/data/options.json}"
SUPERVISOR_OPTIONS_FILE="${SUPERVISOR_OPTIONS_FILE:-/data/options.json}"
MC_DATA_DIR="${MC_DATA_DIR:-/media/minecraft-java}"
RUNTIME_CONFIG_DIR="${RUNTIME_CONFIG_DIR:-/ha-addon-config}"
PERSISTENT_OPTIONS_YAML="${PERSISTENT_OPTIONS_YAML:-${MC_DATA_DIR}/addon-options.yaml}"
RUNTIME_OPTIONS_YAML="${RUNTIME_OPTIONS_YAML:-${RUNTIME_CONFIG_DIR}/options.yaml}"
DASHBOARD_COMMAND_YAML="${DASHBOARD_COMMAND_YAML:-${MC_DATA_DIR}/dashboard-command.yaml}"
EFFECTIVE_OPTIONS_FILE="${EFFECTIVE_OPTIONS_FILE:-/tmp/ha-minecraft-options.json}"
BACKUP_DIR="${BACKUP_DIR:-${MC_DATA_DIR}/backups}"
LEGACY_DIRS=("/data/minecraft" "/data" "/hassio_data/minecraft" "/hassio_data")
MC_UID="${MC_UID:-1000}"
MC_GID="${MC_GID:-1000}"

log() {
  printf '[%s] %s\n' "$1" "$2"
}

fail() {
  log "error" "$1" >&2
  exit 1
}

prepare_options_file() {
  local mode
  mkdir -p "${MC_DATA_DIR}" "${RUNTIME_CONFIG_DIR}"
  seed_persistent_options_from_runtime_copy

  if ! mode="$(python3 /usr/local/lib/ha-minecraft/options_config.py \
      "${SUPERVISOR_OPTIONS_FILE}" \
      "${PERSISTENT_OPTIONS_YAML}" \
      "${EFFECTIVE_OPTIONS_FILE}" \
      load-persistent)"; then
    fail "Failed to load persistent add-on config from ${PERSISTENT_OPTIONS_YAML}"
  fi

  OPTIONS_FILE="${EFFECTIVE_OPTIONS_FILE}"
  if [[ "${mode}" == "seeded" ]]; then
    log "init" "Created persistent add-on config at ${PERSISTENT_OPTIONS_YAML}"
  else
    log "init" "Using persistent add-on config: ${PERSISTENT_OPTIONS_YAML}"
  fi

  mirror_runtime_options_copy
  apply_dashboard_command
  mirror_runtime_options_copy
  restore_supervisor_options || log "init" "Could not mirror persistent config back to Home Assistant UI"
  export OPTIONS_FILE
}

seed_persistent_options_from_runtime_copy() {
  if [[ ! -e "${PERSISTENT_OPTIONS_YAML}" && -r "${RUNTIME_OPTIONS_YAML}" ]]; then
    cp "${RUNTIME_OPTIONS_YAML}" "${PERSISTENT_OPTIONS_YAML}"
    log "init" "Imported existing add-on config from ${RUNTIME_OPTIONS_YAML} to ${PERSISTENT_OPTIONS_YAML}"
  fi
}

mirror_runtime_options_copy() {
  [[ -r "${PERSISTENT_OPTIONS_YAML}" ]] || return 0
  mkdir -p "${RUNTIME_CONFIG_DIR}"
  cp "${PERSISTENT_OPTIONS_YAML}" "${RUNTIME_OPTIONS_YAML}" || true
}

apply_dashboard_command() {
  local mode
  [[ -r "${DASHBOARD_COMMAND_YAML}" ]] || return 0

  if ! mode="$(python3 /usr/local/lib/ha-minecraft/options_config.py \
      "${OPTIONS_FILE}" \
      "${PERSISTENT_OPTIONS_YAML}" \
      "${EFFECTIVE_OPTIONS_FILE}" \
      apply-dashboard-command \
      "${DASHBOARD_COMMAND_YAML}")"; then
    fail "Failed to apply dashboard command from ${DASHBOARD_COMMAND_YAML}"
  fi

  if [[ "${mode}" == "command-applied" ]]; then
    OPTIONS_FILE="${EFFECTIVE_OPTIONS_FILE}"
    restore_supervisor_options || log "init" "Could not write dashboard command back to Home Assistant UI"
    log "init" "Applied dashboard command from ${DASHBOARD_COMMAND_YAML}"
  fi
}

restore_supervisor_options() {
  local token="${SUPERVISOR_TOKEN:-${HASSIO_TOKEN:-}}"
  local body="/tmp/ha-minecraft-supervisor-options.json"
  local slug="${ADDON_SLUG:-${HOSTNAME:-}}"
  local endpoints=()
  local endpoint
  [[ -n "${token}" ]] || return 1
  [[ -r "${EFFECTIVE_OPTIONS_FILE}" ]] || return 1

  jq -c '{options: .}' "${EFFECTIVE_OPTIONS_FILE}" > "${body}"

  endpoints+=("http://supervisor/addons/self/options")
  [[ -n "${slug}" ]] && endpoints+=("http://supervisor/addons/${slug}/options")
  endpoints+=("http://supervisor/addons/local_minecraft_java/options")
  endpoints+=("http://supervisor/addons/minecraft_java/options")

  for endpoint in "${endpoints[@]}"; do
    if curl -fsS \
        -X POST \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/json" \
        --data-binary "@${body}" \
        "${endpoint}" >/tmp/ha-minecraft-supervisor-options-response.json 2>/tmp/ha-minecraft-supervisor-options-error.log; then
      log "init" "Mirrored persistent config back to Home Assistant UI via ${endpoint}"
      return 0
    fi
  done

  log "init" "Supervisor options update failed; last curl error: $(cat /tmp/ha-minecraft-supervisor-options-error.log 2>/dev/null || true)"
  return 1
}

opt() {
  local key="$1"
  local default="${2-}"
  if [[ -r "${OPTIONS_FILE}" ]]; then
    jq -er --arg key "${key}" --arg default "${default}" '.[$key] // $default' "${OPTIONS_FILE}"
  else
    printf '%s\n' "${default}"
  fi
}

bool_opt() {
  local key="$1"
  local default="$2"
  local value
  value="$(opt "${key}" "${default}")"
  case "${value,,}" in
    true) printf 'TRUE\n' ;;
    false) printf 'FALSE\n' ;;
    *) fail "Invalid boolean value for ${key}" ;;
  esac
}

memory_to_mib() {
  local value="$1"
  if [[ ! "${value}" =~ ^([0-9]+)([GgMm])$ ]]; then
    return 1
  fi

  local amount="${BASH_REMATCH[1]}"
  local unit="${BASH_REMATCH[2],,}"
  if [[ "${unit}" == "g" ]]; then
    printf '%s\n' "$((amount * 1024))"
  else
    printf '%s\n' "${amount}"
  fi
}

validate_memory_pair() {
  local initial="$1"
  local maximum="$2"
  local initial_mib maximum_mib

  initial_mib="$(memory_to_mib "${initial}")" || fail "memory_initial must look like 2G or 4096M"
  maximum_mib="$(memory_to_mib "${maximum}")" || fail "memory_max must look like 6G or 6144M"

  if (( initial_mib <= 0 || maximum_mib <= 0 )); then
    fail "Memory values must be greater than zero"
  fi
  if (( initial_mib > maximum_mib )); then
    fail "memory_initial must not be greater than memory_max"
  fi
}

ensure_rcon_password() {
  local enabled="$1"
  local password="$2"
  if [[ "${enabled}" == "TRUE" && -z "${password}" ]]; then
    fail "rcon_password must not be empty when enable_rcon is true"
  fi
}

prepare_data_dir() {
  log "init" "Minecraft data directory: ${MC_DATA_DIR}"
  mkdir -p "${MC_DATA_DIR}" "${BACKUP_DIR}" "${MC_DATA_DIR}/plugins" "${MC_DATA_DIR}/mods" "${MC_DATA_DIR}/logs"
  chmod 775 "${MC_DATA_DIR}" "${BACKUP_DIR}" || true
}

quarantine_homeassistant_config_leak() {
  local config_dir="${MC_DATA_DIR}/config"
  [[ -d "${config_dir}" ]] || return 0

  if [[ -e "${config_dir}/.HA_VERSION" || -e "${config_dir}/configuration.yaml" || -d "${config_dir}/custom_components" ]]; then
    local quarantine_dir="${MC_DATA_DIR}/migration-conflicts/homeassistant-config-$(date -u +%Y%m%dT%H%M%SZ)"
    log "init" "Home Assistant config files were found in Minecraft config storage; moving them to ${quarantine_dir}"
    mkdir -p "$(dirname "${quarantine_dir}")"
    mv "${config_dir}" "${quarantine_dir}"
    mkdir -p "${config_dir}"
  fi
}

link_data_dir_entry() {
  local name="$1"
  local kind="$2"
  local source="/data/${name}"
  local target="${MC_DATA_DIR}/${name}"

  if [[ -L "${source}" ]]; then
    local current_target
    current_target="$(readlink "${source}")"
    if [[ "${current_target}" == "${target}" ]]; then
      return 0
    fi
    log "init" "Updating /data/${name} link from ${current_target} to ${target}"
    rm -f "${source}"
  fi

  if [[ -e "${source}" && ! -e "${target}" ]]; then
    log "init" "Moving /data/${name} to media storage"
    mv "${source}" "${target}"
  elif [[ -e "${source}" ]]; then
    local conflict_dir="${MC_DATA_DIR}/migration-conflicts/$(date -u +%Y%m%dT%H%M%SZ)"
    log "init" "Moving conflicting /data/${name} aside; media storage is authoritative"
    mkdir -p "${conflict_dir}"
    mv "${source}" "${conflict_dir}/${name}"
  fi

  if [[ "${kind}" == "dir" ]]; then
    mkdir -p "${target}"
  else
    mkdir -p "$(dirname "${target}")"
  fi

  ln -s "${target}" "${source}"
}

stage_paper_legacy_dimension() {
  local name="$1"
  local source="/data/${name}"
  local target="${MC_DATA_DIR}/${name}"

  [[ -d "${target}" ]] || return 0

  if [[ -L "${source}" ]]; then
    rm -f "${source}"
  elif [[ -e "${source}" ]]; then
    return 0
  fi

  log "init" "Staging legacy Paper/Purpur dimension ${name} as runtime copy for one-time migration"
  copy_dir_contents "${target}" "${source}"
}

quarantine_migrated_paper_legacy_dimensions() {
  local level_name="$1"
  local name target quarantine_dir

  for name in "${level_name}_nether" "${level_name}_the_end"; do
    target="${MC_DATA_DIR}/${name}"
    [[ -d "${target}" ]] || continue
    [[ ! -e "/data/${name}" ]] || continue

    quarantine_dir="${MC_DATA_DIR}/migration-conflicts/paper-legacy-dimensions-$(date -u +%Y%m%dT%H%M%SZ)"
    log "init" "Paper/Purpur migrated ${name}; moving legacy media copy to ${quarantine_dir}/${name}"
    mkdir -p "${quarantine_dir}"
    mv "${target}" "${quarantine_dir}/${name}"
  done
}

world_has_level_data() {
  local dir="$1"
  [[ -f "${dir}/level.dat" ]]
}

media_world_candidates() {
  find "${MC_DATA_DIR}" -mindepth 2 -maxdepth 3 -type f -name level.dat -print 2>/dev/null \
    | while read -r level_file; do
        local rel
        rel="${level_file#${MC_DATA_DIR}/}"
        case "${rel}" in
          backups/*|cache/*|config/*|crash-reports/*|libraries/*|logs/*|migration-conflicts/*|mods/*|plugins/*|versions/*)
            continue
            ;;
        esac
        printf '%s\n' "${rel%/level.dat}"
      done
}

canonical_media_world_name() {
  local wanted="$1"
  local wanted_lower="${wanted,,}"
  local candidate

  if world_has_level_data "${MC_DATA_DIR}/${wanted}"; then
    printf '%s\n' "${wanted}"
    return 0
  fi

  while IFS= read -r candidate; do
    if [[ "${candidate,,}" == "${wanted_lower}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done < <(media_world_candidates)

  return 1
}

validate_world_selection() {
  local level_name="$1"
  local allow_create="${2:-FALSE}"
  local configured_world="${MC_DATA_DIR}/${level_name}"
  local runtime_world="/data/${level_name}"
  local candidates

  if world_has_level_data "${configured_world}"; then
    log "init" "Existing world detected: ${configured_world}"
    return 0
  fi

  if [[ ! -L "${runtime_world}" ]] && world_has_level_data "${runtime_world}"; then
    log "init" "Runtime world found for ${level_name}; moving it into media storage"
    mkdir -p "${MC_DATA_DIR}"
    mv "${runtime_world}" "${configured_world}"
    return 0
  fi

  local canonical_name
  if canonical_name="$(canonical_media_world_name "${level_name}")"; then
    fail "World name '${level_name}' differs only by case from existing world '${canonical_name}'. Use '${canonical_name}' in the add-on options."
  fi

  mapfile -t candidates < <(media_world_candidates)
  if (( ${#candidates[@]} > 0 )); then
    if [[ "${allow_create}" == "TRUE" ]]; then
      log "init" "No existing world named ${level_name}; Minecraft will create it. Existing worlds: ${candidates[*]}"
      return 0
    fi
    log "error" "Configured level_name '${level_name}' has no level.dat in ${configured_world}" >&2
    log "error" "Existing media world candidates: ${candidates[*]}" >&2
    fail "Set level_name to an existing world name, enable allow_create_worlds, or copy/restore the wanted world to ${configured_world}"
  fi

  log "init" "No existing world named ${level_name}; Minecraft will create it"
}

copy_dir_contents() {
  local from="$1"
  local to="$2"
  mkdir -p "${to}"
  if [[ -d "${from}" ]]; then
    tar -C "${from}" -cf - . | tar -C "${to}" -xf -
  fi
}

sync_world_from_media() {
  local level_name="$1"
  for world_dir in "${level_name}" "${level_name}_nether" "${level_name}_the_end"; do
    if [[ -L "/data/${world_dir}" ]]; then
      log "init" "Removing old symlink /data/${world_dir}; Vanilla requires a real directory"
      rm -f "/data/${world_dir}"
    fi
    if [[ -d "${MC_DATA_DIR}/${world_dir}" && ! -e "/data/${world_dir}" ]]; then
      log "init" "Copying ${world_dir} from media storage to runtime storage"
      copy_dir_contents "${MC_DATA_DIR}/${world_dir}" "/data/${world_dir}"
    fi
  done
}

sync_world_to_media() {
  local level_name="$1"
  for world_dir in "${level_name}" "${level_name}_nether" "${level_name}_the_end"; do
    if [[ -d "/data/${world_dir}" && ! -L "/data/${world_dir}" ]]; then
      log "sync" "Copying ${world_dir} to media storage"
      rm -rf "${MC_DATA_DIR}/${world_dir}.sync-tmp"
      copy_dir_contents "/data/${world_dir}" "${MC_DATA_DIR}/${world_dir}.sync-tmp"
      rm -rf "${MC_DATA_DIR}/${world_dir}"
      mv "${MC_DATA_DIR}/${world_dir}.sync-tmp" "${MC_DATA_DIR}/${world_dir}"
    fi
  done
  fix_media_permissions
}

ensure_json_array_file() {
  local path="$1"
  if [[ ! -e "${path}" || ! -s "${path}" ]]; then
    printf '[]\n' > "${path}"
    return 0
  fi
  if ! jq -e 'type == "array"' "${path}" >/dev/null 2>&1; then
    local backup="${path}.invalid-$(date -u +%Y%m%dT%H%M%SZ)"
    log "init" "Moving invalid JSON file ${path} to ${backup}"
    mv "${path}" "${backup}"
    printf '[]\n' > "${path}"
  fi
}

normalize_minecraft_files() {
  for file in ops.json whitelist.json banned-players.json banned-ips.json usercache.json; do
    ensure_json_array_file "${MC_DATA_DIR}/${file}"
  done
}

fix_media_permissions() {
  log "init" "Ensuring media storage permissions"
  chown -R "${MC_UID}:${MC_GID}" "${MC_DATA_DIR}" || true
  chmod -R u+rwX,g+rwX "${MC_DATA_DIR}" || true
}

link_data_storage() {
  local level_name="${1:-world}"
  local server_type="${2:-PAPER}"
  local allow_create="${3:-FALSE}"
  log "init" "Linking Minecraft storage into /data"

  mkdir -p /data
  validate_world_selection "${level_name}" "${allow_create}"
  if [[ "${server_type}" == "VANILLA" ]]; then
    sync_world_from_media "${level_name}"
  else
    link_data_dir_entry "${level_name}" dir
    if [[ "${server_type}" == "PAPER" || "${server_type}" == "PURPUR" ]]; then
      stage_paper_legacy_dimension "${level_name}_nether"
      stage_paper_legacy_dimension "${level_name}_the_end"
    else
      link_data_dir_entry "${level_name}_nether" dir
      link_data_dir_entry "${level_name}_the_end" dir
    fi
  fi
  for entry in plugins mods logs config libraries versions crash-reports cache; do
    link_data_dir_entry "${entry}" dir
  done
  for entry in eula.txt server.properties ops.json whitelist.json banned-players.json banned-ips.json usercache.json permissions.yml bukkit.yml spigot.yml paper.yml; do
    link_data_dir_entry "${entry}" file
  done
  normalize_minecraft_files
  quarantine_homeassistant_config_leak
  fix_media_permissions
}

has_minecraft_data() {
  local dir="$1"
  [[ -f "${dir}/server.properties" ]] && return 0
  find "${dir}" -mindepth 2 -maxdepth 2 -name level.dat | read -r
}

run_migration() {
  local marker="${MC_DATA_DIR}/.ha_migration_checked"
  [[ -e "${marker}" ]] && has_minecraft_data "${MC_DATA_DIR}" && return 0

  for legacy in "${LEGACY_DIRS[@]}"; do
    if [[ -d "${legacy}" && "${legacy}" != "${MC_DATA_DIR}" ]]; then
      if ! has_minecraft_data "${legacy}"; then
        continue
      fi
      if has_minecraft_data "${MC_DATA_DIR}"; then
        log "init" "Legacy data found at ${legacy}; current data directory already contains Minecraft data, skipping automatic copy"
      else
        log "init" "Copying legacy data from ${legacy} to ${MC_DATA_DIR}"
        tar -C "${legacy}" --exclude='./options.json' -cf - . | tar -C "${MC_DATA_DIR}" -xf -
      fi
      break
    fi
  done

  date -u +%Y-%m-%dT%H:%M:%SZ > "${marker}"
}

rcon_cmd() {
  local command="$1"
  local password="${RCON_PASSWORD:-}"
  local port="${RCON_PORT:-25575}"

  [[ "${ENABLE_RCON:-FALSE}" == "TRUE" ]] || return 0
  [[ -n "${password}" ]] || return 0

  if command -v rcon-cli >/dev/null 2>&1; then
    rcon-cli --host 127.0.0.1 --port "${port}" --password "${password}" "${command}"
  else
    log "rcon" "rcon-cli is unavailable; skipped command"
    return 1
  fi
}
