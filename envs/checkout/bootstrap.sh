#!/bin/sh
# Trial bootstrap. Runs as PID 1 inside the trial container.
#
# The workdir is a tmpfs, which means anything written into it before the
# container starts is masked by the mount. The runner therefore cannot stage
# inputs at create time; it starts the container, copies the payload in, and
# drops a ready marker. This script seeds the workdir from the image's read-only
# copy, waits for that marker, and then hands control to the entrypoint.
set -eu

WORKDIR="${MERIDIAN_WORKDIR:-/work}"
PAYLOAD="$WORKDIR/.meridian"
READY="$PAYLOAD/ready"
DEADLINE_TICKS="${MERIDIAN_PAYLOAD_TICKS:-600}"   # ticks of 0.1s

mkdir -p "$WORKDIR/out" "$WORKDIR/orders"
cp -a /opt/checkout/seed/. "$WORKDIR/"

i=0
while [ "$i" -lt "$DEADLINE_TICKS" ]; do
    if [ -f "$READY" ]; then
        break
    fi
    i=$((i + 1))
    sleep 0.1
done

if [ ! -f "$READY" ]; then
    echo "meridian: trial payload never arrived at $PAYLOAD" >&2
    exit 70
fi

export PYTHONPATH="$PAYLOAD:$PAYLOAD/sut${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$PAYLOAD/entrypoint.py" "$PAYLOAD/context.json"
