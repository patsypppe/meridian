#!/bin/sh
# Trial bootstrap. Runs as PID 1 inside the trial container.
#
# Two constraints shape this script:
#
# 1. The rootfs is read-only, so the Docker archive API refuses to copy anything
#    in — "container rootfs is marked read-only" — even into a writable tmpfs.
# 2. The workdir is a tmpfs, so anything staged before the container starts is
#    masked when the mount appears.
#
# So the payload arrives on **stdin** as a tar stream, which is a pipe and cares
# about neither. EOF is the signal that the payload is complete: no marker file,
# no polling, no race. No host directory is mounted, so there is no channel
# through which one trial could reach another.
set -eu

WORKDIR="${MERIDIAN_WORKDIR:-/work}"

mkdir -p "$WORKDIR/out" "$WORKDIR/orders"
# `cp -R`, not `cp -a`: the workdir tmpfs is root-owned and the trial user
# cannot set times on it, which makes -a fail under `set -e`.
cp -R /opt/checkout/seed/. "$WORKDIR/"

# Blocks until the harness finishes sending and closes its end.
tar -xf - -C "$WORKDIR"

PAYLOAD="$WORKDIR/.meridian"
if [ ! -f "$PAYLOAD/entrypoint.py" ]; then
    echo "meridian: payload did not contain an entrypoint" >&2
    exit 70
fi

export PYTHONPATH="$PAYLOAD:$PAYLOAD/sut${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$PAYLOAD/entrypoint.py" "$PAYLOAD/context.json"
