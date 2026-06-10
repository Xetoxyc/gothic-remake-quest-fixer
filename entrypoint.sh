#!/bin/sh
set -e

if [ ! -f "$OODLE_LIB" ]; then
  echo "[entrypoint] fetching Oodle library -> $OODLE_LIB"
  curl -fsSL -o "$OODLE_LIB" "$OODLE_URL" || {
    echo "[entrypoint] ERROR: could not download Oodle from $OODLE_URL"
    echo "             Mount your own liboo2corelinux64.so.9 at $OODLE_LIB instead."
    exit 1
  }
fi

echo "[entrypoint] Gothic 1 Remake Savegame Editor listening on http://localhost:${PORT}"
exec python /app/app/server.py
