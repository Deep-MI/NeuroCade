#!/usr/bin/env bash
set -euo pipefail

URL="${1:?URL is required}"
ATTEMPTS="${2:-30}"
DELAY_SECONDS="${3:-1}"
HEADER="${4:-}"

for ((attempt = 1; attempt <= ATTEMPTS; attempt += 1)); do
  if [[ -n "$HEADER" ]]; then
    curl -fsS --connect-timeout 2 --max-time 5 -H "$HEADER" "$URL" >/dev/null && exit 0
  else
    curl -fsS --connect-timeout 2 --max-time 5 "$URL" >/dev/null && exit 0
  fi
  sleep "$DELAY_SECONDS"
done

echo "Timed out waiting for $URL" >&2
exit 1
