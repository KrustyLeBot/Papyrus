#!/bin/sh
# Started as root (plain `docker run`): drops to PUID:PGID (default 1000:1000) after making /data usable.
# Started with `user:` in the compose file (the hardened setup): already unprivileged, runs as is.
set -e
if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-1000}"; PGID="${PGID:-1000}"
  mkdir -p /data
  # the folder itself only, never recursively: a large archive must not delay the start
  chown "$PUID:$PGID" /data 2>/dev/null || true
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups python -m papyrus "$@"
fi
if [ ! -w /data ]; then
  echo "papyrus: /data is not writable by uid $(id -u). Set PUID/PGID to the owner of the data folder." >&2
  exit 1
fi
exec python -m papyrus "$@"
