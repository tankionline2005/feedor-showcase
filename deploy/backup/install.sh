#!/usr/bin/env bash
# Installs the backup's scripts and systemd units from this folder and turns the timer on.
#   bash /opt/kindergarten-monitoring/src/deploy/backup/install.sh
# Needs /etc/feedor-backup/rclone.conf and recipient.txt (docs/backup.md, «Настройка с нуля»).
set -euo pipefail
cd "$(dirname "$0")"
for f in /etc/feedor-backup/rclone.conf /etc/feedor-backup/recipient.txt; do
  [[ -f $f ]] || { echo "$f is missing" >&2; exit 1; }
done
install -m 700 feedor-backup feedor-backup-verify /usr/local/sbin/
install -m 644 feedor-backup.service feedor-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now feedor-backup.timer
systemctl list-timers feedor-backup.timer --no-pager
