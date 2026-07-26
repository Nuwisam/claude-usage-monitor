# Runbook

Host: `192.0.2.10` (`usage.example.org`). Katalog: `/var/lib/claude-usage-monitor`
== `Z:/projects/claude-usage-monitor`. **Kopia robocza jest wdrożeniem.**

## Codzienna obsługa

```bash
cd /var/lib/claude-usage-monitor
docker compose ps
docker compose logs -f --tail 50 claude_usage_monitor_backend
git pull && docker compose up -d --build      # zwykły deploy
```

## Czy system żyje

```bash
# 1) kontenery
docker compose ps                       # oba healthy

# 2) czy dane przyrastają (uruchom dwa razy w odstępie kilku minut PRACY z Claude Code)
DB=$(grep -oP '^MARIADB_DATABASE=\K.*' .env); RP=$(grep -oP '^MARIADB_ROOT_PASSWORD=\K.*' .env)
docker exec claude_usage_monitor_mariadb mariadb -uroot -p"$RP" "$DB" -N -e \
 "select concat('batchy=',count(*),' probek=',(select count(*) from limit_samples),
   ' ostatni: ',timestampdiff(second,max(received_at),utc_timestamp()),' s temu')
  from ingest_batches;"

# 3) czy nie ma 429 od Anthropic
docker exec claude_usage_monitor_mariadb mariadb -uroot -p"$RP" "$DB" -N -e \
 "select http_status, count(*) from ingest_batches group by http_status;"
```

**Uwaga przy interpretacji:** dane przyrastają tylko wtedy, gdy **pracujesz** z Claude Code,
i tylko raz na 60 s (throttle). `PostToolUse` odpala się **po** zakończeniu narzędzia, więc
podczas jednego długiego wywołania nic nie przyjdzie — to poprawne zachowanie, nie awaria.

## Diagnostyka po stronie klienta

```bash
python client/analyze-samples.py        # tempo zmian, błędy, przełączenia konta
```

Log lokalny: `%LOCALAPPDATA%\claude-usage-monitor\usage-samples.jsonl`.
Jeśli log rośnie, a baza nie — problem jest w wysyłce; sprawdź `spool.jsonl` (rośnie przy
nieudanych POST-ach) i sekcję o certyfikatach w `client/README.md`.

## Typowe problemy

| Objaw | Przyczyna i co zrobić |
|---|---|
| `/api/status` daje **503** `sso-unreachable` | Backend nie dosięga oauth2-proxy. Sprawdź, czy jest w sieci `identity-proxy_default` i czy `identity_proxy` żyje |
| `/api/status` daje **403** `email-not-allowed` | `ALLOWED_EMAILS` w `.env` nie zawiera Twojego adresu SSO |
| Ingest daje **403**, a token jest dobry | Brak nagłówka `X-Ingest-Key` albo rozjazd z `INGEST_EDGE_KEY` w vhoście Apache |
| Ingest daje **401** | Zły token maszyny. Porównaj `config.json` klienta z `INGEST_TOKENS` |
| `samplesWritten: 0`, ale `ok: true` | **Poprawne.** Dedup — wartości się nie zmieniły i nie minął heartbeat (5 min) |
| Seria w stanie `unknown` w `/api/status` | Klient raportuje, ale brak próbek dla tej serii. Sprawdź `events` i log lokalny. **Nie ignoruj** — to jedyny stan oznaczający awarię |
| `docker compose up` → *„all predefined address pools have been fully subnetted"* | Docker wyczerpał pule (limit ~31 sieci). Usuń osieroconą sieć: `docker network ls`, sprawdź `docker network inspect <n> -f '{{len .Containers}}'` |
| Zdarzenia `clock_skew` | Zegar klienta rozjechany >5 min. Backend użył czasu serwera — dane są poprawne, ale warto zsynchronizować zegar |
| Zdarzenia `schema_drift` | Anthropic zmienił kształt odpowiedzi. Payload jest zapisany w całości; obejrzyj `GET /api/batches/{id}/raw` i zaktualizuj `app/parsing.py` + fixture |

## Backup

**Weekly `data-backup` NIE obejmuje tej bazy.** Jego `mysqldump --all-databases` łączy się
do natywnej MariaDB hosta (`127.0.0.1:3306`), a nasza działa w kontenerze. Żadna linia
`dir-backuper.py` nie dotyczy tego katalogu.

Do stracenia jest **historia zużycia** — nieodtwarzalna, bo Anthropic nie udostępnia przeszłości.
Stan bieżący odbuduje się przy pierwszym pomiarze.

```bash
./scripts/backup.sh                              # zrzut do backups/, retencja 14 dni
./scripts/restore.sh backups/claude_usage_*.sql.gz   # DOMYŚLNIE do bazy scratch
TARGET=prod ./scripts/restore.sh <plik>          # nadpisanie produkcji, pyta o potwierdzenie
```

`restore.sh` celowo odtwarza do bazy `*_scratch`. Backup, którego nigdy nie próbowano odtworzyć,
nie jest backupem — a odtwarzanie „na próbę" wprost na produkcję to najlepszy sposób, żeby ją
stracić.

Żeby włączyć automat, dopisz jedną linię do `/etc/cron.weekly/data-backup`:
```
/var/lib/claude-usage-monitor/scripts/backup.sh
```

## Zmiana klienta na maszynie

```powershell
Copy-Item Z:\projects\claude-usage-monitor\client\usage-probe.py `
          X:\Projekty\repozytorium skilli\tools\usage-probe.py
```

Źródłem prawdy jest repo na `Z:`, kopia wykonywana **musi** leżeć na dysku lokalnym.
Nie trzeba nic restartować — hook czyta skrypt i `config.json` przy każdym uruchomieniu.

## Wyłączenie zbierania

```powershell
# jedna maszyna: usuń albo zmień nazwę config.json  -> tryb tylko lokalny
Rename-Item "$env:LOCALAPPDATA\claude-usage-monitor\config.json" config.json.off
# albo całkiem: usuń bloki PostToolUse i Stop z ~/.claude/settings.json
```

Po stronie serwera unieważnienie pojedynczej maszyny to usunięcie jej wpisu z `INGEST_TOKENS`
w `.env` + `docker compose up -d`.

## Pierwsze wdrożenie od zera

```bash
cd /var/lib/claude-usage-monitor
cp .env.example .env            # MARIADB_*, INGEST_TOKENS, INGEST_EDGE_KEY, ALLOWED_EMAILS
docker compose up -d --build

EDGE=$(grep -oP '^INGEST_EDGE_KEY=\K.*' .env)
sed "s|__INGEST_EDGE_KEY__|$EDGE|" deploy/apache/claude-usage-monitor-include.conf.example \
    > /etc/apache2/sites-available/claude-usage-monitor-include.conf
chmod 600 /etc/apache2/sites-available/claude-usage-monitor-include.conf
# dopisz do sites-available/example_org-ssl.conf:
#   Include sites-available/claude-usage-monitor-include.conf
apachectl configtest && systemctl reload apache2
```

Weryfikacja: 401 z `redirect_url` na `/api/status`, 403 na `/api/ingest` bez `X-Ingest-Key`,
401 z kluczem brzegowym ale bez tokenu, 200 z kompletem.
