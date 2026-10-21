#!/usr/bin/env sh
# Soak lokal app + worker dengan sampling resource, heartbeat, dan beban health HTTPS.
set -eu

ROOT="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DURATION_S="${SOAK_DURATION_S:-1800}"
INTERVAL_S="${SOAK_INTERVAL_S:-30}"
REQUESTS_PER_INTERVAL="${SOAK_REQUESTS_PER_INTERVAL:-5}"
OUTPUT_PREFIX="${SOAK_OUTPUT_PREFIX:-/tmp/apd-soak}"
CSV="${OUTPUT_PREFIX}.csv"
SUMMARY="${OUTPUT_PREFIX}.summary"

case "$DURATION_S:$INTERVAL_S:$REQUESTS_PER_INTERVAL" in
  *[!0-9:]*|0:*|*:0:*|*:0) echo "Durasi, interval, dan jumlah request harus bilangan bulat positif." >&2; exit 2 ;;
esac

compose() {
  docker compose --env-file .env -f deploy/docker-compose.prod.yml "$@"
}

app="$(compose ps -q app)"
worker="$(compose ps -q worker)"
if [ -z "$app" ] || [ -z "$worker" ]; then
  echo "Container app atau worker tidak berjalan." >&2
  exit 1
fi

started_at="$(date +%s)"
ends_at=$((started_at + DURATION_S))
samples=0
http_ok=0
errors=0
peak_app=0
peak_worker=0

printf '%s\n' 'waktu,app_mem_byte,worker_mem_byte,app_cpu,worker_cpu,app_health,worker_health,app_restart,worker_restart,app_oom,worker_oom,heartbeat_sehat,heartbeat_usia_s,https' > "$CSV"

while [ "$(date +%s)" -lt "$ends_at" ]; do
  sample_started="$(date +%s)"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  current_app="$(compose ps -q app)"
  current_worker="$(compose ps -q worker)"
  [ "$current_app" = "$app" ] || errors=$((errors + 1))
  [ "$current_worker" = "$worker" ] || errors=$((errors + 1))

  app_state="$(docker inspect --format '{{.State.Health.Status}},{{.RestartCount}},{{.State.OOMKilled}}' "$app" 2>/dev/null || printf 'hilang,99,true')"
  worker_state="$(docker inspect --format '{{.State.Health.Status}},{{.RestartCount}},{{.State.OOMKilled}}' "$worker" 2>/dev/null || printf 'hilang,99,true')"
  app_health="$(printf '%s' "$app_state" | cut -d, -f1)"
  app_restart="$(printf '%s' "$app_state" | cut -d, -f2)"
  app_oom="$(printf '%s' "$app_state" | cut -d, -f3)"
  worker_health="$(printf '%s' "$worker_state" | cut -d, -f1)"
  worker_restart="$(printf '%s' "$worker_state" | cut -d, -f2)"
  worker_oom="$(printf '%s' "$worker_state" | cut -d, -f3)"

  stats="$(docker stats --no-stream --format '{{.Name}},{{.CPUPerc}}' "$app" "$worker" 2>/dev/null || true)"
  app_cpu="$(printf '%s\n' "$stats" | awk -F, '$1 == "deploy-app-1" {print $2}')"
  worker_cpu="$(printf '%s\n' "$stats" | awk -F, '$1 == "deploy-worker-1" {print $2}')"
  app_bytes="$(docker exec "$app" sh -c 'cat /sys/fs/cgroup/memory.current' 2>/dev/null || printf 0)"
  worker_bytes="$(docker exec "$worker" sh -c 'cat /sys/fs/cgroup/memory.current' 2>/dev/null || printf 0)"
  [ "$app_bytes" -le "$peak_app" ] || peak_app="$app_bytes"
  [ "$worker_bytes" -le "$peak_worker" ] || peak_worker="$worker_bytes"

  heartbeat="$(docker exec "$app" python -c 'from app.worker_health import read; s=read(); print("true" if s["sehat"] else "false", s["usia_heartbeat_detik"])' 2>/dev/null || printf 'false -1')"
  heartbeat_ok="$(printf '%s' "$heartbeat" | awk '{print $1}')"
  heartbeat_age="$(printf '%s' "$heartbeat" | awk '{print $2}')"

  [ "$app_health" = healthy ] || errors=$((errors + 1))
  [ "$worker_health" = healthy ] || errors=$((errors + 1))
  [ "$app_restart" = 0 ] || errors=$((errors + 1))
  [ "$worker_restart" = 0 ] || errors=$((errors + 1))
  [ "$app_oom" = false ] || errors=$((errors + 1))
  [ "$worker_oom" = false ] || errors=$((errors + 1))
  [ "$heartbeat_ok" = true ] || errors=$((errors + 1))

  request=0
  last_code=000
  while [ "$request" -lt "$REQUESTS_PER_INTERVAL" ]; do
    last_code="$(curl --max-time 5 -ksS -o /dev/null -w '%{http_code}' https://localhost/api/healthz || printf 000)"
    if [ "$last_code" = 200 ]; then
      http_ok=$((http_ok + 1))
    else
      errors=$((errors + 1))
    fi
    request=$((request + 1))
    if [ "$request" -lt "$REQUESTS_PER_INTERVAL" ]; then
      sleep $((INTERVAL_S / REQUESTS_PER_INTERVAL))
    fi
  done

  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$now" "$app_bytes" "$worker_bytes" "$app_cpu" "$worker_cpu" \
    "$app_health" "$worker_health" "$app_restart" "$worker_restart" \
    "$app_oom" "$worker_oom" "$heartbeat_ok" "$heartbeat_age" "$last_code" >> "$CSV"
  samples=$((samples + 1))

  elapsed=$(( $(date +%s) - sample_started ))
  if [ "$elapsed" -lt "$INTERVAL_S" ]; then
    sleep $((INTERVAL_S - elapsed))
  fi
done

duration=$(( $(date +%s) - started_at ))
{
  printf 'samples=%s\n' "$samples"
  printf 'http_ok=%s\n' "$http_ok"
  printf 'errors=%s\n' "$errors"
  printf 'peak_app_bytes=%s\n' "$peak_app"
  printf 'peak_worker_bytes=%s\n' "$peak_worker"
  printf 'duration_s=%s\n' "$duration"
} > "$SUMMARY"
cat "$SUMMARY"

[ "$errors" -eq 0 ]