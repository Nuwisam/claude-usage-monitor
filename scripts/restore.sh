#!/bin/sh
# Odtworzenie bazy ze zrzutu.  Uzycie:  scripts/restore.sh backups/claude_usage_YYYY….sql.gz
#
# DOMYSLNIE odtwarza do bazy SCRATCH, nie do produkcyjnej — bo backup, ktorego nigdy nie
# probowano odtworzyc, nie jest backupem, a odtwarzanie na produkcje "zeby sprawdzic"
# to najlepszy sposob, zeby ja stracic.
# Nadpisanie produkcji wymaga jawnego  TARGET=prod
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
SRC="$1"
[ -f "$SRC" ] || { echo "uzycie: $0 <plik.sql.gz>"; exit 1; }

DB=$(grep -oP '^MARIADB_DATABASE=\K.*' .env)
PW=$(grep -oP '^MARIADB_ROOT_PASSWORD=\K.*' .env)

if [ "$TARGET" = "prod" ]; then
  DEST="$DB"
  printf 'NADPISUJESZ BAZE PRODUKCYJNA "%s". Wpisz TAK aby kontynuowac: ' "$DEST"
  read a; [ "$a" = "TAK" ] || { echo "przerwano"; exit 1; }
else
  DEST="${DB}_scratch"
  echo "Odtwarzam do bazy scratch: $DEST  (TARGET=prod nadpisze produkcyjna)"
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
  "mariadb --defaults-extra-file=/tmp/.rs.cnf -N -e 'select concat(\"tabel: \", count(*)) from information_schema.tables where table_schema=\"$DEST\";'; rm -f /tmp/.rs.cnf"

echo "OK — odtworzono do $DEST"
