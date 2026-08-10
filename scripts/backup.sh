#!/bin/sh
# Database dump into ./backups/. Copying a hot InnoDB datadir does NOT give a consistent
# backup — regardless of the file system.
#
# Cron:  0 3 * * *  /var/lib/claude-usage-monitor/scripts/backup.sh
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

[ -f .env ] || { echo "no .env in $DIR"; exit 1; }
DB=$(grep -oP '^MARIADB_DATABASE=\K.*' .env)
PW=$(grep -oP '^MARIADB_ROOT_PASSWORD=\K.*' .env)
KEEP=${KEEP_DAYS:-14}

mkdir -p backups
OUT="backups/claude_usage_$(date -u +%Y%m%d_%H%M%S).sql.gz"

# The password via defaults-file, NOT in argv — otherwise anyone can see it via `ps`.
CNF=$(mktemp); chmod 600 "$CNF"
printf '[client]\nuser=root\npassword=%s\n' "$PW" > "$CNF"
trap 'rm -f "$CNF"' EXIT INT TERM

docker exec -i claude_usage_monitor_mariadb sh -c \
  "cat > /tmp/.bk.cnf && mariadb-dump --defaults-extra-file=/tmp/.bk.cnf \
     --single-transaction --quick --routines --events '$DB'; rm -f /tmp/.bk.cnf" \
  < "$CNF" | gzip -9 > "$OUT"

# A dump with no content is an error that is easy to miss until the backup is needed.
SIZE=$(stat -c%s "$OUT")
[ "$SIZE" -gt 1024 ] || { echo "ERROR: the dump is only $SIZE B — not trustworthy"; rm -f "$OUT"; exit 1; }

find backups -name 'claude_usage_*.sql.gz' -mtime "+$KEEP" -delete
echo "OK  $OUT  ($(numfmt --to=iec "$SIZE" 2>/dev/null || echo "$SIZE B"))"
