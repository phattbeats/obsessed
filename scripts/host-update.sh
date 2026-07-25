#!/usr/bin/env bash
# host-update.sh — one-command redeploy of the Obsessed container on PHATT-RAID.
#
# PHA-1342 originally aligned docker-compose.yml with the CI image-naming
# convention; this script standardises the *host-side* redeploy so it is
# reproducible, idempotent, and logs to a stable path.
#
# Run on PHATT-RAID (Unraid host) — NOT inside the OpenClaw container.
# Requires: docker + docker compose plugin (or `docker-compose` v1).
#
# Usage (on PHATT-RAID):
#   sudo bash /mnt/user/appdata/obsessed/scripts/host-update.sh
#
# Environment overrides:
#   COMPOSE_FILE   path to docker-compose.yml    (default: alongside this script)
#   SERVICE_NAME   compose service name          (default: obsessed)
#   PUBLISHED_PORT host port the container maps (default: 10198)
#   HEALTH_TIMEOUT seconds to wait for /api/health (default: 60)
#
# Exit codes:
#   0  container up, /api/health returned 200 within timeout
#   1  usage error (bad env / missing compose file)
#   2  image pull failed
#   3  container recreate failed
#   4  health probe failed (container up but not serving)

set -Eeuo pipefail

# --- locate compose file ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${SCRIPT_DIR}/../docker-compose.yml}"
if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "host-update: docker-compose.yml not found at ${COMPOSE_FILE}" >&2
  exit 1
fi

SERVICE_NAME="${SERVICE_NAME:-obsessed}"
PUBLISHED_PORT="${PUBLISHED_PORT:-10198}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"
LOG_DIR="${LOG_DIR:-/var/log/obsessed}"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/host-update.log"

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '[%s] %s\n' "$(ts)" "$*" | tee -a "${LOG_FILE}"; }

log "host-update: starting (compose=${COMPOSE_FILE}, service=${SERVICE_NAME}, port=${PUBLISHED_PORT})"

# --- pull + recreate ---
if ! docker compose -f "${COMPOSE_FILE}" pull "${SERVICE_NAME}" 2>&1 | tee -a "${LOG_FILE}"; then
  log "host-update: docker compose pull FAILED"
  exit 2
fi

if ! docker compose -f "${COMPOSE_FILE}" up -d "${SERVICE_NAME}" 2>&1 | tee -a "${LOG_FILE}"; then
  log "host-update: docker compose up -d FAILED"
  exit 3
fi

# --- health probe ---
log "host-update: probing http://127.0.0.1:${PUBLISHED_PORT}/api/health (timeout=${HEALTH_TIMEOUT}s)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
ok=0
while (( $(date +%s) < deadline )); do
  if code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 \
              "http://127.0.0.1:${PUBLISHED_PORT}/api/health" || true); then
    if [[ "${code}" == "200" ]]; then
      ok=1
      break
    fi
  fi
  sleep 2
done

if (( ok == 1 )); then
  log "host-update: HEALTHY — http://10.0.0.100:${PUBLISHED_PORT}/"
  exit 0
else
  log "host-update: UNHEALTHY after ${HEALTH_TIMEOUT}s — check docker logs"
  docker compose -f "${COMPOSE_FILE}" logs --tail=50 "${SERVICE_NAME}" 2>&1 | tee -a "${LOG_FILE}" || true
  exit 4
fi
