#!/bin/bash
# One crawl with .env loaded, logged to logs/. Run by the launchd agent that
# scripts/install_schedule.sh installs; safe to run by hand too.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

# Skip if the previous crawl is still running (shlock clears stale locks).
if ! /usr/bin/shlock -f logs/crawl.lock -p $$; then
    echo "$(date '+%F %T') skipped: previous crawl still running" >> logs/crawl.log
    exit 0
fi
trap 'rm -f logs/crawl.lock' EXIT

set -a
. ./.env
set +a

log="logs/crawl-$(date +%Y%m%d-%H%M).log"
.venv/bin/python -m gept.run > "$log" 2>&1
status=$?
echo "$(date '+%F %T') exit=$status log=$log" >> logs/crawl.log

# Keep the last 60 run logs (a month at two runs a day).
ls -1t logs/crawl-*.log 2>/dev/null | tail -n +61 | xargs rm -f
exit $status
