#!/bin/bash
# One crawl with .env loaded, logged to logs/. Run by the launchd agent that
# scripts/install_schedule.sh installs; safe to run by hand too.
#   scripts/crawl.sh            crawl now
#   scripts/crawl.sh --if-due   crawl only if a scheduled run (08:00/20:00) is missing
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

if [[ "${1:-}" == "--if-due" ]] && ! .venv/bin/python -m gept.schedule; then
    exit 0
fi

# Skip if the previous crawl is still running (shlock clears stale locks).
if ! /usr/bin/shlock -f logs/crawl.lock -p $$; then
    echo "$(date '+%F %T') skipped: previous crawl still running" >> logs/crawl.log
    exit 0
fi
trap 'rm -f logs/crawl.lock' EXIT

set -a
. ./.env
set +a

# launchd fires this on wake/boot, which can happen before Wi-Fi/DNS is up. The
# run would then die on the first DB connect and the Telegram alert couldn't
# send either. Wait up to ~5 min for outbound DNS+HTTPS before starting.
for _ in $(seq 1 20); do
    curl -sf -m 5 https://api.telegram.org/ >/dev/null 2>&1 && break
    echo "$(date '+%F %T') waiting for network…" >> logs/crawl.log
    sleep 15
done

log="logs/crawl-$(date +%Y%m%d-%H%M).log"
.venv/bin/python -m gept.run > "$log" 2>&1
status=$?
echo "$(date '+%F %T') exit=$status log=$log" >> logs/crawl.log

# Refresh dashboard/index.html with whatever was saved.
.venv/bin/python dashboard.py >> "$log" 2>&1 || echo "dashboard build failed" >> "$log"

# Keep the last 60 run logs (a month at two runs a day).
ls -1t logs/crawl-*.log 2>/dev/null | tail -n +61 | xargs rm -f
exit $status
