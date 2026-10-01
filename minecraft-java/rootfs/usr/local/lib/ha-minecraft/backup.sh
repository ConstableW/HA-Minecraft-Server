#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "${SCRIPT_DIR}/common.sh"

rotate_backups() {
  local retention_days="${1:-14}"
  local keep_last="${2:-0}"
  mkdir -p "${BACKUP_DIR}"
  if (( retention_days > 0 )); then
    find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'minecraft-*.tar.gz' -mtime "+${retention_days}" -print \
      | while read -r removed; do
          if [[ -e "${removed}.persistent" ]]; then
            continue
          fi
          rm -f "${removed}"
          rm -f "${removed}.json" 2>/dev/null || true
          log "backup" "Removed old backup ${removed}"
        done
  fi

  if (( keep_last > 0 )); then
    find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'minecraft-*.tar.gz' -print \
      | while read -r candidate; do
          [[ -e "${candidate}.persistent" ]] || printf '%s\n' "${candidate}"
        done \
      | sort -r \
      | awk -v keep="${keep_last}" 'NR > keep {print}' \
      | while read -r removed; do
          rm -f "${removed}"
          rm -f "${removed}.json" 2>/dev/null || true
          log "backup" "Removed backup beyond keep_last=${keep_last}: ${removed}"
        done
  fi
}

validate_backup_keep_last() {
  local keep_last="${1:-0}"
  if [[ ! "${keep_last}" =~ ^[0-9]+$ ]]; then
    fail "backup_keep_last must be a number"
  fi
}

validate_backup_retention_days() {
  local retention_days="${1:-14}"
  if [[ ! "${retention_days}" =~ ^[0-9]+$ ]]; then
    fail "backup_retention_days must be a number"
  fi
}

last_backup_marker() {
  printf '%s\n' "${BACKUP_DIR}/.last-successful-backup"
}

backup_cooldown_active() {
  local min_interval_minutes="${BACKUP_MIN_INTERVAL_MINUTES:-30}"
  local marker
  [[ "${min_interval_minutes}" =~ ^[0-9]+$ ]] || fail "backup_min_interval_minutes must be a number"
  (( min_interval_minutes > 0 )) || return 1

  marker="$(last_backup_marker)"
  [[ -s "${marker}" ]] || return 1

  local last now elapsed min_interval_seconds
  last="$(cat "${marker}" 2>/dev/null || printf '0')"
  [[ "${last}" =~ ^[0-9]+$ ]] || return 1
  now="$(date +%s)"
  elapsed=$((now - last))
  min_interval_seconds=$((min_interval_minutes * 60))

  if (( elapsed < min_interval_seconds )); then
    log "backup" "Skipping backup; last successful backup was $((elapsed / 60)) minutes ago and backup_min_interval_minutes=${min_interval_minutes}"
    return 0
  fi
  return 1
}

mark_backup_success() {
  date +%s > "$(last_backup_marker)"
}

sanitize_backup_reason() {
  local reason="${1:-manual}"
  reason="$(printf '%s' "${reason}" | tr -c 'A-Za-z0-9_.-' '-')"
  reason="${reason#-}"
  reason="${reason%-}"
  [[ -n "${reason}" ]] || reason="manual"
  printf '%s\n' "${reason}"
}

create_backup() {
  local reason="${1:-manual}"
  local force="${2:-false}"
  local retention_days="${BACKUP_RETENTION_DAYS:-14}"
  local keep_last="${BACKUP_KEEP_LAST:-10}"
  local timestamp tmp final

  reason="$(sanitize_backup_reason "${reason}")"
  validate_backup_retention_days "${retention_days}"
  validate_backup_keep_last "${keep_last}"
  mkdir -p "${BACKUP_DIR}"

  if [[ "${force}" != "true" ]] && backup_cooldown_active; then
    rotate_backups "${retention_days}" "${keep_last}" || log "backup" "Backup rotation failed"
    return 0
  fi

  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  tmp="${BACKUP_DIR}/.minecraft-${timestamp}.${reason}.tar.gz.tmp"
  final="${BACKUP_DIR}/minecraft-${timestamp}.${reason}.tar.gz"

  log "backup" "Creating backup ${final}"
  if [[ "${ENABLE_RCON:-FALSE}" == "TRUE" && "${RCON_MESSAGE_BACKUP_SAVE:-TRUE}" == "TRUE" ]]; then
    rcon_cmd "save-all flush" || log "backup" "RCON save-all failed; continuing with filesystem backup"
    rcon_cmd "save-off" || log "backup" "RCON save-off failed; continuing with filesystem backup"
  fi

  if [[ -n "${RUNTIME_WORLD_LEVEL:-}" ]]; then
    sync_world_to_media "${RUNTIME_WORLD_LEVEL}" || log "backup" "Runtime world sync failed; continuing with media backup"
  fi

  if tar -C "${MC_DATA_DIR}" \
      --exclude='./backups' \
      --exclude='./logs' \
      --exclude='./*.tmp' \
      -czf "${tmp}" .; then
    mv "${tmp}" "${final}"
    mark_backup_success
    log "backup" "Backup created"
  else
    rm -f "${tmp}"
    log "backup" "Backup failed" >&2
    [[ "${ENABLE_RCON:-FALSE}" == "TRUE" && "${RCON_MESSAGE_BACKUP_SAVE:-TRUE}" == "TRUE" ]] && rcon_cmd "save-on" || true
    return 1
  fi

  [[ "${ENABLE_RCON:-FALSE}" == "TRUE" && "${RCON_MESSAGE_BACKUP_SAVE:-TRUE}" == "TRUE" ]] && rcon_cmd "save-on" || true
  rotate_backups "${retention_days}" "${keep_last}" || log "backup" "Backup rotation failed"
}

safe_backup_path() {
  local name="$1"
  name="$(basename "${name}")"
  if [[ ! "${name}" =~ ^minecraft-[A-Za-z0-9T._-]+\.tar\.gz$ ]]; then
    return 1
  fi
  printf '%s/%s\n' "${BACKUP_DIR}" "${name}"
}

restore_backup() {
  local name="$1"
  local backup tmp restore_guard
  backup="$(safe_backup_path "${name}")" || {
    log "backup" "Invalid backup name: ${name}"
    return 1
  }
  if [[ ! -f "${backup}" ]]; then
    log "backup" "Backup not found: ${name}"
    return 1
  fi

  log "backup" "Restoring backup ${backup}"
  if tar -tzf "${backup}" | awk 'BEGIN { bad=0 } /(^\/|(^|\/)\.\.($|\/))/ { bad=1 } END { exit bad }'; then
    :
  else
    log "backup" "Backup archive contains unsafe paths: ${name}"
    return 1
  fi
  mkdir -p "${BACKUP_DIR}" "${MC_DATA_DIR}/restore-conflicts"
  tmp="${MC_DATA_DIR}/restore-conflicts/.restore-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  mkdir -p "${tmp}"
  if ! tar -C "${tmp}" -xzf "${backup}"; then
    rm -rf "${tmp}"
    log "backup" "Backup archive could not be extracted: ${name}"
    return 1
  fi
  restore_guard="$(find "${tmp}" -maxdepth 1 -mindepth 1 -print -quit)"
  if [[ -z "${restore_guard}" ]]; then
    rm -rf "${tmp}"
    log "backup" "Backup archive is empty: ${name}"
    return 1
  fi

  find "${MC_DATA_DIR}" -mindepth 1 -maxdepth 1 \
    ! -name 'backups' \
    ! -name 'restore-conflicts' \
    -exec rm -rf {} +
  find "${tmp}" -mindepth 1 -maxdepth 1 -exec mv {} "${MC_DATA_DIR}/" \;
  rmdir "${tmp}" 2>/dev/null || true
  log "backup" "Restore completed from ${name}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:-backup}" in
    restore)
      restore_backup "${2:-}"
      ;;
    *)
      create_backup "${1:-manual}"
      ;;
  esac
fi
