#!/bin/sh
# Run as the owner of the mounted data directory so files written to the host
# bind mount keep the host user's ownership, whatever their UID is.
set -e
if [ "$(id -u)" = "0" ] && [ -d /app/data ]; then
    owner="$(stat -c %u /app/data)"
    group="$(stat -c %g /app/data)"
    if [ "$owner" != "0" ]; then
        exec setpriv --reuid "$owner" --regid "$group" --clear-groups dmi-geoparquet "$@"
    fi
fi
exec dmi-geoparquet "$@"
