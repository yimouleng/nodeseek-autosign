#!/usr/bin/env bash
# Replace this VPS's daily cron entry with a timer that catches up after downtime.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR=/etc/systemd/system

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root" >&2
  exit 1
fi
if [ "$APP_DIR" != /opt/nodeseek-autosign ]; then
  echo "This timer is configured for /opt/nodeseek-autosign" >&2
  exit 1
fi
if [ ! -x "$APP_DIR/.venv/bin/python" ] || [ ! -f "$APP_DIR/.env" ]; then
  echo "The application is not installed or configured" >&2
  exit 1
fi

systemd-analyze verify \
  "$APP_DIR/systemd/nodeseek-sign.service" \
  "$APP_DIR/systemd/nodeseek-sign.timer"

install -m 644 "$APP_DIR/systemd/nodeseek-sign.service" "$UNIT_DIR/nodeseek-sign.service"
install -m 644 "$APP_DIR/systemd/nodeseek-sign.timer" "$UNIT_DIR/nodeseek-sign.timer"
systemctl daemon-reload
systemctl enable --now nodeseek-sign.timer
systemctl is-enabled --quiet nodeseek-sign.timer
systemctl is-active --quiet nodeseek-sign.timer

mkdir -p "$APP_DIR/logs"
umask 077
backup="$APP_DIR/logs/crontab.before-systemd-$(date +%Y%m%d-%H%M%S)"
crontab -l > "$backup" 2>/dev/null || true
updated="$(mktemp)"
trap 'rm -f "$updated"' EXIT
python3 - "$backup" "$updated" "$APP_DIR" <<'PY'
from pathlib import Path
import sys

backup, updated, app = map(Path, sys.argv[1:])
lines = backup.read_text().splitlines()
kept = [line for line in lines if not (
    ("run_sign.sh" in line and str(app) in line)
    or line.startswith("# nodeseek-autosign 每日签到")
)]
updated.write_text("\n".join(kept) + ("\n" if kept else ""))
PY
if ! cmp -s "$backup" "$updated"; then
  crontab "$updated"
fi

echo "Timer enabled; next run:"
systemctl list-timers nodeseek-sign.timer --no-pager
echo "Previous crontab: $backup"
