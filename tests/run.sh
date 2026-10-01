#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

# shellcheck source=../minecraft-java/rootfs/usr/local/lib/ha-minecraft/common.sh
. "${ROOT}/minecraft-java/rootfs/usr/local/lib/ha-minecraft/common.sh"

assert_fail() {
  if ( "$@" ) >/tmp/ha-mc-test.out 2>&1; then
    echo "Expected failure: $*" >&2
    exit 1
  fi
}

memory_to_mib 2G | grep -qx 2048
memory_to_mib 4096M | grep -qx 4096
validate_memory_pair 2G 6G
assert_fail validate_memory_pair bad 6G
assert_fail validate_memory_pair 8G 6G
assert_fail ensure_rcon_password TRUE ""
ensure_rcon_password TRUE "secret"
combine_csv() {
  local first="$1"
  local second="$2"
  if [[ -n "${first}" && -n "${second}" ]]; then
    printf '%s,%s\n' "${first}" "${second}"
  else
    printf '%s%s\n' "${first}" "${second}"
  fi
}
[[ "$(combine_csv Owner Admin1,Admin2)" == "Owner,Admin1,Admin2" ]]

OPTIONS_FILE="${TMP}/options.json"
cat > "${OPTIONS_FILE}" <<'JSON'
{"eula":false,"enable_rcon":false}
JSON
assert_fail env OPTIONS_FILE="${OPTIONS_FILE}" HA_MINECRAFT_LIB_DIR="${ROOT}/minecraft-java/rootfs/usr/local/lib/ha-minecraft" bash "${ROOT}/minecraft-java/rootfs/usr/local/bin/ha-minecraft"

MC_DATA_DIR="${TMP}/media/minecraft-java"
BACKUP_DIR="${MC_DATA_DIR}/backups"
mkdir -p "${MC_DATA_DIR}/world" "${BACKUP_DIR}" "${MC_DATA_DIR}/logs"
echo world > "${MC_DATA_DIR}/world/level.dat"
echo log > "${MC_DATA_DIR}/logs/latest.log"
echo old > "${BACKUP_DIR}/minecraft-20000101T000000Z.manual.tar.gz"
touch -d '40 days ago' "${BACKUP_DIR}/minecraft-20000101T000000Z.manual.tar.gz"

# shellcheck source=../minecraft-java/rootfs/usr/local/lib/ha-minecraft/backup.sh
. "${ROOT}/minecraft-java/rootfs/usr/local/lib/ha-minecraft/backup.sh"
ENABLE_RCON=FALSE BACKUP_RETENTION_DAYS=1 create_backup test
find "${BACKUP_DIR}" -name 'minecraft-*.test.tar.gz' | grep -q .
if tar -tzf "$(find "${BACKUP_DIR}" -name 'minecraft-*.test.tar.gz' | head -n 1)" | grep -q '^./logs/'; then
  echo "Backup unexpectedly contains logs" >&2
  exit 1
fi
if [[ -e "${BACKUP_DIR}/minecraft-20000101T000000Z.manual.tar.gz" ]]; then
  echo "Backup rotation did not remove old backup" >&2
  exit 1
fi

echo "All shell tests passed"
python3 "${ROOT}/tests/test_bedrock.py"
python3 "${ROOT}/tests/test_security.py"
