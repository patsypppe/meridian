#!/usr/bin/env bash
# Regenerate every number on the showcase page from real Meridian runs.
#
#   make images            # once: build the environment + proxy images
#   bash site/data/regenerate.sh
#
# No API key is used or needed: every run talks to the deterministic stand-in
# provider behind the proxy, exactly as CI does. Takes ~6 minutes on a laptop.
#
# What it runs (captured under site/data/raw/, then exported to showcase.json):
#   1. meridian run            the reference suite, offline from cassettes
#   2. site/data/probe.py      the contamination probe, both directions
#                              (writer then probe; isolated, then shared volume)
#   3. meridian suite audit    can any task be passed without doing the work?
#   4. meridian gate  x3       head vs one recorded baseline, three real changes:
#        degraded-prompt       the planner loses its expiry sentence (PR #12)
#        invoice-rounding      write_invoice rounds totals down to a dollar
#        unchanged             the agent as committed
#
# Seeded edits touch only fixtures/ (the system under test), are restored on
# exit even if a step fails, and never touch the harness or its thresholds.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
RAW="site/data/raw"
TAPES="$(mktemp -d -t meridian-showcase-cassettes)"
PLANNER="fixtures/checkout_agent/prompts/planner.md"
TOOLS="fixtures/checkout_agent/tools.py"
cp "$PLANNER" "$TAPES/planner.md.orig"
cp "$TOOLS" "$TAPES/tools.py.orig"

restore() {
  cp "$TAPES/planner.md.orig" "$PLANNER"
  cp "$TAPES/tools.py.orig" "$TOOLS"
  rm -rf "$TAPES"
}
trap restore EXIT

# The stand-in provider is the default; unset keys anyway so nothing can bill.
unset ANTHROPIC_API_KEY OPENAI_API_KEY || true

mkdir -p "$RAW"
M="uv run meridian"
SUITE="./suites/checkout-agent"

echo "1/4 reference run" >&2
$M run --suite "$SUITE" --n 5 --k 3 --proxy-mode replay \
  --out "$RAW/run.json" > "$RAW/run.txt"

echo "2/4 contamination probe, both directions" >&2
uv run python site/data/probe.py > /dev/null

echo "3/4 suite audit" >&2
$M suite audit --suite "$SUITE" --out "$RAW/audit.json" > "$RAW/audit.txt"

gate() {
  local name="$1"
  # `|| true`: exit 1 is the FAIL verdict, which is a result, not an error.
  $M gate --suite "$SUITE" --baseline-run "$BASE" --n 5 --k 3 --tolerance 0.03 \
    --require-significance --proxy-mode record --cassettes "$TAPES" \
    --comment-file "$RAW/gate-$name.md" > /dev/null 2> "$RAW/gate-$name.log" || true
}

echo "4/4 gates" >&2
# One baseline, recorded from the unmodified agent, pinned by run id for all
# three gates. (`--baseline-ref` would look the baseline up by commit, and on a
# dirty working tree the previous gate's *head* run is archived under that same
# commit — so each gate would be compared against the one before it.)
$M run --suite "$SUITE" --n 5 --k 3 --proxy-mode record --gate-mode \
  --cassettes "$TAPES" --out "$RAW/gate-baseline.json" > /dev/null
BASE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_id"])' "$RAW/gate-baseline.json")"
cp fixtures/checkout_agent/prompts/planner.degraded.md "$PLANNER"
gate degraded-prompt
cp "$TAPES/planner.md.orig" "$PLANNER"

python3 - "$TOOLS" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text()
old = '                    "total_cents": total_cents,'
new = '                    "total_cents": (total_cents // 100) * 100,'
if old not in s:
    raise SystemExit("seeded edit anchor not found; refusing to score a no-op")
p.write_text(s.replace(old, new, 1))
PY
gate invoice-rounding
cp "$TAPES/tools.py.orig" "$TOOLS"

gate unchanged

echo "export" >&2
uv run python site/data/export.py
