#!/usr/bin/env sh
# Smoke test against a running stack. Needs curl only.
#   API_URL=http://localhost:8000 sh scripts/smoke_test.sh
# 1. waits for /readyz, 2. POST /certify with data/records/S01.json and
# checks the response shape, 3. checks the row and its blob path in Postgres
# through the api container's own connection.
set -eu
API_URL="${API_URL:-http://localhost:8000}"
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say() { printf '%s\n' "$*"; }
fail() { say "FAIL: $*"; exit 1; }
field() { # field NAME FILE -> the FIRST "NAME":"value" or "NAME":value (timings_ms has a "route" too)
  grep -o "\"$1\":\"\{0,1\}[^\",}]*" "$2" | head -1 | sed 's/^"[^"]*":"\{0,1\}//'
}

say "1. waiting for $API_URL/readyz"
i=0
until curl -fsS "$API_URL/readyz" -o "$TMP/ready.json" 2>/dev/null; do
  i=$((i + 1)); [ "$i" -ge 90 ] && { cat "$TMP/ready.json" 2>/dev/null; fail "not ready after 90 s"; }
  sleep 1
done
say "   ready: $(cat "$TMP/ready.json")"

say "2. POST /certify (S01)"
code=$(curl -sS -o "$TMP/cert.json" -w '%{http_code}' -X POST "$API_URL/certify" \
  -H 'content-type: application/json' --data-binary @data/records/S01.json)
[ "$code" = "200" ] || { cat "$TMP/cert.json"; fail "/certify returned $code"; }
route=$(field route "$TMP/cert.json")
case "$route" in auto|review) ;; *) fail "bad route '$route'";; esac
grep -q '"prompt_sha1"' "$TMP/cert.json" || fail "no versions block"
say "   route=$route reasons=$(grep -o '"reasons":\[[^]]*\]' "$TMP/cert.json" | head -1)"

say "3. the stored row and its blob"
row=$(docker compose -f docker/compose.yml --env-file .env exec -T postgres \
  psql -U "${PGUSER:-automccd}" -d "${PGDATABASE:-automccd}" -Atc \
  "select route || ' ' || coalesce(blob_path, '') from certificates where record_id = 'S01' order by id desc limit 1")
[ -n "$row" ] || fail "no certificates row for S01"
case "$row" in *" certificates/"*) ;; *) fail "row has no blob path: $row";; esac
say "   $row"
say "SMOKE OK"
