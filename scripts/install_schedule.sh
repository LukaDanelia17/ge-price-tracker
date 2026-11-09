#!/bin/bash
# Install (or reinstall) the launchd agent that runs scripts/crawl.sh at 08:00
# and 20:00 Mac local time. A run missed while the Mac slept starts on wake.
#   scripts/install_schedule.sh            install / update
#   scripts/install_schedule.sh --remove   uninstall
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
label=ge.pricetracker.crawl
plist="$HOME/Library/LaunchAgents/$label.plist"
domain="gui/$(id -u)"

launchctl bootout "$domain/$label" 2>/dev/null || true
if [[ "${1:-}" == "--remove" ]]; then
    rm -f "$plist"
    echo "removed $label"
    exit 0
fi

mkdir -p "$root/logs" "$(dirname "$plist")"
cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$label</string>
    <key>ProgramArguments</key>
    <array><string>/bin/bash</string><string>$root/scripts/crawl.sh</string></array>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
    </array>
    <key>StandardOutPath</key><string>$root/logs/launchd.out</string>
    <key>StandardErrorPath</key><string>$root/logs/launchd.err</string>
</dict>
</plist>
EOF
launchctl bootstrap "$domain" "$plist"
echo "installed $label: runs at 08:00 and 20:00 ($(readlink /etc/localtime | sed 's|.*/zoneinfo/||'))"
echo "run now:  launchctl kickstart $domain/$label"
