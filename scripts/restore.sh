#!/bin/sh
# Restores the database from a dump.  Usage:  scripts/restore.sh backups/claude_usage_YYYY….sql.gz
#
# BY DEFAULT it restores into the SCRATCH database, not the production one — because a backup
# that has never been restored is not a backup, and restoring onto production "just to check"
# is the best way to lose it.
# Overwriting production requires an explicit  TARGET=prod
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
SRC="$1"
[ -f "$SRC" ] || { echo "usage: $0 <file.sql.gz>"; exit 1; }

DB=$(grep -oP '^MARIADB_DATABASE=\K.*' .env)
PW=$(grep -oP '^MARIADB_ROOT_PASSWORD=\K.*' .env)

if [ "$TARGET" = "prod" ]; then
  DEST="$DB"
  printf 'YOU ARE OVERWRITING THE PRODUCTION DATABASE "%s". Type YES to continue: ' "$DEST"
  read a; [ "$a" = "YES" ] || { echo "aborted"; exit 1; }
else
  DEST="${DB}_scratch"
  echo "Restoring into the scratch database: $DEST  (TARGET=prod overwrites the production one)"
fi

CNF=$(mktemp); chmod 600 "$CNF"
printf '[client]\nuser=root\npassword=%s\n' "$PW" > "$CNF"
trap 'rm -f "$CNF"' EXIT INT TERM

docker exec -i claude_usage_monitor_mariadb sh -c \
  "cat > /tmp/.rs.cnf" < "$CNF"
docker exec claude_usage_monitor_mariadb sh -c \
  "mariadb --defaults-extra-file=/tmp/.rs.cnf -e 'DROP DATABASE IF EXISTS \`$DEST\`; CREATE DATABASE \`$DEST\`;'"
gunzip -c "$SRC" | docker exec -i claude_usage_monitor_mariadb sh -c \
  "mariadb --defaults-extra-file=/tmp/.rs.cnf '$DEST'"
docker exec claude_usage_monitor_mariadb sh -c \
  "mariadb --defaults-extra-file=/tmp/.rs.cnf -N -e 'select concat(\"tables: \", count(*)) from information_schema.tables where table_schema=\"$DEST\";'; rm -f /tmp/.rs.cnf"

echo "OK — restored into $DEST"
