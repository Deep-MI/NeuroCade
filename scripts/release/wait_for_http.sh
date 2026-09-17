#!/usr/bin/env bash
set -euo pipefail

URL="${1:?URL is required}"
ATTEMPTS="${2:-30}"
DELAY_SECONDS="${3:-1}"
HEADERS=()
for header in "${@:4}"; do
  [[ -n "$header" ]] && HEADERS+=(-H "$header")
done

for ((attempt = 1; attempt <= ATTEMPTS; attempt += 1)); do
  curl -fsS --connect-timeout 2 --max-time 5 "${HEADERS[@]}" "$URL" >/dev/null && exit 0
  sleep "$DELAY_SECONDS"
done

echo "Timed out waiting for $URL" >&2
exit 1
